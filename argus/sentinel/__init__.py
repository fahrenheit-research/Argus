"""ARGUS Sentinel — input/output guards.

Public API:
    scan_input(text)   -> ScanResult   (prompt-injection + secrets, async)
    scan_output(text)  -> ScanResult   (PII detection + redaction, async)
    scrub_pii(text)    -> str          (sync convenience; redaction only)
    ScanResult         dataclass with is_safe/threats/cleaned_text/reasoning

This package is INFRASTRUCTURE — not a tool the LLM calls. Hook it around
every provider transport call so user input is scanned before reaching the
model and model output is scanned before reaching the user or disk.
"""

from argus.sentinel.guard import (
    ScanResult,
    scan_input,
    scan_output,
    scrub_pii,
)

__all__ = ["ScanResult", "scan_input", "scan_output", "scrub_pii"]
