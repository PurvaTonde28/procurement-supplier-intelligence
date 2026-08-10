"""Deterministic PII redaction — regex-based, not LLM-based, so it can't
itself be talked out of its job by adversarial input. Applied to outbound
negotiation drafts before a human ever reviews/approves them."""
import re

PII_PATTERNS = {
    "EMAIL": re.compile(r"[\w.\-]+@[\w.\-]+\.\w+"),
    "PHONE_IN": re.compile(r"(?:\+91[-\s]?)?\b[6-9]\d{9}\b"),
    "CREDIT_CARD": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "PAN_IN": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
    "IFSC_IN": re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b"),
}


def redact_pii(text: str) -> dict:
    """Returns {"redacted_text": str, "findings": [{"type", "count"}]}.
    Findings list is returned even when count is 0, so callers can log
    'checked, none found' distinctly from 'never checked'."""
    redacted = text
    findings = []

    for pii_type, pattern in PII_PATTERNS.items():
        matches = pattern.findall(redacted)
        if matches:
            findings.append({"type": pii_type, "count": len(matches)})
            redacted = pattern.sub(f"[REDACTED_{pii_type}]", redacted)

    return {"redacted_text": redacted, "findings": findings}