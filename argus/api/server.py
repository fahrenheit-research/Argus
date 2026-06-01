"""ARGUS HTTP + WebSocket API — the bridge for the mobile / web app.

Exposes the entire ARGUS agent loop as a clean REST + WebSocket API so the
Android app (or any HTTP client) can:

  POST /chat                  — one-shot reply (non-streaming)
  WS   /chat/stream           — token-streaming chat
  GET  /voice/state           — current voice state (asleep/listening/...)
  POST /voice/say             — synthesize + play a phrase
  GET  /sessions              — list sessions
  GET  /sessions/{id}         — load one session's messages
  GET  /memory                — full MEMORY.md + USER.md contents
  POST /memory                — append a fact
  GET  /tools                 — list available tools + enabled state
  POST /tools/{name}/toggle   — enable/disable a toolset
  GET  /files                 — list ~/argus-workspace files
  GET  /files/{name}          — download one workspace file
  POST /upload                — upload a file to the workspace
  GET  /health                — quick health check (provider, model, version)
  GET  /                      — root: API info

Start it with:  argus serve [--host 0.0.0.0] [--port 8787]
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Optional

log = logging.getLogger("argus.api")


# ── Lazy imports ──────────────────────────────────────────────────────────────
# fastapi is an optional dep; only import when serve is actually invoked.

def _require_fastapi():
    try:
        from fastapi import (FastAPI, WebSocket, WebSocketDisconnect, UploadFile,
                             File, HTTPException, BackgroundTasks)
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
        from pydantic import BaseModel
        return locals()
    except ImportError as e:
        raise RuntimeError(
            "FastAPI not installed. Run: uv sync --extra api"
        ) from e


# ── Workspace ────────────────────────────────────────────────────────────────

WORKSPACE = Path(os.path.expanduser("~/argus-workspace"))
WORKSPACE.mkdir(exist_ok=True)


# ── Build the FastAPI app ────────────────────────────────────────────────────

def build_app():
    """Construct and return the FastAPI application."""
    fa = _require_fastapi()
    FastAPI         = fa["FastAPI"]
    WebSocket       = fa["WebSocket"]
    WebSocketDisconnect = fa["WebSocketDisconnect"]
    UploadFile      = fa["UploadFile"]
    File            = fa["File"]
    HTTPException   = fa["HTTPException"]
    CORSMiddleware  = fa["CORSMiddleware"]
    FileResponse    = fa["FileResponse"]
    JSONResponse    = fa["JSONResponse"]
    StreamingResponse = fa["StreamingResponse"]
    BaseModel       = fa["BaseModel"]

    from argus import __version__
    from argus import config as _config
    from argus.tools.registry import register_defaults, get_tool

    # Side-effect: register all tools so the agent loop has them.
    register_defaults()

    app = FastAPI(
        title="ARGUS API",
        version=__version__,
        description="HTTP + WebSocket bridge for ARGUS — the multi-eyed sentinel.",
    )

    # Allow any origin — this is meant to run on your LAN/localhost.
    # If you expose it to the internet, narrow this to your app's origin.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Pydantic models ──────────────────────────────────────────────────────
    class ChatRequest(BaseModel):
        message: str
        session_id: Optional[str] = None
        provider:   Optional[str] = None
        model:      Optional[str] = None
        use_swarm:  bool          = False

    class ChatResponse(BaseModel):
        reply:       str
        session_id:  str
        tokens_used: int = 0
        elapsed_ms:  int = 0
        tool_calls:  list[dict] = []

    class MemoryAppend(BaseModel):
        fact: str

    class ToolToggle(BaseModel):
        enabled: bool

    # ── Root ─────────────────────────────────────────────────────────────────
    @app.get("/")
    async def root():
        return {
            "service":  "argus-api",
            "version":  __version__,
            "endpoints": [
                "/chat", "/chat/stream", "/voice/state", "/voice/say",
                "/sessions", "/memory", "/tools", "/files",
                "/upload", "/health",
            ],
        }

    @app.get("/health")
    async def health():
        cfg = _config.load()
        return {
            "ok":       True,
            "version":  __version__,
            "provider": cfg.agent.default_provider,
            "model":    cfg.agent.default_model,
            "toolsets": cfg.agent.toolsets,
        }

    # ── Chat — one-shot, non-streaming ──────────────────────────────────────
    @app.post("/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest):
        import time
        from argus.loop import run_turn, Conversation
        from argus.swarm.auto import should_swarm

        cfg = _config.load()
        if req.provider:
            cfg.agent.default_provider = req.provider
        if req.model:
            cfg.agent.default_model = req.model

        convo = Conversation()
        session_id = req.session_id or uuid.uuid4().hex[:12]

        # Auto-swarm detection
        if req.use_swarm or should_swarm(req.message)[0]:
            from argus.swarm.constellation import Constellation
            con   = Constellation(cfg=cfg, goal=req.message)
            t0    = time.monotonic()
            result = await con.run()
            return ChatResponse(
                reply=result.get("final", ""),
                session_id=session_id,
                tokens_used=result.get("tokens", 0),
                elapsed_ms=int((time.monotonic() - t0) * 1000),
                tool_calls=result.get("tool_calls", []),
            )

        # Normal one-shot
        t0 = time.monotonic()
        reply_parts: list[str] = []
        tool_calls : list[dict] = []
        try:
            async for evt in run_turn(cfg, convo, req.message):
                etype = type(evt).__name__
                if etype == "TextEvent" and getattr(evt, "text", None):
                    reply_parts.append(evt.text)
                elif etype == "ToolCallEvent":
                    tool_calls.append({
                        "name": getattr(evt, "name", ""),
                        "args": getattr(evt, "arguments", {}),
                    })
        except Exception as e:
            raise HTTPException(500, f"{type(e).__name__}: {e}")

        return ChatResponse(
            reply="".join(reply_parts),
            session_id=session_id,
            elapsed_ms=int((time.monotonic() - t0) * 1000),
            tool_calls=tool_calls,
        )

    # ── Chat — token-streaming over WebSocket ────────────────────────────────
    @app.websocket("/chat/stream")
    async def chat_stream(ws: WebSocket):
        """Client → server:  {"message": "...", "session_id": "..."}
        Server → client:  {"t": "token",  "text": "..."}
                          {"t": "tool",   "name": "...", "args": {...}}
                          {"t": "done",   "elapsed_ms": 1234}
                          {"t": "error",  "message": "..."}
        """
        await ws.accept()
        from argus.loop import run_turn, Conversation
        from argus.swarm.auto import should_swarm
        import time

        convo = Conversation()

        try:
            while True:
                raw = await ws.receive_text()
                try:
                    req = json.loads(raw)
                except json.JSONDecodeError:
                    await ws.send_json({"t": "error", "message": "invalid JSON"})
                    continue

                message = req.get("message", "").strip()
                if not message:
                    await ws.send_json({"t": "error", "message": "empty message"})
                    continue

                cfg = _config.load()
                t0  = time.monotonic()

                # Auto-swarm if multi-task
                if req.get("use_swarm") or should_swarm(message)[0]:
                    from argus.swarm.constellation import Constellation
                    await ws.send_json({"t": "swarm_start"})
                    try:
                        con    = Constellation(cfg=cfg, goal=message)
                        result = await con.run()
                        await ws.send_json({"t": "token", "text": result.get("final", "")})
                    except Exception as e:
                        await ws.send_json({"t": "error", "message": f"swarm: {e}"})
                    await ws.send_json({
                        "t": "done",
                        "elapsed_ms": int((time.monotonic() - t0) * 1000),
                    })
                    continue

                # Normal streaming
                try:
                    async for evt in run_turn(cfg, convo, message):
                        etype = type(evt).__name__
                        if etype == "TextEvent" and getattr(evt, "text", None):
                            await ws.send_json({"t": "token", "text": evt.text})
                        elif etype == "ToolCallEvent":
                            await ws.send_json({
                                "t": "tool",
                                "name": getattr(evt, "name", ""),
                                "args": getattr(evt, "arguments", {}),
                            })
                except Exception as e:
                    await ws.send_json({"t": "error", "message": f"{type(e).__name__}: {e}"})

                await ws.send_json({
                    "t": "done",
                    "elapsed_ms": int((time.monotonic() - t0) * 1000),
                })

        except WebSocketDisconnect:
            return

    # ── Voice state ─────────────────────────────────────────────────────────
    @app.get("/voice/state")
    async def voice_state():
        try:
            from argus.voice.listener import voice_state as vs
            return {
                "status":   vs.status,
                "hint":     vs.hint,
                "rms":      getattr(vs, "rms", 0.0),
            }
        except Exception as e:
            return {"status": "off", "hint": f"voice unavailable: {e}", "rms": 0.0}

    @app.post("/voice/say")
    async def voice_say(payload: dict):
        text = payload.get("text", "").strip()
        if not text:
            raise HTTPException(400, "no text")
        try:
            from argus.voice.listener import synth_best
            path = await synth_best(text)
            if path and path.exists():
                return FileResponse(str(path), media_type="audio/mpeg",
                                    filename=path.name)
            raise HTTPException(500, "synthesis failed")
        except Exception as e:
            raise HTTPException(500, f"{type(e).__name__}: {e}")

    # ── Sessions ────────────────────────────────────────────────────────────
    @app.get("/sessions")
    async def list_sessions():
        from argus.paths import SESSIONS_DIR
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        out = []
        for p in sorted(SESSIONS_DIR.glob("*.json"), reverse=True):
            try:
                d = json.loads(p.read_text())
                out.append({
                    "id":       p.stem,
                    "title":    d.get("title", p.stem),
                    "created":  d.get("created", ""),
                    "messages": len(d.get("messages", [])),
                })
            except Exception:
                continue
        return out

    @app.get("/sessions/{session_id}")
    async def get_session(session_id: str):
        from argus.paths import SESSIONS_DIR
        path = SESSIONS_DIR / f"{session_id}.json"
        if not path.exists():
            raise HTTPException(404, "session not found")
        return json.loads(path.read_text())

    # ── Memory ──────────────────────────────────────────────────────────────
    @app.get("/memory")
    async def get_memory():
        from argus.paths import MEMORY_MD, HOME
        user_md = HOME / "USER.md"
        return {
            "memory": MEMORY_MD.read_text() if MEMORY_MD.exists() else "",
            "user":   user_md.read_text()   if user_md.exists()   else "",
        }

    @app.post("/memory")
    async def add_memory(payload: MemoryAppend):
        from argus.paths import MEMORY_MD
        MEMORY_MD.parent.mkdir(parents=True, exist_ok=True)
        with MEMORY_MD.open("a") as f:
            f.write(f"\n- {datetime.now().isoformat(timespec='seconds')}: {payload.fact}\n")
        return {"ok": True, "fact": payload.fact}

    # ── Tools ────────────────────────────────────────────────────────────────
    @app.get("/tools")
    async def list_tools():
        from argus.tools.registry import _REGISTRY
        cfg = _config.load()
        enabled = set(cfg.agent.toolsets)
        out = []
        for name, impl in _REGISTRY.items():
            out.append({
                "name":     name,
                "toolset":  getattr(impl, "toolset", "core"),
                "enabled":  getattr(impl, "toolset", "core") in enabled,
                "description": getattr(impl.spec, "description", "")[:120],
            })
        return out

    @app.post("/tools/{toolset}/toggle")
    async def toggle_toolset(toolset: str, payload: ToolToggle):
        cfg = _config.load()
        if payload.enabled and toolset not in cfg.agent.toolsets:
            cfg.agent.toolsets.append(toolset)
        elif not payload.enabled and toolset in cfg.agent.toolsets:
            cfg.agent.toolsets.remove(toolset)
        _config.save(cfg)
        return {"toolset": toolset, "enabled": payload.enabled,
                "toolsets": cfg.agent.toolsets}

    # ── Files ────────────────────────────────────────────────────────────────
    @app.get("/files")
    async def list_files():
        out = []
        for p in sorted(WORKSPACE.iterdir(), key=lambda x: -x.stat().st_mtime):
            if p.is_file():
                out.append({
                    "name":     p.name,
                    "size":     p.stat().st_size,
                    "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
                    "ext":      p.suffix.lower(),
                })
        return out

    @app.get("/files/{filename}")
    async def get_file(filename: str):
        # Sanitize: no path escapes
        path = (WORKSPACE / filename).resolve()
        if WORKSPACE.resolve() not in path.parents:
            raise HTTPException(400, "path escapes workspace")
        if not path.exists():
            raise HTTPException(404, "file not found")
        return FileResponse(str(path), filename=path.name)

    @app.post("/upload")
    async def upload(file: UploadFile = File(...)):
        if not file.filename:
            raise HTTPException(400, "no filename")
        # Sanitize filename
        safe_name = Path(file.filename).name
        target = WORKSPACE / safe_name
        content = await file.read()
        target.write_bytes(content)
        return {"name": safe_name, "size": len(content),
                "path": str(target)}

    return app


def serve(host: str = "0.0.0.0", port: int = 8787,
          reload: bool = False) -> None:
    """Launch the API server. Called from `argus serve`."""
    try:
        import uvicorn
    except ImportError as e:
        raise RuntimeError("uvicorn not installed. Run: uv sync --extra api") from e

    # Build the app once so import errors surface immediately
    app = build_app()
    print(f"\n  ⟨◇⟩  ARGUS API listening on http://{host}:{port}\n")
    print(f"     Try:  curl http://{host}:{port}/health")
    print(f"     Docs: http://{host}:{port}/docs\n")
    uvicorn.run(app, host=host, port=port, reload=reload,
                log_level="info")
