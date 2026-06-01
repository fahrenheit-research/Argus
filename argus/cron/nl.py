"""Natural-language → cron expression translator.

Accepts phrases like "every weekday at 9 am", "every 30 minutes", "tomorrow
at 3pm", etc. Returns either:
    (cron_expr, confidence, explanation)   on confident parse
    (None,      0.0,        question)      when ambiguous → caller asks user

Uses pure regex + a small set of patterns so we DON'T spin up an LLM call
for every cron add. Fast, deterministic, debuggable.

If the input already looks like a 5-field cron expression, we validate
and pass it through.
"""
from __future__ import annotations

import re
from typing import Optional

# Pre-baked patterns matched in order. First match wins.
_DAY_NAMES = {
    "monday": "1", "mon": "1", "tuesday": "2", "tue": "2", "tues": "2",
    "wednesday": "3", "wed": "3", "thursday": "4", "thu": "4", "thurs": "4",
    "friday": "5", "fri": "5", "saturday": "6", "sat": "6", "sunday": "0",
    "sun": "0",
}


def _parse_time(s: str) -> Optional[tuple[int, int]]:
    """Parse "9am", "9:30 am", "14:00", "3pm", etc → (hour24, minute)."""
    s = s.strip().lower().replace(".", "")
    # 24h
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        if 0 <= h < 24 and 0 <= mn < 60:
            return h, mn
    # 12h with optional minutes + am/pm
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)$", s)
    if m:
        h, mn, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if h == 12: h = 0 if ampm == "am" else 12
        elif ampm == "pm": h += 12
        if 0 <= h < 24 and 0 <= mn < 60:
            return h, mn
    # Bare hour 9 / 14
    m = re.match(r"^(\d{1,2})$", s)
    if m:
        h = int(m.group(1))
        if 0 <= h < 24:
            return h, 0
    return None


def parse(text: str) -> tuple[Optional[str], float, str]:
    """Translate `text` to a cron expression.

    Returns: (cron_expr | None, confidence 0..1, message)
        - On confident parse:  (expr, 0.95, "every weekday at 09:00")
        - On ambiguous input:  (None, 0.0,  "what time of day?")
        - On already-cron:     (expr, 1.0,  "")
    """
    raw = text.strip().lower()
    if not raw:
        return None, 0.0, "what schedule? (e.g. 'every weekday at 9am')"

    # ── Case 1: already a 5-field cron expression ──────────────────────────
    if re.match(r"^[\d\*/,\-\? ]+$", raw) and len(raw.split()) == 5:
        # Validate via croniter (which APScheduler also uses)
        try:
            from croniter import croniter   # type: ignore
            from datetime import datetime
            croniter(raw, datetime.now())
            return raw, 1.0, ""
        except Exception as e:
            return None, 0.0, f"invalid cron expression: {e}"

    # ── Case 2: "every N minutes/hours" ────────────────────────────────────
    m = re.search(r"every\s+(\d+)\s*(min|minute|minutes|hour|hours|hr|hrs)", raw)
    if m:
        n      = int(m.group(1))
        unit   = m.group(2)
        if unit.startswith("min"):
            if n < 1 or n > 59:
                return None, 0.0, f"interval must be 1-59 minutes, got {n}"
            return f"*/{n} * * * *", 0.95, f"every {n} minute(s)"
        else:  # hours
            if n < 1 or n > 23:
                return None, 0.0, f"interval must be 1-23 hours, got {n}"
            return f"0 */{n} * * *", 0.95, f"every {n} hour(s) (on the hour)"

    # ── Case 3: "every weekday/weekend/day [at TIME]" ──────────────────────
    weekday_match = re.search(r"every\s+(weekday|workday|business day)", raw)
    weekend_match = re.search(r"every\s+(weekend|saturday and sunday)", raw)
    daily_match   = re.search(r"\b(every day|daily|each day)\b", raw)

    # Specific day names
    day_match = None
    for name, dow in _DAY_NAMES.items():
        if re.search(rf"\bevery\s+{name}\b", raw):
            day_match = dow
            break

    # Extract time, if any
    t_match = re.search(
        r"\bat\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?|\d{1,2}:\d{2})\b", raw)
    time_text = t_match.group(1).strip() if t_match else None
    time_val  = _parse_time(time_text) if time_text else None

    has_weekday = weekday_match is not None
    has_weekend = weekend_match is not None
    has_daily   = daily_match   is not None

    # If we have a day-class but no time, ask
    if (has_weekday or has_weekend or has_daily or day_match) and not time_val:
        return None, 0.0, "what time of day? (e.g. '9am', '14:30', '6 pm')"

    if has_weekday and time_val:
        h, mn = time_val
        return f"{mn} {h} * * 1-5", 0.95, f"every weekday at {h:02d}:{mn:02d}"
    if has_weekend and time_val:
        h, mn = time_val
        return f"{mn} {h} * * 0,6", 0.95, f"every weekend at {h:02d}:{mn:02d}"
    if has_daily and time_val:
        h, mn = time_val
        return f"{mn} {h} * * *", 0.95, f"every day at {h:02d}:{mn:02d}"
    if day_match and time_val:
        h, mn = time_val
        dow_name = next(k for k, v in _DAY_NAMES.items() if v == day_match and len(k) > 3)
        return f"{mn} {h} * * {day_match}", 0.95, f"every {dow_name} at {h:02d}:{mn:02d}"

    # ── Case 4: just a time, no day spec → assume "every day at <time>" ────
    if t_match and not (has_weekday or has_weekend or has_daily or day_match):
        h, mn = time_val if time_val else (0, 0)
        return f"{mn} {h} * * *", 0.80, f"every day at {h:02d}:{mn:02d}"

    # ── Case 5: "hourly" / "minute" shorthands ─────────────────────────────
    if re.search(r"\bhourly\b", raw):
        return "0 * * * *", 0.95, "every hour on the hour"
    if re.search(r"\bevery minute\b", raw):
        return "* * * * *", 0.95, "every minute (use sparingly!)"

    # ── Nothing matched ─────────────────────────────────────────────────────
    return None, 0.0, (
        "I don't recognise that schedule. Try:\n"
        "  • 'every weekday at 9am'\n"
        "  • 'every 30 minutes'\n"
        "  • 'every monday at 14:00'\n"
        "  • 'daily at 7pm'\n"
        "  • or pass a raw cron expression like '0 9 * * 1-5'"
    )


def local_tz_name() -> str:
    """Return the local IANA timezone name (e.g. 'America/Los_Angeles')."""
    try:
        from tzlocal import get_localzone   # type: ignore
        return str(get_localzone())
    except Exception:
        from datetime import datetime
        return datetime.now().astimezone().tzname() or "UTC"
