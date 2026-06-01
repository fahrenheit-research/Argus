"""Smoke tests — every screen and primitive renders without crashing.

Uses a Rich Console with `record=True` and `file=StringIO` so nothing is
printed; failures show up as exceptions or empty output.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from argus import __version__
from argus import theme as argus_theme
from argus.banner import banner_panel, print_banner
from argus.data import providers, seed
from argus.glyph import glyph, glyph_and_mark, inline_prompt_glyph, wordmark
from argus.screens import (
    config as cfg,
    doctor,
    gateway,
    memory,
    sessions,
    skills,
    update,
)
from argus.slash import ChatState, dispatch
from argus.state import is_secret_key, mask_secret, scrub_line
from argus.stub.llm import FauxProvider
from argus.stub.telegram import get_me, validate_token


def _silent_console() -> Console:
    return argus_theme.console(file=io.StringIO(), record=True, color_system="truecolor", width=120)


def test_version_string():
    assert __version__.count(".") == 2


def test_glyph_sizes_render():
    for size in ("display", "standard", "inline"):
        g = glyph(size)  # type: ignore[arg-type]
        assert "◇" in g.plain
        assert "⟨" in g.plain and "⟩" in g.plain
    assert "A R G U S" in glyph_and_mark().plain
    assert wordmark().plain == "A R G U S"
    assert inline_prompt_glyph().plain == "⟨◇⟩"


def test_banner_renders():
    c = _silent_console()
    print_banner(c, provider="groq", model="llama-3.3-70b-versatile")
    out = c.export_text()
    # Block-letter wordmark + the model row beneath the eye + tools/skills
    # headings should all be present.
    assert "ARGUS" in out  # wordmark renders via ANSI shadow blocks
    assert "llama-3.3-70b-versatile" in out
    assert "Available Tools" in out
    assert "Available Skills" in out
    assert __version__ in out


def test_mask_secret_redacts_middle():
    full = "gsk_abcdefghijklmnopqrstuvwxyzABCDEFGH"
    masked = mask_secret(full)
    assert masked.startswith("gsk_")
    assert masked.endswith(full[-4:])
    assert "*" in masked
    assert full[8:-8] not in masked  # the middle is gone


def test_is_secret_key():
    assert is_secret_key("GROQ_API_KEY")
    assert is_secret_key("TELEGRAM_BOT_TOKEN")
    assert is_secret_key("providers.openai.api_key")
    assert not is_secret_key("agent.default_provider")
    assert not is_secret_key("ui.color")


def test_scrub_line_strips_known_prefixes():
    line = "got token gsk_REAL_SECRET_HERE_DO_NOT_SHIP and sk-ant-OTHER123 too"
    out = scrub_line(line)
    assert "REAL_SECRET" not in out
    assert "OTHER123" not in out
    assert "gsk_***" in out
    assert "sk-ant-***" in out


def test_provider_registry_complete():
    ids = {p.id for p in providers.PROVIDERS}
    # PRD §11.1 — every provider in the support matrix
    assert {"groq", "openai", "anthropic", "openrouter", "gemini", "deepseek", "huggingface", "ollama", "custom"} <= ids
    groq = providers.get_provider("groq")
    assert any(m.id == "llama-3.3-70b-versatile" for m in groq.models)


def test_faux_provider_streams_some_tokens():
    p = FauxProvider(seed=42)
    chunks = list(p.stream("hello"))
    assert len(chunks) > 10
    text = "".join(chunks)
    assert any(text == reply for reply in _all_canned_replies())


def _all_canned_replies() -> list[str]:
    from argus.stub.llm import _CANNED_REPLIES
    return _CANNED_REPLIES


def test_telegram_token_validation():
    assert validate_token("123456789:ABCdefGHIjklMNOpqrSTUvwxYZ_abcdef")
    assert not validate_token("not a token")
    assert get_me("123456789:ABCdefGHIjklMNOpqrSTUvwxYZ_abcdef") is not None
    assert get_me("garbage") is None


@pytest.mark.parametrize("fn", [
    lambda c: doctor.run(force_all_green=False),
    lambda c: doctor.run(force_all_green=True),
    lambda c: gateway.status(),
    lambda c: gateway.stop(),
    lambda c: gateway.allow(user_id=None, list_only=True),
    lambda c: gateway.revoke("487293841"),
    lambda c: gateway.logs(n=5),
    lambda c: memory.show(),
    lambda c: memory.add("a fact"),
    lambda c: skills.list_skills(),
    lambda c: skills.set_enabled("github-pr-review", enabled=True),
    lambda c: skills.set_enabled("unknown-skill", enabled=False),
    lambda c: sessions.list_sessions(),
    lambda c: sessions.tree(),
    lambda c: sessions.resume("refactor"),
    lambda c: sessions.resume(""),
    lambda c: sessions.export("20260530_211522_a1b2c3"),
    lambda c: sessions.delete("20260530_211522_a1b2c3"),
    lambda c: cfg.list_config(),
    lambda c: cfg.get_config("agent.default_provider"),
    lambda c: cfg.set_config("agent.default_model", "llama-3.1-8b-instant"),
    lambda c: cfg.set_config("OPENAI_API_KEY", "sk-some-fake-value"),
    lambda c: update.run(check=True, channel="stable"),
    lambda c: update.rollback(),
])
def test_screens_render_without_raising(fn):
    # Each screen creates its own console internally; we just confirm no exception.
    fn(None)


def test_slash_dispatch_status_and_help():
    c = _silent_console()
    state = ChatState()
    dispatch("/help", c, state)
    dispatch("/status", c, state)
    dispatch("/unknown", c, state)
    out = c.export_text()
    # We can't see internal consoles' output here, but the dispatch path
    # returns without raising, which is the contract under test.
    assert isinstance(out, str)


def test_seed_data_is_well_formed():
    assert len(seed.SESSIONS) >= 3
    # parent_id chain — every parent_id either is None or points at an existing id
    ids = {s.id for s in seed.SESSIONS}
    for s in seed.SESSIONS:
        if s.parent_id is not None:
            assert s.parent_id in ids
