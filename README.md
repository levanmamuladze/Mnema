# Mnema

**An AI-powered, role-aware onboarding assistant.** Employees ask questions in
plain language and get answers grounded *only* in the company documents their
role is allowed to see.

Built with FastAPI, PostgreSQL + pgvector, a local Ollama LLM, and a
retrieval-augmented-generation (RAG) pipeline with cross-encoder reranking. The
entire stack — database, vector index, LLM, and app — comes up from a single
`docker compose up --build`.

---

## Executive Summary

Onboarding new hires is slow and repetitive: the same questions about policies,
tools, and processes get asked over and over, and the answers live scattered
across PDFs, wikis, and people's heads. Mnema turns a company's existing
documents into a conversational assistant that is:

- **Role-aware** — an HR employee and an engineer asking the same question get
  answers drawn from different document sets. The model is *architecturally*
  prevented from answering outside the user's granted scope.
- **Grounded** — answers cite retrieved company documents; the system refuses
  rather than hallucinate when no relevant document exists.
- **Self-hosted** — runs entirely on your infrastructure with a local LLM
  (Ollama). No document content leaves the network. Suitable for sensitive
  internal material.

---

## Product Overview

| | |
|---|---|
| **Who it's for** | Companies onboarding employees across distinct teams/departments that each have their own confidential documentation. |
| **Core loop** | Admin uploads docs → tags them with roles → employee chats with a role-scoped assistant grounded in those docs. |
| **Differentiator** | Dynamic, per-role RBAC enforced at the database query layer, not just in prompts; fully self-hostable with a local LLM. |

---

## Features

- **Conversational RAG chat** with token-by-token streaming (Server-Sent Events).
- **Role-based access control (RBAC)** — dynamic roles (not hardcoded), per-role
  document tagging, role-admins scoped to the roles they own, and global
  superusers. Retrieval is filtered by role membership in SQL.
- **Per-role AI persona** — each role defines a persona (name, domain, scope) the
  assistant adopts, layered on top of a system-owned safety scaffold that
  role-admins cannot rewrite.
- **Document ingestion** — PDF, DOCX, Markdown, and 40+ text/source-code formats;
  load → chunk → embed (batched) → store with a pgvector HNSW index.
- **Cross-encoder reranking** — dense retrieval produces candidates; a
  cross-encoder reorders them for precision.
- **Secure auth** — JWT in HttpOnly cookies, refresh-token rotation with a
  server-side reuse-detection denylist, bcrypt password hashing.
- **Admin console** — manage users, roles, role memberships, document uploads,
  and document/role assignments from a web UI.
- **Operational hardening** — request-ID-correlated structured logging, admin
  audit log lines, rate limiting, security headers/CSP, DB-backed health check,
  and Alembic migrations applied automatically on startup.

---

## Architecture

```
Browser (Jinja2 + vanilla JS, SSE)
        │  HttpOnly cookie auth
        ▼
┌──────────────────────────────────────────────────────────┐
│ FastAPI app                                                │
│                                                            │
│  api/routers     auth · chat · admin     (+ rate limiting) │
│       │                                                    │
│  services        ChatService · IngestionService           │
│       │                                                    │
│  rag             Retriever · Reranker · RAGPipeline        │
│       │            (Embedder, prompts)                     │
│       │                                                    │
│  db/repositories  ownership- & RBAC-scoped queries         │
│       │                                                    │
│  db/models       SQLAlchemy 2.0 (async)                    │
└───────┬───────────────────────────────┬───────────────────┘
        │                                │
        ▼                                ▼
 PostgreSQL 17 + pgvector          Ollama (local LLM)
 (HNSW vector index)               qwen2.5:7b by default
```

**Layering rule:** `api → services → rag → db/repositories → db/models → core`.
Dependencies never point upward. `core/` (config, security, exceptions) is
imported everywhere and imports nothing from the app.

**Session lifecycle:** one async DB session per request (auto-commit on success,
rollback on exception). The chat path deliberately uses short-lived sessions per
phase so no DB connection is held open during slow LLM streaming.

**RBAC enforcement point:** access is enforced in the *query* (role-join /
ownership filters), not in the prompt or the UI. The persona/prompt scope is a
second layer, not the primary control.

---

## Technology Stack

| Layer | Technology |
|---|---|
| API framework | FastAPI 0.115, Uvicorn |
| Language | Python 3.11 (async/await throughout) |
| Database | PostgreSQL 17 + `pgvector` (HNSW index) |
| ORM / migrations | SQLAlchemy 2.0 (async), Alembic |
| LLM serving | Ollama (default model `qwen2.5:7b`) |
| Embeddings | `sentence-transformers` — `BAAI/bge-base-en-v1.5` (768-dim) |
| Reranking | `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| Auth | python-jose (JWT), passlib + bcrypt |
| Rate limiting | slowapi |
| Frontend | Jinja2 templates, vanilla JS, marked + DOMPurify |
| Packaging | Docker + Docker Compose |

---

## Installation

### Prerequisites
- Docker and Docker Compose, **or**
- Python 3.11, PostgreSQL 17 with the `vector` extension, and a running Ollama.

### Running with Docker (recommended)

```bash
git clone <repo-url> mnema && cd mnema
cp .env.example .env          # then edit secrets (see below)
docker compose up --build
```

This will, in order: start Postgres and wait until healthy → start Ollama and
wait until it serves HTTP → pull the configured model (`ollama-init`) → apply
Alembic migrations, seed the superuser and starter roles, then launch the app.

Open **http://localhost:8000** and sign in with the admin credentials from your
`.env`.

> **Before exposing this anywhere shared**, set a strong `SECRET_KEY` and
> `ADMIN_PASSWORD`, change the Postgres password in `docker-compose.yml`, set
> `DEBUG=false`, and set `COOKIE_SECURE=true` behind HTTPS. See
> [Security Notes](#security-notes).

### GPU acceleration (default)

Ollama runs on the GPU by default — it's dramatically faster than CPU. This
requires the **NVIDIA Container Toolkit** on the host. No extra flags are needed:

```bash
docker compose up --build
```

**No NVIDIA GPU?** Comment out the `deploy:` block on the `ollama` service in
`docker-compose.yml` to fall back to CPU (works on any host, but generating a
7B model on CPU is slow — expect multi-second-to-minute responses).

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | **required** | JWT signing key. Min 32 chars; rejected if it contains an insecure fragment. Generate: `python -c 'import secrets; print(secrets.token_urlsafe(48))'` |
| `DEBUG` | `false` | Enables SQL echo and exposes `/api/docs`. Must be `false` in production. |
| `DATABASE_URL` | `sqlite+aiosqlite:///./rag_app.db` | DB DSN. In Docker, set to the Postgres service. |
| `ALLOWED_ORIGINS` | `http://localhost:8000,...` | Comma-separated CORS origin allowlist. |
| `COOKIE_SECURE` | `false` | Set `true` behind HTTPS (also enables HSTS). |
| `COOKIE_SAMESITE` | `lax` | Cookie SameSite policy. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Access-token lifetime. |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh-token lifetime. |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint. |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Chat / title model. |
| `EMBED_MODEL` / `EMBED_DIMENSION` | `BAAI/bge-base-en-v1.5` / `768` | Embedding model — **dimension must match the model**. |
| `RERANKER_MODEL` / `RERANKER_ENABLED` | `cross-encoder/ms-marco-MiniLM-L-6-v2` / `true` | Reranking. |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `512` / `64` | Chunking. |
| `TOP_K_RETRIEVAL` / `RETRIEVAL_CANDIDATES` | `5` / `20` | Retrieval depth. |
| `MAX_UPLOAD_SIZE_MB` | `50` | Upload cap. |
| `ADMIN_USERNAME` / `ADMIN_EMAIL` / `ADMIN_PASSWORD` | `admin` / … / `ChangeMe123!` | Seeded superuser. Weak defaults are **refused** when `DEBUG=false`. |

---

## Running Locally (non-Docker)

```bash
python -m venv .venv
# Windows:   .venv\Scripts\activate
# Unix:      source .venv/bin/activate

pip install -r requirements.txt
python scripts/create_env.py          # writes .env with a strong SECRET_KEY

# In another terminal:
ollama serve
ollama pull qwen2.5:7b

# Apply migrations, then run:
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## Testing

```bash
pip install -r requirements-dev.txt
pytest                # unit tests (run against in-memory SQLite where applicable)
```

Current automated coverage focuses on unit-level logic: JWT/token rotation and
revocation, refresh flow, prompt building, schema validation, chunking,
concurrent chat streaming, and ingestion batching. See the project review for
known gaps (API/RBAC integration and end-to-end tests).

---

## Deployment

- **Migrations** run automatically on container start (`scripts/docker_entrypoint.py`),
  including a safe stamp path for pre-Alembic databases.
- **Health check** at `GET /health` verifies database connectivity (returns 503
  if the DB is unreachable); the Docker healthcheck uses it.
- **Backups** — see `scripts/backup.sh` / `scripts/backup.ps1` / `scripts/restore.sh`.
- **Logs** are JSON-friendly and request-ID correlated; admin actions emit
  `admin_audit …` lines.

For multi-instance / higher-concurrency deployments, see the scaling notes in the
project review (single Uvicorn worker, in-memory rate limiter, and serialized LLM
generation are the current limits to address first).

---

## API Documentation

Interactive docs (`/api/docs`, `/api/redoc`) are served **only when `DEBUG=true`**.

### Authentication
| Method | Path | Description |
|---|---|---|
| POST | `/auth/login` | Log in; sets HttpOnly cookies and returns tokens. |
| POST | `/auth/refresh` | Rotate the token pair (reuse-detected). |
| POST | `/auth/logout` | Clear cookies and revoke the refresh token. |
| GET | `/auth/me` | Current user + role memberships. |

### Chat (authenticated)
| Method | Path | Description |
|---|---|---|
| GET/POST | `/chat/conversations` | List / create conversations. |
| GET | `/chat/conversations/{id}` | Conversation with messages (ownership-enforced). |
| PATCH/DELETE | `/chat/conversations/{id}` | Rename / delete. |
| POST | `/chat/conversations/{id}/messages` | Send a message; SSE token stream. |
| GET | `/chat/documents` | Documents visible to the caller. |

### Admin (superuser / role-admin)
| Method | Path | Description |
|---|---|---|
| POST/GET | `/admin/users` , `/admin/users/create` | Manage users (superuser). |
| PUT/DELETE | `/admin/users/{id}/roles` , `/admin/users/{id}` | Roles / delete user. |
| GET/POST/PUT/DELETE | `/admin/roles…` | Manage roles and memberships. |
| POST/GET/PUT/DELETE | `/admin/documents…` | Upload, list, retag, delete documents. |

---

## Security Notes

- Tokens are stored in **HttpOnly cookies** (JS cannot read them); a Bearer
  header fallback supports API clients.
- Refresh tokens **rotate** on use and old `jti`s are denylisted to detect reuse.
- All authorization is enforced in the **database query** (ownership + role
  joins), with superuser bypass; the `is_superuser` JWT claim is informational
  only and re-verified against the DB on every request.
- Assistant output is rendered through **DOMPurify**; user content via
  `textContent`. A strict-ish CSP and standard security headers are sent on every
  response.
- Uploads are size-capped during read, filename-sanitized, MIME/extension
  allowlisted, and stored per-uploader with a UUID name.
- **Before production:** strong `SECRET_KEY` and `ADMIN_PASSWORD`, change the
  Postgres password, `DEBUG=false`, `COOKIE_SECURE=true` behind HTTPS, a real
  CORS allowlist, and a scheduled job to purge expired denylist rows.

---

## Screenshots

> _Add screenshots here:_ login, role-scoped chat with streaming, and the admin
> console (users / roles / document upload).

---

## Deployment, security & commercial docs

- **[DEPLOYMENT.md](DEPLOYMENT.md)** — per-scenario client install guide
  (on-prem GPU, CPU, client cloud, Apple Silicon), sizing, HTTPS networking,
  backups, and a printable checklist.
- **[docs/security-whitepaper.md](docs/security-whitepaper.md)** — security
  architecture for buyer review (query-layer RBAC, data residency, controls).
- **[legal/](legal/)** — EULA, DPA, and privacy-policy templates (drafts; have
  counsel review).
- **[docs/roadmap-sso-and-connectors.md](docs/roadmap-sso-and-connectors.md)** —
  implementation designs for SSO/OIDC and document connectors.
- **Demo in one command:** `./scripts/demo.sh` (or `scripts/demo.ps1`) brings up
  the stack and loads the bundled GitHub demo dataset.

## Roadmap

- **Done:** RBAC isolation integration test + CI pipeline; configurable
  (Redis) rate-limit store; bounded LLM generation concurrency; `/metrics`
  endpoint + optional Sentry; expired-token cleanup job; HTTPS reverse-proxy
  overlay; loopback-bound internal services.
- **Next:** OIDC SSO, then a Google Drive connector (see the design doc above).
- Multiple app workers behind the shared rate-limit store, load-tested.
- Optional managed/cloud LLM backend selectable per deployment.

---

## License

Proprietary — all rights reserved. Commercial use is governed by the End-User
License Agreement; see **[legal/eula.md](legal/eula.md)** (draft — finalize with
counsel before distribution).

## Contact

Maintainer: **levanmamoulatze@gmail.com**
