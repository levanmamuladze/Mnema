# Mnema — Verification & Inspection Guide

This guide covers three things:
1. How conversations are saved and labeled
2. How document ingestion works and how to verify it
3. How to use pgAdmin to inspect all database tables

---

## 1. How Conversations Are Saved and Labeled

### Storage
Every conversation and message is persisted in PostgreSQL across two tables:

- `conversations` — one row per session (id, user_id, title, created_at, updated_at)
- `messages` — one row per message (id, conversation_id, role, content, created_at)

When a user sends their first message, a conversation row is created immediately.
Every subsequent user and assistant message is appended to that conversation.
The `updated_at` column is bumped on every turn so the sidebar always shows
the most recently active conversation at the top.

### Auto-Labeling
Conversations are automatically named after the **first full exchange**
(user message 1 → assistant reply 1). As soon as that first reply finishes
streaming, the app spawns a background task that sends a one-shot prompt to
Ollama:

```
Generate a short, descriptive title (3-6 words) for this conversation based on the first exchange.
Respond with ONLY the title text, no quotes, no punctuation at the end.

User: <first user message>
Assistant: <first assistant reply>
```

The returned title is normalized (capitalized, trailing punctuation stripped)
and stored in `conversations.title`. Because titling runs as a fire-and-forget
background task — not inline — the chat reply is never blocked waiting on it.
The sidebar picks up the new title on its next refresh.

If Ollama is slow or returns garbage, the conversation stays as
"New conversation" — it never crashes or blocks the chat.

---

## 2. How Document Ingestion Works

### Pipeline (triggered on upload)
```
PDF / TXT file
    ↓ DocumentLoader      — extracts raw text (PyMuPDF for PDF, utf-8 for TXT)
    ↓ TextChunker         — splits into ~512 char chunks with 64 char overlap
    ↓ Embedder            — runs BAAI/bge-base-en-v1.5 (768-dim vectors) on each chunk
    ↓ DocumentChunkRepository (bulk insert — one flush per document)
    ↓ PostgreSQL: document_chunks table (content TEXT + embedding VECTOR(768))
    ↓ documents.is_indexed = True
```

The embedding model and its dimension are configured in `.env`
(`EMBED_MODEL` / `EMBED_DIMENSION`) and **must** match — 768 for
`BAAI/bge-base-en-v1.5`, 384 for `all-MiniLM-L6-v2`. The `document_chunks.embedding`
column type is fixed at table-creation time, so changing the model later
requires a migration that recreates the column.

### Role-Based Access
Roles are dynamic. A superuser creates roles (e.g. `hr`, `developer`) in the
Admin panel; each role carries a persona (`persona_name`, `domain_label`,
`scope_description`, `general_knowledge_scope`). Fresh installs are seeded with
two starter roles, `hr` and `developer`.

- **Users ↔ roles** is many-to-many via the `user_roles` table. A membership
  can optionally carry `is_role_admin = true`, which grants admin rights inside
  that one role only (manage its members, its documents, and its persona).
- **Documents ↔ roles** is many-to-many via `document_roles`, keyed by
  `role_id` (a real FK to `roles`, not a string).

At query time the retriever pulls candidate chunks restricted to documents
tagged with at least one of the user's roles:
```sql
SELECT dc.content, dc.embedding <=> :query_vec AS distance
FROM document_chunks dc
WHERE dc.document_id IN (
        SELECT dr.document_id
        FROM document_roles dr
        WHERE dr.role_id IN (:user_role_ids)   -- the user's role memberships
      )
  AND dc.embedding IS NOT NULL
ORDER BY distance
LIMIT 20;            -- candidate pool, then reranked down to TOP_K_RETRIEVAL (5)
```

Superusers bypass this filter and see all chunks from all documents.
When reranking is enabled (`RERANKER_ENABLED=true`), the candidate pool is
re-scored by a cross-encoder (`cross-encoder/ms-marco-MiniLM-L-6-v2`) and
trimmed to the final top-K.

### Verifying a document was indexed
After uploading, the Admin panel shows **Indexed: Yes** in the document table.
You can also check in pgAdmin (see Section 3).

---

## 3. pgAdmin — How to Connect and Inspect Tables

### Step 1 — Start pgAdmin
pgAdmin is included in docker-compose.yml and starts automatically with
the stack:

```powershell
docker compose up -d
```

To start just the database and pgAdmin (no app/Ollama), run:

```powershell
docker compose up -d db pgadmin
```

Open your browser and go to: **http://localhost:5050**

### Step 2 — Login
| Field    | Value            |
|----------|------------------|
| Email    | admin@admin.com  |
| Password | admin            |

(These are set in docker-compose.yml via `PGADMIN_DEFAULT_EMAIL` /
`PGADMIN_DEFAULT_PASSWORD`. The short password and the master-password prompt
are intentionally relaxed there for local use.)

### Step 3 — Open the pre-registered server
A server named **Mnema DB** is pre-registered automatically (via
`pgadmin/servers.json`), so you don't need to add it by hand. In the left
panel, expand:

`Servers → Mnema DB`

The first time you expand it, pgAdmin asks for the database password. Enter:

| Field    | Value      |
|----------|------------|
| Password | `postgres` |

Tick **Save Password** so you aren't asked again, then continue expanding:
`Mnema DB → Databases → app → Schemas → public → Tables`

You will see all 10 application tables listed below (plus `alembic_version`,
which Alembic uses to track the current migration revision).

> **If "Mnema DB" is missing** (e.g. you have an old `pgadmin_data` volume),
> register it manually: right-click **Servers → Register → Server…**, then on
> the **Connection** tab use Host `db`, Port `5432`, Maintenance DB `app`,
> Username `app`, Password `postgres`. (`db` is the Docker Compose service
> name — pgAdmin reaches Postgres over the internal `mnema_net` network, not
> via `localhost`.)

---

## 4. Table-by-Table Inspection

The core application tables are:
`users`, `roles`, `user_roles`, `documents`, `document_roles`,
`document_chunks`, `conversations`, `messages`, `revoked_tokens`.

For each table, right-click it → **View/Edit Data → All Rows** to see the data.
Or open the **Query Tool** (Tools menu) and paste the SQL below.

---

### `users` — All registered accounts

```sql
SELECT id, username, email, is_superuser, is_active, created_at
FROM users
ORDER BY created_at DESC;
```

**What to check:**
- The seeded admin exists with `is_superuser = true`
- `is_active = true` for accounts that should be able to log in
- No duplicate usernames or emails

> Note: there is no `role` or `is_admin` column anymore. Roles live in
> `user_roles` (see below); the old global `is_admin` is now `is_superuser`.

---

### `roles` — Defined teams / access tiers

```sql
SELECT id, name, display_name, persona_name, domain_label, created_at
FROM roles
ORDER BY id;
```

**What to check:**
- Fresh installs have the `hr` and `developer` starter roles
- Each role has a non-empty persona (`persona_name`, `domain_label`,
  `scope_description`, `general_knowledge_scope`)

---

### `user_roles` — Which users belong to which roles

```sql
SELECT
    u.username,
    r.name        AS role,
    ur.is_role_admin,
    ur.created_at
FROM user_roles ur
JOIN users u ON u.id = ur.user_id
JOIN roles r ON r.id = ur.role_id
ORDER BY u.username, r.name;
```

**What to check:**
- Every non-superuser has at least one role (otherwise they retrieve nothing)
- `is_role_admin = true` only where someone should manage that specific role
- Superusers may have no rows here at all — they bypass roles entirely

---

### `conversations` — All chat sessions

```sql
SELECT
    c.id,
    u.username,
    c.title,
    c.created_at,
    c.updated_at,
    COUNT(m.id) AS message_count
FROM conversations c
JOIN users u ON u.id = c.user_id
LEFT JOIN messages m ON m.conversation_id = c.id
GROUP BY c.id, u.username, c.title, c.created_at, c.updated_at
ORDER BY c.updated_at DESC;
```

**What to check:**
- `title` is populated after the first exchange (auto-named by AI)
- `updated_at` changes every time a new message is sent
- `message_count` grows correctly

---

### `messages` — Every message in every conversation

```sql
SELECT
    m.id,
    m.conversation_id,
    u.username,
    m.role,
    LEFT(m.content, 120) AS content_preview,
    m.created_at
FROM messages m
JOIN conversations c ON c.id = m.conversation_id
JOIN users u ON u.id = c.user_id
ORDER BY m.created_at DESC
LIMIT 50;
```

**What to check:**
- Messages alternate between `role = 'user'` and `role = 'assistant'`
- Content is not empty
- Timestamps are in correct order within each conversation

---

### `documents` — Uploaded files

```sql
SELECT
    d.id,
    d.original_filename,
    d.file_size,
    d.mime_type,
    d.is_indexed,
    d.uploaded_at,
    u.username AS uploaded_by
FROM documents d
JOIN users u ON u.id = d.uploaded_by_admin
ORDER BY d.uploaded_at DESC;
```

**What to check:**
- `is_indexed = true` for every document (means embedding pipeline ran)
- `file_size > 0`
- Correct uploader shown

---

### `document_roles` — Which roles can access each document

```sql
SELECT
    d.original_filename,
    r.name AS role
FROM document_roles dr
JOIN documents d ON d.id = dr.document_id
JOIN roles    r ON r.id = dr.role_id
ORDER BY d.id;
```

**What to check:**
- Each document has at least one role assigned (otherwise only superusers see it)
- Roles resolve to real rows in `roles` (the join returns a name, not null)

---

### `document_chunks` — The actual indexed content + embeddings

```sql
SELECT
    dc.id,
    d.original_filename,
    dc.chunk_index,
    LEFT(dc.content, 200) AS chunk_preview,
    CASE WHEN dc.embedding IS NULL THEN 'MISSING' ELSE 'OK' END AS embedding_status
FROM document_chunks dc
JOIN documents d ON d.id = dc.document_id
ORDER BY d.id, dc.chunk_index;
```

**Count chunks per document:**
```sql
SELECT
    d.original_filename,
    COUNT(dc.id) AS chunk_count,
    COUNT(dc.embedding) AS embedded_count
FROM documents d
LEFT JOIN document_chunks dc ON dc.document_id = d.id
GROUP BY d.id, d.original_filename
ORDER BY d.id;
```

**What to check:**
- `embedded_count` equals `chunk_count` for every document
- Each `embedding` is a 768-dim vector (matches `EMBED_DIMENSION`)
- If `embedded_count = 0` the document was not indexed — re-upload it
- Typical chunk count: 5–50 per page of text

---

## 5. Quick Health Check Query

Run this single query to get a full system snapshot:

```sql
SELECT
    (SELECT COUNT(*) FROM users)                          AS total_users,
    (SELECT COUNT(*) FROM users WHERE is_superuser)       AS superusers,
    (SELECT COUNT(*) FROM roles)                          AS total_roles,
    (SELECT COUNT(*) FROM user_roles)                     AS role_memberships,
    (SELECT COUNT(*) FROM documents)                      AS total_documents,
    (SELECT COUNT(*) FROM documents WHERE is_indexed)     AS indexed_documents,
    (SELECT COUNT(*) FROM document_chunks)                AS total_chunks,
    (SELECT COUNT(*) FROM document_chunks WHERE embedding IS NOT NULL) AS embedded_chunks,
    (SELECT COUNT(*) FROM conversations)                  AS total_conversations,
    (SELECT COUNT(*) FROM messages)                       AS total_messages;
```

A healthy system shows `total_chunks = embedded_chunks` and
`indexed_documents = total_documents`.

---

## 6. Troubleshooting Common Issues

### "I don't have relevant information" even after uploading
1. Check `documents.is_indexed = true` in pgAdmin
2. Check `embedded_count = chunk_count` in the chunks query above
3. If chunks exist but embedding_status is MISSING, re-upload the document
4. Confirm the document is tagged with a role the user belongs to
   (`document_roles` joined to the user's `user_roles`), or log in as a superuser
5. If you are the admin, confirm `is_superuser = true` in the users table

### Conversations not showing in sidebar
1. Check `conversations` table — is the row there?
2. Check that `user_id` matches the logged-in user's `id`
3. Try `docker compose restart app` to clear any cached state

### Title stuck as "New conversation"
1. The auto-naming triggers after the **1st exchange** (first user message +
   first assistant reply) and runs as a background task — give the sidebar a
   moment to refresh
2. Check Ollama is running: `docker exec -it mnema_ollama ollama list`
3. If Ollama is slow, the title just stays as-is — it never blocks chat

### Can't reach pgAdmin / can't connect to the database
1. Confirm both containers are healthy: `docker compose ps`
2. pgAdmin is at **http://localhost:5050** (the host port), but inside pgAdmin
   the database Host is `db` (the service name), **not** `localhost`
3. The DB credentials are user `app` / password `postgres` / database `app`

---

## 7. pgAdmin Tips

- **Query Tool**: Tools → Query Tool (or press F5) — write and run any SQL
- **Export to CSV**: In Query Tool results, click the download icon
- **Filter rows**: Right-click table → View/Edit Data → All Rows, then use the filter bar at the top
- **Auto-refresh**: Right-click a table → Properties → set refresh interval
- **ERD**: Right-click the database → Generate ERD — shows all tables and relationships visually
