"""Regex-based PHI redaction for authenticated users' text, applied both
before it's sent to the LLM and before it's persisted (per explicit choice --
no LLM-based redaction pass).

Known, accepted limitation of a regex-only approach: a bare, unlabeled name
in free prose ("John Smith was admitted...") will NOT be caught -- there is
no reliable pattern for "this token is a person's name" without an NLP
model. What this DOES catch:
  - structured identifiers regardless of context: SSNs, emails, phone
    numbers, ISO/US-style dates (a superset that also catches non-DOB dates
    -- accepted as a safety-over-utility tradeoff)
  - labeled fields, which cover both prose ("Patient Name: John Smith") and
    many real-world tabular/HL7-style exports ("MRN: 4471182")

Same shape/style as backend/code_guard.py's sanitize_rationale: a keyword
(or direct pattern) triggers redaction of the value near it, returning
(text, was_redacted) rather than raising.
"""
from __future__ import annotations

import re

_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}\b")
_PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b")

_DIRECT_PATTERNS = [
    (_SSN_RE, "[REDACTED-SSN]"),
    (_EMAIL_RE, "[REDACTED-EMAIL]"),
    (_PHONE_RE, "[REDACTED-PHONE]"),
    (_DATE_RE, "[REDACTED-DATE]"),
]

# Deliberately excludes bare "Name" -- this app's own domain has legitimate
# headers like "TestName"/"FieldName" that would otherwise be false positives.
_LABELED_FIELD_RE = re.compile(
    r"\b(SSN|Social\s*Security(?:\s*Number)?|DOB|Date\s*of\s*Birth|MRN|"
    r"Patient\s*Name|Address|Phone(?:\s*Number)?|Email)\b\s*[:#]?\s*([^\n,|;]+)",
    re.IGNORECASE,
)


def redact_phi(text: str) -> tuple[str, bool]:
    """Returns (possibly-redacted text, whether anything was redacted).

    Labeled-field redaction runs first (on the original text), direct
    patterns run second as cleanup for anything left unlabeled -- in that
    order specifically, because a direct-pattern tag like "[REDACTED-SSN]"
    contains "SSN" as a standalone word, which the labeled-field pass would
    otherwise match a second time inside its own output if it ran last.
    """
    if not text:
        return text, False

    redacted = False
    result = text

    def _label_sub(match: re.Match[str]) -> str:
        nonlocal redacted
        redacted = True
        return f"{match.group(1)}: [REDACTED]"

    result = _LABELED_FIELD_RE.sub(_label_sub, result)

    for pattern, replacement in _DIRECT_PATTERNS:
        result, count = pattern.subn(replacement, result)
        if count:
            redacted = True

    return result, redacted
