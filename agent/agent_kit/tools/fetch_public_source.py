"""Retrieve an official text source as a local, citable evidence snapshot."""
from .. import public_sources
DEFINITION = {
    "type": "function", "name": "fetch_public_source",
    "description": "Retrieve a specific public primary-source HTTPS page into the inbox with URL, retrieval time and content hash. Supported exact hosts: uscode.house.gov, www.federalregister.gov, www.ecfr.gov, www.govinfo.gov, www.uscis.gov, www.supremecourt.gov, legislation.gov.uk, www.legislation.gov.uk, www.judiciary.uk, leginfo.legislature.ca.gov. HTML/plain text/XML only, 1MB, 12 requests per run. For a known statute use the official section URL. Analyze/scan the returned document_id and record exact quotes before relying on it; retrieved does not mean current, applicable or legally verified. If blocked, try a different official text endpoint then disclose the gap. Never submit credentials or private facts in source URLs.",
    "parameters": {"type": "object", "additionalProperties": False, "properties": {"url": {"type": "string"}}, "required": ["url"]},
}
def run(ctx, args):
    return public_sources.fetch(ctx.case, ctx.root, args.get("url", ""))
