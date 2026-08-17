"""Defense-in-depth against invented terminology codes leaking into rationale text.

The synthesis prompt already instructs the model never to state a specific
code (LOINC/SNOMED/ICD-10/etc). Phase 2 proved prompt instructions alone are
not reliable (the model once named an uncited resource despite being told not
to) -- so this scans accepted rationale text for code-system keywords near
code-shaped tokens and redacts them, rather than trusting the prompt alone.
"""
from __future__ import annotations

import re

_CODE_SYSTEM_KEYWORDS = re.compile(
    r"\b(LOINC|SNOMED(?:\s*CT)?|ICD-?10|ICD-?9|CPT|RxNorm|NDC|UCUM)\b", re.IGNORECASE
)

# Only searched within a short window around a code-system keyword match, so
# these stay deliberately loose -- a bare 4-10 digit number is meaningless on
# its own, but is a real code shape right next to a keyword the model was
# told not to use at all.
_CODE_SHAPED_TOKENS = [
    re.compile(r"\b\d{1,5}-\d\b"),  # LOINC, e.g. 2345-7
    re.compile(r"\b[A-TV-Z]\d{2}(?:\.\d{1,4})?\b"),  # ICD-10, e.g. E11.9
    re.compile(r"\b\d{4,10}\b"),  # CPT / RxNorm / NDC / SNOMED-shaped numerics
]

_KEYWORD_WINDOW_CHARS = 40


def sanitize_rationale(text: str) -> tuple[str, bool]:
    """Redacts any code-shaped token found near a code-system keyword.

    Returns (possibly-modified text, whether anything was redacted).
    """
    redacted = False
    result = text

    for keyword_match in _CODE_SYSTEM_KEYWORDS.finditer(text):
        window_start = max(0, keyword_match.start() - _KEYWORD_WINDOW_CHARS)
        window_end = min(len(text), keyword_match.end() + _KEYWORD_WINDOW_CHARS)
        window = text[window_start:window_end]

        for pattern in _CODE_SHAPED_TOKENS:
            for code_match in pattern.finditer(window):
                code_text = code_match.group(0)
                if code_text in result:
                    result = result.replace(code_text, "[code redacted]")
                    redacted = True

    return result, redacted
