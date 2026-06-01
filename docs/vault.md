# ARGUS Vault

Local-first SQLite + vector memory store.

The Vault is where ARGUS remembers things across sessions — your preferences,
facts about your work, learnings from past conversations. It is:

- **Local-first** — no service, no cloud, no account. Single SQLite file.
- **Fast** — embedded sqlite-vec index. Recall typically <5 ms for 1k entries,
  <50 ms for 10k.
- **Tiny** — ~86 MB for the local embedder model. The vault DB itself is
  a few KB per entry.
- **Yours** — your `~/.argus/vault.db` never leaves the machine unless you
  copy it.

## Install

```bash
uv sync --extra vault
```

This adds `sqlite-vec`, `sentence-transformers`, and `numpy`. First call to
`remember` will download the MiniLM model (~86 MB, cached at
`~/.cache/huggingface/hub/`).

## Quick usage

### From the CLI

```bash
argus vault remember "I prefer a deep British male voice for ARGUS"
argus vault remember "Q4 revenue target is 16.8M" --kind fact --tags revenue,Q4
argus vault remember "Met with stripe support 2026-05-31 about webhook outage" \
    --kind event --source meeting

argus vault recall "what voice do I want"
argus vault recall "anything about stripe outages" -k 10
argus vault recall "Q4 targets" --kind fact

argus vault list
argus vault stats
argus vault forget 42
```

### From Python

```python
from argus.vault import Vault

v = Vault()
v.remember("ARGUS uses 14 swarm roles inspired by Argus Panoptes.")
v.remember("Brand colors: #FF38D1, #FFC247, #42E8F5", kind="fact", tags="brand")

matches = v.recall("how many swarm roles", k=3)
for m in matches:
    print(f"  {m.score:.3f}  #{m.id}  {m.text[:80]}")
```

## Kinds

A `kind` is a free-form tag, but the conventions ARGUS uses are:

| Kind      | Purpose                                                    |
|-----------|------------------------------------------------------------|
| `fact`    | Stable knowledge — defaults, configurations, definitions   |
| `user`    | User preferences and identity                              |
| `event`   | Time-bounded things that happened                          |
| `snippet` | Code, reference text, anything copy-paste-able             |
| `skill`   | Routines / playbooks (consumed by AgentMomento)            |

Filter recall to one kind:

```bash
argus vault recall "..." --kind user
```

## Embedders

Default: **all-MiniLM-L6-v2** (local, 384-dim, 86 MB). Fast, free, offline.

Override with the `ARGUS_VAULT_EMBED` env var:

| Value             | Model                                    | Dim | Cost              |
|-------------------|------------------------------------------|-----|-------------------|
| `minilm` (default)| sentence-transformers MiniLM-L6-v2       | 384 | Free, local       |
| `openai-3-small`  | OpenAI text-embedding-3-small            | 1536| ~$0.02 / 1M tokens|
| `openai-3-large`  | OpenAI text-embedding-3-large            | 3072| ~$0.13 / 1M tokens|

OpenAI models require `OPENAI_API_KEY`. The vault stores the embedder name
+ dim per entry, so it never compares vectors of different sizes — you can
mix models in the same vault if you really want to (recall will only
consider entries matching the embedder you're currently using).

## How recall works

1. Your query is embedded once with the active embedder.
2. SQLite returns all stored vectors of matching `(model, dim)`.
3. Cosine similarity computed in-process (embeddings are unit-normalised so
   `dot product = cosine`).
4. Sorted descending, top-k returned with their similarity scores.

Score range: `[-1, 1]`. In practice for MiniLM, relevant matches score 0.4+,
strong matches 0.6+, near-duplicates 0.9+.

## Backends

| Backend     | When used                                         | Performance       |
|-------------|---------------------------------------------------|-------------------|
| `sqlite-vec`| When the C extension loads (most macOS/Linux)     | <5 ms / 1k rows   |
| `numpy`     | Fallback when extension can't load                | ~50 ms / 10k rows |

`argus vault stats` shows which one you're on.

## Backup & sync

The vault is one SQLite file. To back it up:

```bash
cp ~/.argus/vault.db ~/Dropbox/argus-vault-$(date +%F).db
```

To sync between machines: use any file-sync tool (Dropbox, rsync, Syncthing).
SQLite handles concurrent reads fine; do not write from two machines at once.

## When to use it

- Things ARGUS should remember across sessions
- Searching past conversations semantically (rather than grep)
- Long-term user preferences that should bias responses
- Domain knowledge specific to your workflow

## When NOT to use it

- Bulk document storage — use `~/argus-workspace/` (a regular folder)
- High-frequency event logging — use a real log file
- Anything you'd query by exact ID — use a regular dict / JSON

## Roadmap (good first contributions)

- Auto-recall hook in the agent loop (inject top-3 vault hits as system context)
- `argus vault import` from MEMORY.md
- Hybrid search (BM25 + vector) for keyword-heavy queries
- Time-decay scoring (recent memories rank higher)
- Tag-based filtering in CLI (`--tag voice` etc.)
