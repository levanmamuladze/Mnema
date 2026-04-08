# Onboarding AI — Verification & Inspection Guide

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
Conversations are automatically named after the **second full exchange**
(user message 1 → assistant reply 1 → user message 2). At that point the
system sends a one-shot prompt to Ollama:

```
Generate a short, descriptive title (3-6 words) for this conversation.
Respond with ONLY the title text, no quotes, no punctuation at the end.

User: <first user message>
Assistant: <first assistant reply>
```

The returned title is stored in `conversations.title` and sent back to the
browser via a `title_update` SSE event so the sidebar updates instantly
without a page reload.

If Ollama is slow or returns garbage, the conversation stays as
"New conversation" — it never crashes or blocks the chat.

---

## 2. How Document Ingestion Works

### Pipeline (triggered on upload)
```
PDF / TXT file
    ↓ DocumentLoader      — extracts raw text (PyMuPDF for PDF, utf-8 for TXT)
    ↓ TextChunker         — splits into ~512 char chunks with 64 char overlap
    ↓ Embedder            — runs all-MiniLM-L6-v2 (384-dim vectors) on each chunk
    ↓ DocumentChunkRepository.create_chunk()
    ↓ PostgreSQL: document_chunks table (content TEXT + embedding VECTOR(384))
    ↓ documents.is_indexed = True
```

### Role-Based Access
When a document is uploaded, admin assigns it to one or more roles
(`hr`, `developer`). This creates rows in `document_roles`.

At query time the retriever does:
```sql
SELECT dc.*, dc.embedding <=> $query_vec AS distance
FROM document_chunks dc
JOIN documents d ON d.id = dc.document_id
JOIN document_roles dr ON dr.document_id = d.id
WHERE dr.role = $user_role           -- enforced at SQL level
  AND dc.embedding IS NOT NULL
ORDER BY distance
LIMIT 5
```

Admins bypass this filter and see all chunks from all documents.

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

Open your browser and go to: **http://localhost:5050**

### Step 2 — Login
| Field    | Value            |
|----------|------------------|
| Email    | admin@admin.com  |
| Password | admin            |

### Step 3 — Register the database server
1. In the left panel, right-click **Servers** → **Register → Server…**
2. Fill in the **General** tab:
   - Name: `Onboarding DB`
3. Click the **Connection** tab and fill in:

| Field            | Value      |
|------------------|------------|
| Host             | `db`       |
| Port             | `5432`     |
| Maintenance DB   | `app`      |
| Username         | `app`      |
| Password         | `postgres` |

4. Click **Save**

The server will appear in the left panel. Expand:
`Onboarding DB → Databases → app → Schemas → public → Tables`

You will see all 7 tables listed below.

---

## 4. Table-by-Table Inspection

For each table, right-click it → **View/Edit Data → All Rows** to see the data.
Or open the **Query Tool** (Tools menu) and paste the SQL below.

---

### `users` — All registered accounts

```sql
SELECT id, username, email, role, is_admin, is_active, created_at
FROM users
ORDER BY created_at DESC;
```

**What to check:**
- Admin user exists with `is_admin = true` and `role = 'admin'`
- HR/developer users have correct roles
- No duplicate usernames or emails

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
- `title` is populated after the second exchange (auto-named by AI)
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
    dr.role
FROM document_roles dr
JOIN documents d ON d.id = dr.document_id
ORDER BY d.id;
```

**What to check:**
- Each document has at least one role assigned
- Roles are either `hr` or `developer`

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
- If `embedded_count = 0` the document was not indexed — re-upload it
- Typical chunk count: 5–50 per page of text

---

### `learning_progress` — Per-user topic tracking

```sql
SELECT
    u.username,
    lp.topic,
    ROUND(lp.progress::numeric, 1) AS progress_pct,
    lp.updated_at
FROM learning_progress lp
JOIN users u ON u.id = lp.user_id
ORDER BY u.username, lp.progress DESC;
```

**What to check:**
- Progress increases after chat sessions
- Topics match the content of uploaded documents
- Values stay between 0 and 100

---

## 5. Quick Health Check Query

Run this single query to get a full system snapshot:

```sql
SELECT
    (SELECT COUNT(*) FROM users)              AS total_users,
    (SELECT COUNT(*) FROM users WHERE is_admin) AS admins,
    (SELECT COUNT(*) FROM documents)          AS total_documents,
    (SELECT COUNT(*) FROM documents WHERE is_indexed) AS indexed_documents,
    (SELECT COUNT(*) FROM document_chunks)    AS total_chunks,
    (SELECT COUNT(*) FROM document_chunks WHERE embedding IS NOT NULL) AS embedded_chunks,
    (SELECT COUNT(*) FROM conversations)      AS total_conversations,
    (SELECT COUNT(*) FROM messages)           AS total_messages,
    (SELECT COUNT(*) FROM learning_progress)  AS progress_records;
```

A healthy system shows `total_chunks = embedded_chunks` and
`indexed_documents = total_documents`.

---

## 6. Troubleshooting Common Issues

### "I don't have relevant information" even after uploading
1. Check `documents.is_indexed = true` in pgAdmin
2. Check `embedded_count = chunk_count` in the chunks query above
3. If chunks exist but embedding_status is MISSING, re-upload the document
4. If you are admin, confirm `is_admin = true` in the users table

### Conversations not showing in sidebar
1. Check `conversations` table — is the row there?
2. Check that `user_id` matches the logged-in user's `id`
3. Try `docker compose restart app` to clear any cached state

### Title stuck as "New conversation"
1. The auto-naming triggers after the **2nd exchange** (4 messages total)
2. Check Ollama is running: `docker exec -it ollama ollama list`
3. If Ollama is slow, the title just stays as-is — it never blocks chat

---

## 7. pgAdmin Tips

- **Query Tool**: Tools → Query Tool (or press F5) — write and run any SQL
- **Export to CSV**: In Query Tool results, click the download icon
- **Filter rows**: Right-click table → View/Edit Data → All Rows, then use the filter bar at the top
- **Auto-refresh**: Right-click a table → Properties → set refresh interval
- **ERD**: Right-click the database → Generate ERD — shows all tables and relationships visually
