from __future__ import annotations

import re
import unicodedata

_SENTENCE_PUNCTUATION = re.compile(r"[?.!;:]")

NUMBER_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
}

_NUMBER_WORD_RE = re.compile(r"\b(" + "|".join(NUMBER_WORDS) + r")\b", re.IGNORECASE)


def replace_number_words(text: str) -> str:
    """Replace whole number words zero-ten with their digits; "tone" survives intact."""
    return _NUMBER_WORD_RE.sub(lambda match: NUMBER_WORDS[match.group(0).lower()], text)


def normalize(text: str) -> str:
    """Strip sentence punctuation, collapse whitespace, map number words to digits."""
    return replace_number_words(" ".join(_SENTENCE_PUNCTUATION.sub("", text).split()))


ASCII_EQUIVALENTS = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "ʼ": "'", "´": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "«": '"', "»": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-",
    "−": "-", "⁃": "-",
    "…": "...", "•": "*", "·": ".", "⁄": "/", "∕": "/",
    "‹": "<", "›": ">",
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ", " ": "\n",
    "​": "", "‌": "", "‍": "", "﻿": "",
}  # fmt: skip

_ASCII_TABLE = str.maketrans(ASCII_EQUIVALENTS)


def to_ascii(text: str) -> str:
    """Rewrite text into characters a US keyboard can produce, as far as it goes."""
    substituted = text.translate(_ASCII_TABLE)
    decomposed = unicodedata.normalize("NFKD", substituted)
    return "".join(c for c in decomposed if not unicodedata.combining(c))
