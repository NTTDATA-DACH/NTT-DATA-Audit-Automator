"""
German sentence splitting for BSI prose.

Ported verbatim from the Grundschutz-Plus-Plus-Tools repo (Gpp-ai-tool/src/utils/
sentence_split.py) so requirement sentence numbering is identical by construction
between the two projects. Stdlib-only.
"""

import re
from typing import List

# German abbreviations that end in a period but do not end a sentence. Kept conservative:
# only forms that actually occur in BSI prose and are unambiguous.
ABBREVIATIONS = (
    "z. B.", "z.B.", "d. h.", "d.h.", "u. a.", "u.a.", "u. U.", "o. Ä.", "o.Ä.",
    "i. d. R.", "bzw.", "ggf.", "etc.", "evtl.", "inkl.", "vgl.", "bspw.",
    "sog.", "ca.", "max.", "min.", "Nr.", "Abs.",
    # "(engl. Predictive Maintenance)" was split mid-sentence and produced fragment
    # pairs (OPS.1.1.1.A26, INF.13.A18); the corpus scan found no further gaps.
    "engl.",
)
# Sentence boundary: terminal punctuation, whitespace, then an uppercase/quote/paren opener.
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ„\"(])")


def split_sentences(text: str) -> List[str]:
    """Splits German prose into sentences without breaking at known abbreviations.

    Abbreviation periods are masked with a sentinel before splitting (multi-word forms
    like "z. B." would otherwise split internally) and restored afterwards.
    """
    normalized = " ".join((text or "").split())
    if not normalized:
        return []
    sentinel = "\x00"
    for abbr in sorted(ABBREVIATIONS, key=len, reverse=True):
        normalized = normalized.replace(abbr, abbr.replace(".", sentinel))
    pieces = SENTENCE_SPLIT.split(normalized)
    return [p.replace(sentinel, ".").strip() for p in pieces if p.strip()]
