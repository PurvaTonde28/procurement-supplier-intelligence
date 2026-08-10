"""Prompt-injection pattern detection for untrusted ingested documents
(contracts, invoices) before they're fed to any LLM in the extraction
or RAG pipeline. Contract/invoice PDFs are effectively user-uploaded
content from an untrusted third party (the supplier) — treated
accordingly, not assumed benign."""
import re

INJECTION_PATTERNS = [
    r"ignore (all |the )?(previous|prior|above) instructions",
    r"disregard (all |the )?(previous|prior|above)",
    r"you are now",
    r"system\s*prompt",
    r"new instructions?\s*:",
    r"act as (a|an)\s",
    r"reveal (your|the) (system )?(prompt|instructions)",
    r"forget (everything|all) (you|that)",
    r"</?system>",
    r"\[system\]",
    r"override.*instructions",
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]


def scan_for_injection(text: str) -> dict:
    """Returns {"is_suspicious": bool, "matched_patterns": [str]}."""
    matched = []
    for pattern in _COMPILED:
        if pattern.search(text):
            matched.append(pattern.pattern)
    return {"is_suspicious": len(matched) > 0, "matched_patterns": matched}