# ARGUS HTTP + WebSocket API

The `argus serve` command exposes the entire agent loop as a REST + WebSocket
API. Use it for the mobile app, a web client, integrations, automation —
anything you'd otherwise wrap with a subprocess call.

## Install

```bash
uv sync --extra api
```

Adds `fastapi`, `uvicorn`, `python-multipart`, `websockets`.

## Run

```bash
argus serve                           # 0.0.0.0:8787
argus serve --host 127.0.0.1          # localhost only
argus serve --port 9000 --reload      # dev mode
```

Swagger UI:  `http://localhost:8787/docs`
Health check: `curl http://localhost:8787/health`

## Endpoints

| Method | Path                          | Purpose                                |
|--------|-------------------------------|----------------------------------------|
| GET    | `/`                           | Service info                           |
| GET    | `/health`                     | Provider, model, version, enabled toolsets |
| POST   | `/chat`                       | One-shot reply (auto-swarms multi-task) |
| WS     | `/chat/stream`                | Token-streaming chat                   |
| GET    | `/voice/state`                | Current voice state + RMS              |
| POST   | `/voice/say`                  | Synthesize a phrase → returns MP3      |
| GET    | `/sessions`                   | List past chat sessions                |
| GET    | `/sessions/{id}`              | Load one session                       |
| GET    | `/memory`                     | MEMORY.md + USER.md contents           |
| POST   | `/memory`                     | Append a fact                          |
| GET    | `/tools`                      | List all registered tools + enabled state |
| POST   | `/tools/{toolset}/toggle`     | Enable/disable a toolset               |
| GET    | `/files`                      | List `~/argus-workspace/`              |
| GET    | `/files/{name}`               | Download one file                      |
| POST   | `/upload`                     | Upload a file (multipart)              |

## Examples

### Chat (REST)

```bash
curl -s http://localhost:8787/chat \
     -H 'Content-Type: application/json' \
     -d '{"message": "what is 2 plus 2"}'
```

```json
{
  "reply": "Four.",
  "session_id": "a1b2c3d4",
  "tokens_used": 0,
  "elapsed_ms": 540,
  "tool_calls": []
}
```

### Chat (WebSocket — recommended for the mobile app)

```python
import asyncio, json, websockets

async def main():
    async with websockets.connect("ws://localhost:8787/chat/stream") as ws:
        await ws.send(json.dumps({"message": "what's the weather in tokyo"}))
        async for raw in ws:
            evt = json.loads(raw)
            print(evt)
            if evt.get("t") == "done":
                break

asyncio.run(main())
```

Server emits:
- `{"t": "token", "text": "..."}` — each generated chunk
- `{"t": "tool",  "name": "web_search", "args": {...}}` — when a tool fires
- `{"t": "swarm_start"}` — when Constellation engages
- `{"t": "done",  "elapsed_ms": 1240}` — turn complete
- `{"t": "error", "message": "..."}` — on failure

### File upload

```bash
curl -F file=@./report.pdf http://localhost:8787/upload
# { "name": "report.pdf", "size": 12480, "path": "/Users/.../argus-workspace/report.pdf" }
```

## Security notes

The default config binds `0.0.0.0` (all interfaces) so the mobile app on
your phone can reach the Mac on the LAN. This is fine on a trusted home
network. **Do not expose this port to the internet** — there is no auth.

If you need to put it on the public internet:
1. Bind to localhost only: `argus serve --host 127.0.0.1`
2. Front it with a reverse proxy (Caddy, Nginx) doing TLS + basic auth
3. Or tunnel through Tailscale / Cloudflare Tunnel

CORS is wide-open by default for development. Narrow it in
`argus/api/server.py:CORSMiddleware` before publishing.
