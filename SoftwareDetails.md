# Mnema

A production-ready **AI onboarding assistant** that answers employee
questions using your company's internal documentation, augmented with
the LLM's own domain expertise.

Built with:
- FastAPI (async backend)
- Local LLM via Ollama (default: `qwen2.5:7b`, configurable)
- RAG pipeline (pgvector + sentence-transformer embeddings +
  cross-encoder reranker)
- Role-based document access control (RBAC)
- Fully containerised — one-command launch on any host

------------------------------------------------------------------------

## Features

- Chat-based onboarding assistant
- Context-aware answers using indexed company documents
- Role-based document access (HR / Developer / Admin)
- Real-time token streaming over Server-Sent Events
- Single-file upload + whole-folder upload from the browser
  (PDF, DOCX, TXT/Markdown, CSV/JSON, source code)
- HNSW vector index for fast pgvector retrieval at scale
- Optional cross-encoder reranking
  (`cross-encoder/ms-marco-MiniLM-L-6-v2`)
- Auto-generated conversation titles after the first exchange
- JWT auth with HttpOnly cookies + refresh-token rotation
- Rate-limiting (slowapi), security headers, CORS allowlist
- Strong-`SECRET_KEY` validation that hard-fails on insecure defaults

------------------------------------------------------------------------

## Architecture

```
Browser  --HTTPS-->  FastAPI (uvicorn)
                       |
                       +-- /auth        JWT + HttpOnly cookies
                       +-- /chat (SSE)  ChatService -> RAGPipeline
                       |                                  |
                       |                  +-- Retriever (pgvector + reranker)
                       |                  +-- Prompt builder (role-aware)
                       |                  +-- Ollama /api/chat (stream)
                       +-- /admin       Documents, users, roles

PostgreSQL (pgvector)   <-- async SQLAlchemy
Ollama (CPU or NVIDIA)  <-- httpx streaming, per-phase timeouts
HuggingFace cache       <-- named volume, downloads on first run
```

The container topology is choreographed so a fresh `docker compose up
--build` always lands on a working system:

```
db  (pgvector/pgvector:pg17)  --healthcheck (pg_isready) -->
     ollama  (ollama/ollama)  --healthcheck (ollama list) -->
          ollama-init  (one-shot pull of $OLLAMA_MODEL, exits 0)
                                    |
                                    +--> app  (FastAPI + RAG)
                                          - waits db healthy
                                          - waits ollama healthy
                                          - waits ollama-init completed
                                          - runs docker_entrypoint.py:
                                              wait for /api/tags,
                                              create tables + HNSW index,
                                              seed admin user
```

------------------------------------------------------------------------

## Project Structure

```
mnema/
├── app/
│   └── main.py                  # FastAPI factory + lifespan
├── api/
│   ├── deps.py                  # auth dependencies
│   ├── routers/{auth,chat,admin}.py
│   └── schemas/                 # Pydantic request/response models
├── core/
│   ├── config.py                # Pydantic settings + SECRET_KEY guard
│   ├── security.py              # JWT + bcrypt
│   ├── exceptions.py            # typed HTTP errors
│   └── limiter.py               # slowapi rate limiter
├── db/
│   ├── session.py               # async engine + AsyncSessionLocal
│   ├── models.py                # SQLAlchemy + pgvector Vector column
│   └── repositories/            # one repo per aggregate
├── services/
│   ├── chat_service.py          # SSE generator, RAG orchestration
│   ├── ingestion_service.py     # load -> chunk -> embed -> bulk insert
│   └── title_service.py         # auto conversation-title generation
├── rag/
│   ├── pipeline.py              # streams from Ollama /api/chat
│   ├── retriever.py             # pgvector + reranker, RBAC-filtered
│   ├── embedder.py              # sentence-transformers (async-safe)
│   ├── reranker.py              # cross-encoder (async-safe)
│   └── prompts.py               # role-aware system prompts
├── ingestion/
│   ├── loader.py                # PDF (PyMuPDF) / DOCX / text
│   └── chunker.py               # overlap-aware splitter
├── frontend/
│   ├── templates/               # Jinja2 (base, index, chat, admin)
│   └── static/                  # CSS + JS
├── scripts/
│   ├── docker_entrypoint.py     # boot orchestration inside container
│   ├── create_env.py            # generates strong SECRET_KEY into .env
│   ├── seed_admin.py            # standalone admin-create utility
│   └── index_documents.py       # CLI re-indexer
├── tests/                       # pytest suite (auth, ingestion, RAG)
├── docker-compose.yml           # full stack (GPU by default)
├── Dockerfile
├── .env.example
├── requirements.txt
└── README.md
```

------------------------------------------------------------------------

## Tech Stack

- **Web**: FastAPI, uvicorn (uvloop + httptools), Jinja2
- **Database**: PostgreSQL 17 with `pgvector` extension; async
  SQLAlchemy 2.x; HNSW index for cosine-distance search
- **LLM**: Ollama (any chat-capable model — `qwen2.5:7b` by default)
- **Embeddings**: `BAAI/bge-base-en-v1.5` (768-dim) via
  `sentence-transformers`
- **Reranking**: `cross-encoder/ms-marco-MiniLM-L-6-v2` (optional)
- **Auth**: JWT (HS256) + bcrypt; HttpOnly access + refresh cookies
- **HTTP client**: `httpx` with per-phase `Timeout(connect=10, read=600,
  write=30, pool=10)` so 7B-model cold starts don't kill the stream
- **Document loaders**: PyMuPDF (PDF), `python-docx`, plain text /
  source code
- **Rate limiting**: `slowapi`
- **Containerisation**: Docker Compose v2 with healthchecks and ordered
  dependencies

------------------------------------------------------------------------

## Run

### Default (CPU, works on any host)

```bash
docker compose up --build
```

This will:

1. Start Postgres + pgvector, wait until healthy
2. Start Ollama, wait until `/api/tags` responds
3. Run a one-shot `ollama-init` that pulls `$OLLAMA_MODEL` (default
   `qwen2.5:7b`) — the app waits on `service_completed_successfully`
   before booting so chat never 404s on a fresh clone
4. Start the FastAPI app, which creates tables + the HNSW index and
   seeds the admin user from `.env`

Open `http://localhost:8000` and sign in with the credentials from
`.env` (defaults: `admin` / `Admin123!`).

### GPU (default, NVIDIA)

Ollama runs on the GPU by default — no extra flags:

```bash
docker compose up --build
```

Requires `nvidia-container-toolkit` on the host. To run on CPU instead,
comment out the `deploy:` block on the `ollama` service in
`docker-compose.yml`.

### Switching the model

In `.env`:

```env
OLLAMA_MODEL=llama3.1:8b
```

Then `docker compose up --build` — `ollama-init` pulls the new model
automatically.

------------------------------------------------------------------------

## Configuration (selected)

| Variable             | Default                                | Notes                                                       |
| -------------------- | -------------------------------------- | ----------------------------------------------------------- |
| `SECRET_KEY`         | required, ≥ 32 chars, no dev fragments | hard-fail validator; use `python scripts/create_env.py`     |
| `DATABASE_URL`       | `postgresql+asyncpg://app:postgres@db:5432/app` | service name `db` resolves over the Compose network |
| `OLLAMA_BASE_URL`    | `http://ollama:11434`                  | overridden in Compose to enforce in-container hostname      |
| `OLLAMA_MODEL`       | `qwen2.5:7b`                           | pulled automatically by `ollama-init`                       |
| `EMBED_MODEL`        | `BAAI/bge-base-en-v1.5`                | must match `EMBED_DIMENSION`                                |
| `EMBED_DIMENSION`    | `768`                                  | 384 for `all-MiniLM-L6-v2`, 768 for `bge-base-en-v1.5`      |
| `RERANKER_ENABLED`   | `true`                                 | toggle the cross-encoder pass                               |
| `TOP_K_RETRIEVAL`    | `5`                                    | chunks injected into the prompt                             |
| `RETRIEVAL_CANDIDATES` | `20`                                 | candidates fetched before reranking                         |
| `ALLOWED_ORIGINS`    | `http://localhost:8000,...`            | CORS allowlist (explicit, no wildcards)                     |
| `HF_CACHE_DIR`       | unset (named volume)                   | optional: bind-mount host HF cache to skip first-run download |

------------------------------------------------------------------------

## Recent reliability hardening

- Per-phase `httpx.Timeout` so cold model loads (30–120 s on CPU) no
  longer kill the SSE stream.
- Ollama healthcheck + `ollama-init` ordering so the app never
  starts before the model is ready.
- Removed `TRANSFORMERS_OFFLINE` + the baked HF cache COPY — models
  download to a named volume on first run.
- GPU is the default (`deploy:` block on the `ollama` service); CPU is a
  one-line comment-out for hosts without an NVIDIA GPU.
- Dropped `--reload` and the `.:/app` bind mount that killed
  in-flight SSE responses during edits.
- Bulk-insert document chunks (one `flush` per document, not per chunk)
  — ~50–100× faster ingestion on large PDFs.
- Refresh-token cookie flow with distinct 401 messages for expired vs.
  invalid tokens.
- Frontend falls back to escaped text when `marked` / `DOMPurify` are
  unreachable (offline / CSP / blocked CDN), so streamed tokens are
  never silently dropped.
- File-upload dialog no longer reopens twice on first selection (input
  moved outside the drop-zone to break the post-picker click bubble).

------------------------------------------------------------------------

## Summary

A self-contained, privacy-first AI onboarding assistant. One
`docker compose up --build` from a clean clone gives you Postgres
with pgvector, Ollama with the model pre-pulled, and a streaming
chat UI with role-based document retrieval — no manual steps, no
cloud calls, no shared infrastructure.
