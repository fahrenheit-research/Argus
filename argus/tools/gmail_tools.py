"""Native Gmail tools — uses the OAuth tokens stored by GmailConnector.

When the user says "send an email to alice@example.com about the proposal"
the agent now calls `gmail_send` directly. No more dead-ends where the
connector finishes auth but nothing actually works.

Tools registered:
  • gmail_send          — send an email
  • gmail_search        — Gmail-query syntax (from:, subject:, has:attachment, …)
  • gmail_list_inbox    — recent inbox messages
  • gmail_read_message  — full body of one message by ID
  • gmail_create_draft  — draft instead of send

All tools share a common token-refresh helper that swaps an expired
access_token for a fresh one using the stored refresh_token. Refreshed
tokens are written back to disk so the next call doesn't repeat the dance.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Any

import httpx

from argus.providers.base import ToolSpec
from argus.tools.registry import ToolImpl, register


_GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
_TOKEN_URL = "https://oauth2.googleapis.com/token"


# ── Token plumbing ──────────────────────────────────────────────────────────


def _load_tokens_and_creds() -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return (tokens, oauth_creds) or None if Gmail isn't connected."""
    from argus.connectors.gmail import GmailConnector
    g = GmailConnector()
    tokens = g.load_tokens()
    creds  = g.load_credentials()
    if not tokens.get("access_token") or not creds.get("client_id"):
        return None
    return tokens, creds


async def _ensure_fresh_token(tokens: dict[str, Any], creds: dict[str, Any]) -> str:
    """Return a valid access_token. Refreshes via refresh_token if needed.

    Strategy: don't pre-check expiry (clocks drift); just call once with the
    current token and refresh on 401. The wrapper functions below catch 401
    and re-call this with `force=True`.
    """
    return tokens.get("access_token", "")


async def _refresh_token(tokens: dict[str, Any], creds: dict[str, Any]) -> str | None:
    """Exchange refresh_token → new access_token. Persist on success."""
    refresh = tokens.get("refresh_token")
    if not refresh:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(_TOKEN_URL, data={
                "client_id":     creds["client_id"],
                "client_secret": creds["client_secret"],
                "refresh_token": refresh,
                "grant_type":    "refresh_token",
            })
        data = r.json()
        new_access = data.get("access_token")
        if not new_access:
            return None
        # Persist the refreshed token + any new expiry
        tokens["access_token"] = new_access
        if data.get("expires_in"):
            tokens["expires_in"] = data["expires_in"]
        from argus.connectors.gmail import GmailConnector
        GmailConnector().save_tokens(tokens)
        return new_access
    except Exception:
        return None


async def _gmail_call(method: str, path: str, *,
                     params: dict | None = None,
                     json_body: dict | None = None) -> dict[str, Any]:
    """Call the Gmail API, handling 401-refresh once. Returns parsed JSON."""
    pack = _load_tokens_and_creds()
    if not pack:
        return {"_error": "GMAIL_NOT_CONNECTED",
                "_remedy": "Run `argus connect gmail` (or say 'connect gmail' in chat) and finish OAuth first."}
    tokens, creds = pack

    async def _do(access: str) -> httpx.Response:
        async with httpx.AsyncClient(timeout=20) as c:
            return await c.request(
                method,
                f"{_GMAIL_API}{path}",
                headers={"Authorization": f"Bearer {access}",
                         "Accept":        "application/json"},
                params=params, json=json_body,
            )

    access = tokens.get("access_token", "")
    r = await _do(access)
    if r.status_code == 401:
        new_access = await _refresh_token(tokens, creds)
        if not new_access:
            return {"_error": "GMAIL_AUTH_EXPIRED",
                    "_remedy": "Run `argus connect gmail` to re-authorise."}
        r = await _do(new_access)

    if r.status_code >= 400:
        return {"_error": f"GMAIL_HTTP_{r.status_code}",
                "_detail": r.text[:400]}
    try:
        return r.json() if r.text else {}
    except json.JSONDecodeError:
        return {"_error": "GMAIL_BAD_JSON", "_detail": r.text[:400]}


# ── Tool: gmail_send ─────────────────────────────────────────────────────────


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


async def _gmail_send(args: dict[str, Any]) -> str:
    to_raw  = (args.get("to") or "").strip()
    subject = (args.get("subject") or "").strip()
    body    = (args.get("body") or "").strip()
    cc_raw  = (args.get("cc") or "").strip()
    bcc_raw = (args.get("bcc") or "").strip()
    html    = bool(args.get("html", False))

    if not to_raw:
        return "ERROR: 'to' is required"
    if not subject:
        return "ERROR: 'subject' is required"
    if not body:
        return "ERROR: 'body' is required"

    # Allow comma-separated recipients
    to_list  = [a.strip() for a in to_raw.split(",")  if a.strip()]
    cc_list  = [a.strip() for a in cc_raw.split(",")  if a.strip()] if cc_raw else []
    bcc_list = [a.strip() for a in bcc_raw.split(",") if a.strip()] if bcc_raw else []

    bad = [a for a in to_list + cc_list + bcc_list if not _EMAIL_RE.match(a)]
    if bad:
        return f"ERROR: invalid email address(es): {', '.join(bad)}"

    # Build RFC 2822 message
    msg = MIMEText(body, "html" if html else "plain", "utf-8")
    msg["To"]      = ", ".join(to_list)
    msg["Subject"] = subject
    if cc_list:  msg["Cc"]  = ", ".join(cc_list)
    if bcc_list: msg["Bcc"] = ", ".join(bcc_list)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    resp = await _gmail_call("POST", "/messages/send", json_body={"raw": raw})
    if "_error" in resp:
        return f"ERROR: {resp['_error']}\n{resp.get('_remedy') or resp.get('_detail', '')}"
    cc_part  = f" (cc {', '.join(cc_list)})"  if cc_list  else ""
    bcc_part = f" (bcc {', '.join(bcc_list)})" if bcc_list else ""
    return (
        f"OK: sent to {', '.join(to_list)}{cc_part}{bcc_part}"
        f"\nGmail message id: {resp.get('id', '?')}"
    )


# ── Tool: gmail_create_draft ─────────────────────────────────────────────────


async def _gmail_draft(args: dict[str, Any]) -> str:
    to_raw  = (args.get("to") or "").strip()
    subject = (args.get("subject") or "").strip()
    body    = (args.get("body") or "").strip()
    if not (to_raw and subject and body):
        return "ERROR: 'to', 'subject', and 'body' are all required"

    msg = MIMEText(body, "plain", "utf-8")
    msg["To"]      = to_raw
    msg["Subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    resp = await _gmail_call("POST", "/drafts",
                             json_body={"message": {"raw": raw}})
    if "_error" in resp:
        return f"ERROR: {resp['_error']}\n{resp.get('_remedy') or resp.get('_detail', '')}"
    return f"OK: draft created (id {resp.get('id', '?')}). View it in Gmail to send."


# ── Tool: gmail_search ──────────────────────────────────────────────────────


async def _gmail_search(args: dict[str, Any]) -> str:
    query = (args.get("query") or "").strip()
    limit = max(1, min(int(args.get("limit", 10)), 50))
    if not query:
        return "ERROR: 'query' is required (Gmail query syntax: from:..., subject:..., has:attachment, etc.)"

    list_resp = await _gmail_call("GET", "/messages",
                                  params={"q": query, "maxResults": limit})
    if "_error" in list_resp:
        return f"ERROR: {list_resp['_error']}\n{list_resp.get('_remedy') or list_resp.get('_detail', '')}"
    messages = list_resp.get("messages", [])
    if not messages:
        return f"(no matches for query: {query})"

    # Hydrate each with sender / subject / snippet (parallel)
    async def _fetch(msg_id: str) -> dict:
        return await _gmail_call("GET", f"/messages/{msg_id}",
                                 params={"format": "metadata",
                                         "metadataHeaders": "From,Subject,Date"})
    hydrated = await asyncio.gather(*(_fetch(m["id"]) for m in messages))

    lines = [f"Found {len(hydrated)} message(s) for '{query}':\n"]
    for m in hydrated:
        if "_error" in m: continue
        hdrs = {h["name"]: h["value"]
                for h in m.get("payload", {}).get("headers", [])}
        snippet = m.get("snippet", "")[:120]
        lines.append(
            f"• [{m.get('id', '?')[:12]}] {hdrs.get('Date', '?')[:25]} | "
            f"{hdrs.get('From', '?')[:40]}\n"
            f"    {hdrs.get('Subject', '(no subject)')[:70]}\n"
            f"    {snippet}\n"
        )
    return "\n".join(lines)


# ── Tool: gmail_list_inbox ──────────────────────────────────────────────────


async def _gmail_list_inbox(args: dict[str, Any]) -> str:
    limit = max(1, min(int(args.get("limit", 10)), 50))
    args2 = dict(args)
    args2["query"] = "in:inbox"
    args2["limit"] = limit
    return await _gmail_search(args2)


# ── Tool: gmail_read_message ────────────────────────────────────────────────


async def _gmail_read(args: dict[str, Any]) -> str:
    message_id = (args.get("message_id") or "").strip()
    if not message_id:
        return "ERROR: 'message_id' is required (get it from gmail_search or gmail_list_inbox)"

    resp = await _gmail_call("GET", f"/messages/{message_id}",
                             params={"format": "full"})
    if "_error" in resp:
        return f"ERROR: {resp['_error']}\n{resp.get('_remedy') or resp.get('_detail', '')}"

    payload = resp.get("payload", {})
    headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
    body_text = _extract_body(payload)

    out = [
        f"From:    {headers.get('From', '?')}",
        f"To:      {headers.get('To', '?')}",
        f"Subject: {headers.get('Subject', '(no subject)')}",
        f"Date:    {headers.get('Date', '?')}",
        "",
        body_text[:4000] if body_text else "(empty body)",
    ]
    return "\n".join(out)


def _extract_body(payload: dict) -> str:
    """Walk the MIME tree, return the first text/plain body found (else text/html stripped)."""
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            try: return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
            except Exception: pass
    for part in payload.get("parts", []) or []:
        body = _extract_body(part)
        if body: return body
    # Fallback: try text/html and strip tags crudely
    if payload.get("mimeType") == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            try:
                html = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
                return re.sub(r"<[^>]+>", " ", html)
            except Exception: pass
    return ""


# ── Registration ────────────────────────────────────────────────────────────


def register_gmail_tools() -> None:
    """Register all Gmail action tools under the 'mail' toolset."""

    register(ToolImpl("mail", ToolSpec(
        name="gmail_send",
        description=(
            "Send an email via the user's connected Gmail account. "
            "Requires the user to have completed Gmail OAuth (run "
            "`argus connect gmail` if not). Supports plain text or HTML. "
            "Multiple recipients via comma-separated 'to' / 'cc' / 'bcc'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "to":      {"type": "string", "description": "Recipient(s), comma-separated"},
                "subject": {"type": "string"},
                "body":    {"type": "string", "description": "Email body"},
                "cc":      {"type": "string", "description": "Optional CC (comma-separated)"},
                "bcc":     {"type": "string", "description": "Optional BCC (comma-separated)"},
                "html":    {"type": "boolean", "description": "Treat body as HTML. Default false."},
            },
            "required": ["to", "subject", "body"],
        },
    ), _gmail_send))

    register(ToolImpl("mail", ToolSpec(
        name="gmail_create_draft",
        description="Create a Gmail draft (does NOT send). Use when the user wants to review before sending.",
        parameters={
            "type": "object",
            "properties": {
                "to":      {"type": "string"},
                "subject": {"type": "string"},
                "body":    {"type": "string"},
            },
            "required": ["to", "subject", "body"],
        },
    ), _gmail_draft))

    register(ToolImpl("mail", ToolSpec(
        name="gmail_search",
        description=(
            "Search the user's Gmail using Gmail query syntax: "
            "'from:alice@x.com', 'subject:proposal', 'has:attachment', "
            "'newer_than:7d', 'is:unread', 'label:STARRED'. Returns a list "
            "of matching messages with sender, subject, date, snippet."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Gmail query syntax"},
                "limit": {"type": "integer", "description": "Max results. Default 10, max 50."},
            },
            "required": ["query"],
        },
    ), _gmail_search))

    register(ToolImpl("mail", ToolSpec(
        name="gmail_list_inbox",
        description="List the most-recent messages from the user's inbox.",
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Default 10, max 50."},
            },
        },
    ), _gmail_list_inbox))

    register(ToolImpl("mail", ToolSpec(
        name="gmail_read_message",
        description="Fetch and return the full body of one Gmail message by its ID.",
        parameters={
            "type": "object",
            "properties": {
                "message_id": {"type": "string",
                               "description": "The Gmail message id (from gmail_search results)"},
            },
            "required": ["message_id"],
        },
    ), _gmail_read))
