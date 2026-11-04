"""Shared text-normalization contract (closeout §5).

Stdlib-only, zero first-party imports: safe for import-free
modules (e.g. the reasoning layer) that cannot depend on
`document_text` (which pulls the model client, usage, and
settings). `document_text.normalize` delegates here, so the
two cannot drift: they are the same function object.

Contract v1: fold curly quotes/dashes and NBSP, collapse
whitespace, strip, lowercase. Joins built on this must still
coerce page representations and pin source versions
separately — normalization covers text only.
"""

from __future__ import annotations

import re

NORMALIZER_VERSION = "textnorm/v1"

_SPACE = re.compile(r"\s+")
_QUOTES = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u00a0": " ",
    }
)


def normalize(text: str) -> str:
    """Canonical quote/page-text normalization (see module docstring)."""
    return _SPACE.sub(" ", (text or "").translate(_QUOTES)).strip().lower()
