# Onboarding AI — Refactored

A production-ready AI onboarding assistant built with FastAPI, RAG, and Ollama.

## Quick Start

### 1. Create virtual environment

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Mac/Linux:
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Set up environment

```bash
python scripts/create_env.py   # generates .env with a random SECRET_KEY
# Then edit .env if you need to change DATABASE_URL or other settings
```

### 4. Start Ollama

```bash
ollama serve
ollama pull llama3.1
```

### 5. Create the admin user

```bash
python scripts/seed_admin.py
# Default: username=admin, email=admin@company.com, password=Admin123!
# Custom:  python scripts/seed_admin.py --username myname --email me@co.com --password MyPass1!
```

### 6. Run the app

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Open http://localhost:8000 — log in with your admin credentials.

---

## Project Structure

```
onboarding_refactored/
├── app/
│   └── main.py                  # FastAPI factory + lifespan
├── core/
│   ├── config.py                # All settings via pydantic-settings
│   ├── security.py              # JWT + bcrypt
│   └── exceptions.py            # Typed HTTP exceptions
├── db/
│   ├── models.py                # SQLAlchemy models (User, Document, etc.)
│   ├── session.py               # Async engine + get_session dependency
│   └── repositories/
│       ├── base.py              # Generic CRUD repository
│       ├── user_repository.py
│       ├── conversation_repository.py
│       └── document_repository.py
├── api/
│   ├── deps.py                  # get_current_user, require_admin
│   ├── routers/
│   │   ├── auth.py              # POST /auth/login, /auth/refresh, GET /auth/me
│   │   ├── chat.py              # GET/POST /chat/conversations, streaming messages
│   │   └── admin.py             # User + document management
│   └── schemas/
│       ├── auth.py
│       ├── user.py
│       ├── chat.py
│       └── document.py
├── services/
│   ├── chat_service.py          # Orchestrates RAG + message persistence
│   └── ingestion_service.py     # load → chunk → embed → store
├── rag/
│   ├── embedder.py              # SentenceTransformer wrapper (async-safe)
│   ├── retriever.py             # Cosine similarity retrieval
│   ├── prompts.py               # Prompt builder
│   └── pipeline.py              # Streams tokens from Ollama
├── ingestion/
│   ├── loader.py                # PDF + text file loader
│   └── chunker.py               # Overlap-aware text splitter
├── frontend/
│   ├── templates/               # Jinja2: base, index (login), chat, admin
│   └── static/                  # CSS + JS
├── scripts/
│   ├── create_env.py            # Generates .env from .env.example
│   ├── seed_admin.py            # Creates first admin user
│   └── index_documents.py       # Re-index documents
├── uploads/                     # Uploaded files (gitignored)
├── .env.example                 # Template — commit this
├── .env                         # Your secrets — never commit
└── requirements.txt
```

---

## API Endpoints

### Authentication
| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/login` | Login, returns access + refresh tokens |
| POST | `/auth/refresh` | Refresh access token |
| GET | `/auth/me` | Get current user info |

### Chat (authenticated)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/chat/conversations` | List user's conversations |
| POST | `/chat/conversations` | Create new conversation |
| GET | `/chat/conversations/{id}` | Get conversation with messages |
| POST | `/chat/conversations/{id}/messages` | Send message (SSE stream) |
| GET | `/chat/learning-insights` | Get user's learning insights |

### Admin only
| Method | Path | Description |
|--------|------|-------------|
| POST | `/admin/users/create` | Create a new user |
| GET | `/admin/users` | List all users |
| POST | `/admin/documents/upload` | Upload + index a document |
| GET | `/admin/documents` | List all documents |
| DELETE | `/admin/documents/{id}` | Delete a document |

### Other
| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/api/docs` | Swagger UI |
| GET | `/api/redoc` | ReDoc UI |

---

## Configuration

All config lives in `.env`. Key variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | (required) | JWT signing key — min 32 chars |
| `DATABASE_URL` | `sqlite+aiosqlite:///./rag_app.db` | DB connection string |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | `llama3.1` | Model to use |
| `EMBED_MODEL` | `BAAI/bge-base-en-v1.5` | Sentence transformer model |
| `CHUNK_SIZE` | `512` | Characters per chunk |
| `CHUNK_OVERLAP` | `64` | Overlap between chunks |
| `TOP_K_RETRIEVAL` | `5` | Number of chunks to retrieve |
| `MAX_UPLOAD_SIZE_MB` | `50` | Max file upload size |
| `DEBUG` | `false` | Enables SQL logging + CORS wildcard |

### Switch to PostgreSQL

```env
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/rag_app
```

Then run:
```sql
CREATE DATABASE rag_app;
```

---

## Re-indexing Documents

If you add documents directly to the DB or need to re-embed:

```bash
# Index only unindexed documents
python scripts/index_documents.py

# Force re-index everything
python scripts/index_documents.py --all
```

---

## Architecture Notes

**Dependency direction:** `api` → `services` → `db/repositories` → `db/models` → `core`  
Never import upward. `core/` is imported by all layers but imports nothing from them.

**Session lifecycle:** One `AsyncSession` per request, auto-committed on success, rolled back on exception. Never share sessions across requests.

**Streaming:** Chat responses use Server-Sent Events (SSE). The frontend reads `data:` lines and appends tokens to the bubble in real time.

**Embeddings:** The `SentenceTransformer` model is loaded once at startup (via `@lru_cache`) and reused. Embedding runs in a thread pool executor so it doesn't block the async event loop.
