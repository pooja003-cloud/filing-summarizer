"""Shared text normalization (DECISIONS.md, Decision 8).

summarize.py and check.py must clean the filing and the model's quotes with
this same function. If the two sides are cleaned differently, mismatches look
like model errors when they are really pipeline bugs.

Case is NOT normalized: changing capitalization changes a quote (Decision 9).
"""

import re

# Characters replaced with a plain space.
_SPACES = {
    " ": " ",  # non-breaking space
    " ": " ",  # figure space
    " ": " ",  # thin space
    " ": " ",  # narrow non-breaking space
}

# Characters removed entirely (invisible in rendered HTML).
_REMOVE = {
    "­": "",  # soft hyphen
    "​": "",  # zero-width space
    "‌": "",  # zero-width non-joiner
    "‍": "",  # zero-width joiner
    "﻿": "",  # byte-order mark / zero-width no-break space
}

# Typographic punctuation mapped to plain ASCII.
_PUNCT = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",  # single quotes
    "“": '"', "”": '"', "„": '"', "‟": '"',  # double quotes
    "‐": "-", "‑": "-",  # hyphen, non-breaking hyphen
    "‒": "-", "–": "-", "—": "-", "―": "-",  # figure/en/em dash, bar
}

_TABLE = str.maketrans({**_SPACES, **_REMOVE, **_PUNCT})
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Return text with invisible characters removed, typographic quotes and
    dashes made plain, and every run of whitespace collapsed to one space."""
    return _WS.sub(" ", text.translate(_TABLE)).strip()
