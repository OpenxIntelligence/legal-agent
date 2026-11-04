"""BM25 fallback ranking for corpus search (stdlib-only, offline).

Normalized-substring search is exact and stays primary: in
legal work an exact quote must always beat a fuzzy guess. This
module answers only when substring finds nothing, ranking
transcribed pages by BM25 over normalized term vectors.

Three pieces keep the fallback honest:

- Number words fold to digits ("twenty-one" -> "21") on both
  sides, so paraphrases citing numbers still meet the record.
- A near-miss guard excludes pages the query nearly-verbatim
  matches (longest-common-substring ratio >= 0.6 but not
  exact): a query differing from the record by one span
  ("defects: 22" vs "defects: 21") contradicts it and must
  stay silent rather than return the almost-match. Paraphrases
  share only scattered words and pass through.
- Only pages scoring within RATIO_CUT of the top page return,
  and nothing returns on an all-zero top score.

BM25 here is the standard Okapi form (k1=1.5, b=0.75,
idf = ln(1 + (N - df + 0.5) / (df + 0.5))). Implemented inline
rather than via rank-bm25: the formula is small, the
dependency would pull numpy, and the tokenization,
number-folding, and near-miss guard are custom either way.

Scaling note: ranking re-tokenizes the eligible pages per
query and the near-miss check is quadratic in text length.
Instant on case-sized corpora; a real deployment over large
collections needs a persistent index, not this scan.
"""

from __future__ import annotations

import math
import re
from difflib import SequenceMatcher

from . import textnorm

K1 = 1.5
B = 0.75
NEAR_MISS_RATIO = 0.6
RATIO_CUT = 0.7
MAX_COMPLETION_TERMS = 3

_TOKEN = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset(
    """
    a an the and or but is are was were be been being have has had
    do does did will would shall should can could may might must
    of to in on at by for with from as it its this that these
    those he she they we you i me him her them us my your his
    our their not no so if then than too very
    """.split()
)

_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_MULTIPLIERS = {"hundred": 100, "thousand": 1000, "million": 1_000_000}


def tokenize(text: str) -> list[str]:
    """Normalized content tokens: lowercase, alphanumerics, no stops."""
    return [
        token
        for token in _TOKEN.findall(textnorm.normalize(text or ""))
        if token not in STOPWORDS
    ]


def fold_numbers(tokens: list[str]) -> list[str]:
    """Fold English number words to digits ("twenty","one" -> "21").

    Greedy left-to-right composition over ones/teens, tens, and
    hundred/thousand/million multipliers. Unparseable runs pass
    through untouched; digit tokens canonicalize ("021" -> "21").
    """
    out: list[str] = []
    total = 0
    current = 0
    open_run = False

    def flush() -> None:
        nonlocal total, current, open_run
        if open_run:
            out.append(str(total + current))
            total = 0
            current = 0
            open_run = False

    for token in tokens:
        if token in _ONES:
            current += _ONES[token]
            open_run = True
        elif token in _TENS:
            current += _TENS[token]
            open_run = True
        elif token in _MULTIPLIERS:
            factor = _MULTIPLIERS[token]
            if not open_run:
                current = 1
            if factor == 100:
                current *= factor
            else:
                total += current * factor
                current = 0
            open_run = True
        elif token.isdigit():
            flush()
            out.append(str(int(token)))
        else:
            flush()
            out.append(token)
    flush()
    return out


def _is_consonant(word: str, index: int) -> bool:
    letter = word[index]
    if letter in "aeiou":
        return False
    if letter == "y":
        return index == 0 or not _is_consonant(word, index - 1)
    return True


def stem(token: str) -> str:
    """Porter-lite stem (no measure conditions; digits untouched).

    Fixed rule set, applied equally to queries and pages:
    1a plurals (sses→ss, ies→i, s→∅), 1b participles
    (eed→ee, ed/ing→∅ with double-consonant fixup),
    1c consonant+y→i, two step-2/4 suffixes
    (ment→∅, s/t+ion→∅), and final-e removal.
    Deliberately conservative: no measure or cvc
    conditions, length guards on every cut.
    """
    word = token or ""
    if not word or word.isdigit():
        return word
    if word.endswith("sses") and len(word) > 5:
        word = word[:-2]
    elif word.endswith("ies") and len(word) > 4:
        word = word[:-2]
    elif word.endswith("ss"):
        pass
    elif word.endswith("s") and len(word) > 3:
        word = word[:-1]
    if word.endswith("eed") and len(word) > 4:
        word = word[:-1]
    elif word.endswith("ed") and len(word) > 4:
        word = word[:-2]
        word = _fixup(word)
    elif word.endswith("ing") and len(word) > 5:
        word = word[:-3]
        word = _fixup(word)
    if (
        len(word) > 3
        and word.endswith("y")
        and _is_consonant(word, len(word) - 2)
    ):
        word = word[:-1] + "i"
    if word.endswith("ment") and len(word) > 6:
        word = word[:-4]
    elif (
        len(word) > 5
        and word.endswith("ion")
        and word[-4] in ("s", "t")
    ):
        word = word[:-3]
    if word.endswith("e") and len(word) > 4:
        word = word[:-1]
    return word


def _fixup(word: str) -> str:
    """Collapse a trailing double consonant (never l/s/z)."""
    if (
        len(word) > 3
        and word[-1] == word[-2]
        and word[-1] not in ("l", "s", "z")
        and _is_consonant(word, len(word) - 1)
    ):
        return word[:-1]
    return word


def query_terms(query: str) -> list[str]:
    """Tokenize + number-fold + stem a query (or page text)."""
    return [stem(token) for token in fold_numbers(tokenize(query))]


def idf(documents_with_term: int, document_count: int) -> float:
    """BM25 inverse document frequency, always non-negative."""
    return math.log(
        1.0
        + (document_count - documents_with_term + 0.5)
        / (documents_with_term + 0.5)
    )


def score(
    query: list[str],
    document: list[str],
    average_length: float,
    document_frequency: dict[str, int],
    document_count: int,
) -> float:
    """Okapi BM25 of one tokenized document for a tokenized query."""
    if not query or not document or average_length <= 0:
        return 0.0
    counts: dict[str, int] = {}
    for token in document:
        counts[token] = counts.get(token, 0) + 1
    length_norm = 1.0 - B + B * (len(document) / average_length)
    total = 0.0
    for token in set(query):
        term_count = counts.get(token, 0)
        if not term_count:
            continue
        total += idf(document_frequency.get(token, 0), document_count) * (
            term_count * (K1 + 1.0)
        ) / (term_count + K1 * length_norm)
    return total


def rank(
    query_tokens: list[str], documents: list[tuple[str, list[str]]]
) -> list[tuple[str, float]]:
    """Score (key, tokens) documents best-first. Empty query: all zero."""
    frequencies: dict[str, int] = {}
    for _, tokens in documents:
        for token in set(tokens):
            frequencies[token] = frequencies.get(token, 0) + 1
    count = len(documents)
    average = (
        sum(len(tokens) for _, tokens in documents) / count if count else 0.0
    )
    scored = [
        (key, score(query_tokens, tokens, average, frequencies, count))
        for key, tokens in documents
    ]
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored


def longest_match_ratio(needle: str, haystack: str) -> float:
    """Longest-common-substring length over normalized needle length."""
    clean_needle = textnorm.normalize(needle or "")
    if not clean_needle:
        return 0.0
    match = SequenceMatcher(
        None, clean_needle, textnorm.normalize(haystack or ""),
        autojunk=False,
    ).find_longest_match()
    return match.size / len(clean_needle)


def near_miss(needle: str, haystack: str) -> bool:
    """Whether the needle nearly-verbatim matches without matching.

    A ratio of 1.0 is an exact hit (the substring layer owns
    those); at or above NEAR_MISS_RATIO the query differs from
    the page by one span — a contradiction, not a paraphrase.
    """
    ratio = longest_match_ratio(needle, haystack)
    return NEAR_MISS_RATIO <= ratio < 1.0
