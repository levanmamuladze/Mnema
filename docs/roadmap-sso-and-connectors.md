# Implementation designs: SSO/SAML and document connectors

These two features require integrating with **external systems** (an identity
provider; third-party content APIs) and can only be built responsibly with a
real IdP/credentials to test against. Rather than ship untested
authentication/integration code, this document is an implementation-ready
design for each, so the work can be done in a focused session when you have a
concrete customer/IdP to target.

Both fit the existing layering (`api → services → rag → db/repositories →
db/models → core`) and the query-layer RBAC model.

---

## A. SSO / SAML (and OIDC)

### Why
Mid-market and enterprise IT expect "log in with our Google/Microsoft/Okta."
It's a hard procurement gate above ~100 seats. Today auth is username/password
managed by an admin (`core/security.py`, `api/routers/auth.py`).

### Recommended approach
Support **OpenID Connect (OIDC)** first — it covers Google Workspace, Microsoft
Entra ID (Azure AD), and Okta with one code path and is much simpler than SAML.
Add **SAML 2.0** only if a specific customer requires it.

Use a maintained library rather than hand-rolling:
- OIDC: `authlib` (integrates cleanly with FastAPI/Starlette).
- SAML (if needed): `python3-saml`.

### Data model changes (Alembic migration)
Add to `users`:
- `auth_provider` (`"local" | "oidc" | "saml"`, default `"local"`)
- `external_subject` (the IdP's stable user id / `sub`), nullable, unique per
  provider
- make `hashed_password` nullable (SSO users have none)

Add a settings table or env config for the provider:
- `SSO_ENABLED`, `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`,
  `OIDC_REDIRECT_URL`, plus an optional **email-domain → default role** mapping
  for just-in-time provisioning.

### New routes (`api/routers/auth.py`)
- `GET /auth/sso/login` → redirect to the IdP authorize URL (Authlib).
- `GET /auth/sso/callback` → validate the IdP response, find-or-create the user
  by `external_subject`, then **issue the same JWT HttpOnly cookies the local
  flow already issues** (reuse `core/security.py`). This is the key point: SSO
  changes only *authentication*; the existing session, RBAC, and cookie logic
  are untouched downstream.

### Role assignment
SSO authenticates; it does not authorize. Two supported modes:
1. **JIT provisioning** with a default role from email domain (configurable),
   then an admin refines roles. (Simplest.)
2. **Group mapping**: map IdP groups/claims → Mnema roles. (Add a
   `role_mappings` table; needs the IdP to send group claims.)

Keep RBAC enforcement exactly where it is (query layer). SSO must never bypass
the role filter.

### Security checklist
- Validate `state`/`nonce` (CSRF/replay) — Authlib handles this if used
  correctly.
- Verify ID-token signature, `iss`, `aud`, `exp`.
- Keep local admin login available as a break-glass path (config flag), so a
  misconfigured IdP can't lock everyone out.

### Testing
- Unit-test the callback's find-or-create + role-assignment logic with a **mock
  IdP token** (no network).
- Integration-test against a containerized IdP (e.g. Keycloak) in CI for the
  full redirect/callback loop.
- Add an RBAC test asserting an SSO-provisioned user with role A cannot retrieve
  role B documents (reuse `tests/test_rbac_isolation.py` patterns).

### Effort
OIDC: ~2–4 focused days incl. tests. SAML: add ~2–3 days if required.

---

## B. Document connectors (Slack / Google Drive / Confluence / Notion)

### Why
Today documents are uploaded manually (single file or folder). Connectors keep
the knowledge base fresh automatically and increase stickiness and deal size.
This is a **growth** feature, not a launch blocker.

### Architecture (fits the existing ingestion pipeline)
The ingestion path already is: `load → chunk → embed → bulk insert`
(`services/ingestion_service.py`, `ingestion/loader.py`). A connector is just a
**new source that produces files/text + metadata** feeding the same pipeline.

Define a `Connector` interface:
```
class Connector(Protocol):
    async def list_documents(self) -> list[SourceDoc]: ...
    async def fetch(self, doc: SourceDoc) -> bytes | str: ...
```
Each `SourceDoc` carries a stable `external_id`, title, mime type, modified
time, and (where available) a source ACL hint used to suggest role tagging.

### Data model changes
Add to `documents`:
- `source` (`"upload" | "gdrive" | "slack" | "confluence" | "notion"`)
- `external_id` (stable id in the source), `source_updated_at`
- a unique constraint on `(source, external_id)` so re-syncs **upsert** instead
  of duplicating.

Add a `connector_configs` table (per-connector credentials/cursor), with secrets
encrypted at rest (or referenced from a secrets manager).

### Sync model
- A background sync job (manual "Sync now" button + optional schedule) calls
  `list_documents`, diffs against stored `external_id`/`source_updated_at`,
  fetches changed docs, and runs the existing ingest. Deletions at source →
  soft-delete chunks.
- Run syncs through the existing batched ingestion (already optimized:
  one flush per document).

### The hard part: access mapping
A connector must **not** silently widen access. Default to **admin-assigned
role tagging** at connect time (the admin picks which role(s) a connected
source maps to). Only attempt automatic ACL mapping (e.g. Google Drive
permissions → roles) as an explicit, reviewed feature — and never let it grant
broader visibility than the admin approved. RBAC stays enforced at the query
layer regardless of source.

### Per-connector notes
- **Google Drive:** OAuth, `files.list` + `files.export`, page through changes
  API for incremental sync. Native Google Docs export to text/markdown.
- **Confluence:** REST API, space → role mapping is natural.
- **Notion:** API, database/page traversal; rate limits matter.
- **Slack:** treat channels as sources; be deliberate about which channels and
  about PII.

### Testing
- Unit-test the diff/upsert logic with a fake connector (no network).
- Contract-test each connector against recorded fixtures (VCR-style).
- RBAC test: connected documents respect role tags exactly like uploads.

### Effort
First connector (Google Drive) incl. the connector framework + upsert + sync UI:
~1–2 weeks. Each additional connector: ~3–5 days.

---

## Suggested order
1. **OIDC SSO** — unblocks larger deals; small, contained, high-value.
2. **Google Drive connector** — the most-requested source; establishes the
   connector framework the others reuse.
3. SAML and additional connectors **on customer demand**, not speculatively.
