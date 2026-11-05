"""Offline bounded public retrieval and exact evidence snapshot coverage."""
import hashlib
import json
import unittest
from unittest.mock import patch, Mock
from email.message import Message
from agent_kit import public_sources as ps, document_text, tools
from agent_kit.case_record import CaseRecord
from agent_kit.progress import new_run
from tests import support

URL = 'https://uscode.house.gov/section'
HTML = ('<html><title>Official section</title><script>ignore this</script><p>'
        'The government shall review the designation before expiration. '
        'A failure to act has a specified statutory consequence. Exact Unicode: § “notice”.'
        '</p><a href="/next#section">Next section</a>'
        '<a href="https://evil.example/">external</a></html>').encode()

class RetrievalTest(unittest.TestCase):
    def setUp(self):
        self.root, temp = support.make_root()
        self.addCleanup(temp.cleanup)
        self.run_id = new_run(self.root, instructions='i', objective='o', verification=True)
        self.case = CaseRecord(self.root, self.run_id)

    def test_fetch_persists_exact_deterministic_citable_snapshot(self):
        with patch.object(ps, 'download', return_value=(HTML, 'text/html', URL)):
            result = ps.fetch(self.case, self.root, URL)
        doc = result['document_id']
        text = document_text.page_text(self.root, doc, 1)
        self.assertIn('§ “notice”', text)
        self.assertNotIn('ignore this', text)
        self.assertEqual(result['links'], ['https://uscode.house.gov/next'])
        self.assertEqual(result['content_sha256'], hashlib.sha256(HTML).hexdigest())
        self.assertEqual(result['sha256'], document_text.get_document(self.root, doc)['sha256'])
        self.assertEqual((self.root / 'workspace/public_sources' / (doc + '.raw')).read_bytes(), HTML)
        fresh = CaseRecord(self.root, self.run_id)
        self.assertEqual(fresh.data['sources'][doc]['url'], URL)
        fresh.note_source(doc, 'uscode', 'synthetic statute section', 'synthetic edition')
        self.assertEqual(fresh.data['sources'][doc]['content_sha256'], result['content_sha256'])
        self.assertEqual(fresh.data['sources'][doc]['url'], URL)
        self.assertEqual(fresh.data['public_sources'][doc]['asserted_by'], 'public_source_fetch')

    def test_failures_consume_hard_budget_and_cannot_loop_forever(self):
        with patch.object(ps, 'download', side_effect=ps.PublicSourceError('blocked')) as download:
            for _ in range(ps.MAX_FETCHES):
                with self.assertRaises(ps.PublicSourceError): ps.fetch(self.case, self.root, URL)
            with self.assertRaisesRegex(ps.PublicSourceError, 'budget exhausted'):
                ps.fetch(self.case, self.root, URL)
        self.assertEqual(download.call_count, ps.MAX_FETCHES)
        self.assertEqual(CaseRecord(self.root, self.run_id).data['public_research_requests']['fetch'], ps.MAX_FETCHES)

    def test_search_is_discovery_only_and_tries_allowed_url_fallback(self):
        row = {'title': 'Synthetic notice', 'body_html_url': 'https://unapproved.example/body',
               'html_url': 'https://www.federalregister.gov/documents/example',
               'publication_date': '2026-01-01', 'document_number': 'test'}
        with patch.object(ps, 'download', return_value=(json.dumps({'results': [row]}).encode(), 'application/json', URL)):
            result = ps.search(self.case, 'designation notice')
        self.assertEqual(result['results'][0]['url'], row['html_url'])
        self.assertIn('only', result['scope'])
        self.assertFalse(self.case.data.get('sources'))
        self.assertEqual(len(document_text.list_documents(self.root)), len(support.INITIAL))

    def test_registered_tools_are_available_and_classified(self):
        from agent_kit.reasoning import TOOL_ACTION_CLASSES
        for name in ('search_public_sources', 'fetch_public_source'):
            self.assertIn(name, tools.BASE_TOOLS)
            self.assertIn(name, tools.MODULES)
            self.assertEqual(TOOL_ACTION_CLASSES[name], 'research')

class NetworkBoundaryTest(unittest.TestCase):
    def test_rejects_unsafe_urls_before_network(self):
        for url in ('http://uscode.house.gov/', 'https://localhost/', 'https://127.0.0.1/',
                    'https://uscode.house.gov.evil.example/', 'https://user:secret@uscode.house.gov/',
                    'https://uscode.house.gov:444/', 'https://uscode.house.gov/#fragment',
                    'https://uscode.house.gov/\r\nsecret'):
            with self.subTest(url=url), self.assertRaises(ps.PublicSourceError):
                ps.validate_url(url, resolve=False)

    def test_private_dns_and_redirect_destinations_are_rejected(self):
        with patch.object(ps.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 443))]):
            with self.assertRaisesRegex(ps.PublicSourceError, 'public addresses'):
                ps.validate_url(URL)
        with self.assertRaises(ps.PublicSourceError):
            ps._Redirects().redirect_request(Mock(full_url=URL), None, 302, '', {}, 'https://evil.example/')

    def response(self, data, content_type='text/html; charset=utf-8'):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.geturl.return_value = URL
        response.read.return_value = data
        response.headers = Message()
        response.headers['Content-Type'] = content_type
        return response

    def test_download_has_no_ambient_auth_and_is_size_and_encoding_bounded(self):
        opener = Mock()
        opener.open.return_value = self.response(HTML)
        with patch.object(ps, 'validate_url', side_effect=lambda url, **kw: url), patch.object(ps.urllib.request, 'build_opener', return_value=opener) as build:
            self.assertEqual(ps.download(URL)[0], HTML)
            self.assertEqual(build.call_args.args[0].proxies, {})
            request = opener.open.call_args.args[0]
            self.assertEqual(set(k.lower() for k in request.headers), {'user-agent', 'accept'})
            self.assertEqual(opener.open.call_args.kwargs['timeout'], ps.TIMEOUT)
            opener.open.return_value.read.assert_called_once_with(ps.MAX_BYTES + 1)
            opener.open.return_value = self.response(b'x' * (ps.MAX_BYTES + 1))
            with self.assertRaisesRegex(ps.PublicSourceError, '1 MB'): ps.download(URL)
            opener.open.return_value = self.response(b'\xa7', 'text/html; charset=iso-8859-1')
            with self.assertRaisesRegex(ps.PublicSourceError, 'encoding'): ps.download(URL)

    def test_rejects_binary_invalid_utf8_and_oversized_text(self):
        for data, kind in ((HTML, 'application/pdf'), (b'\xff' * 100, 'text/plain'),
                           (b'x' * (ps.MAX_TEXT + 1), 'text/plain')):
            with self.subTest(kind=kind, length=len(data)), self.assertRaises(ps.PublicSourceError):
                ps.extract(data, kind)

if __name__ == '__main__': unittest.main()
