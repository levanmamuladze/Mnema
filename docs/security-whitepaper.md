# Mnema Security Whitepaper

**Audience:** IT, security, and compliance reviewers evaluating Mnema for
internal use.
**Version:** 1.0 · **Last updated:** 2026-06-01

This document describes how Mnema protects your data and enforces access
control. It is written to answer the questions a security or compliance team
asks before approving an internal AI tool.

---

## 1. Executive summary

Mnema is a self-hosted AI assistant that answers employee questions using your
own documents. Its two defining security properties:

1. **No data goes to a third-party AI provider.** The language model runs
   locally (Ollama). Your documents, questions, and answers are processed on
   infrastructure you control and are never sent to OpenAI, Anthropic, or any
   external AI service.
2. **Access control is enforced in the database query, not in the prompt.** A
   user can only ever retrieve content from documents tagged with one of their
   roles. This boundary is applied as a SQL filter *before* the model sees any
   text — so it cannot be bypassed by prompt injection or jailbreaking.

The second property is verified by an automated isolation test that runs in CI
on every change (see §4).

---

## 2. Data residency and the trust boundary

| Concern | How Mnema addresses it |
|---|---|
| Where documents are stored | In your PostgreSQL database, on your server. |
| Where the AI runs | Locally via Ollama, on your server/GPU. No external API calls. |
| What leaves your network | Nothing related to document content. With an on-prem or air-gapped deployment, nothing leaves at all. |
| Third-party processors | None for inference. (You choose where to host: on-prem, or your own cloud tenant.) |

Mnema makes **no outbound calls to any AI service**. The only external network
dependency is the one-time download of open-source embedding/LLM model weights
on first install (which can be pre-staged for fully air-gapped installs).

> **Honest scope note:** "no third-party AI provider" holds in every deployment.
> "Data never leaves your premises" holds for on-prem/air-gapped installs. If
> you host in your own cloud tenant, data stays within *your* cloud security
> boundary but physically resides in that cloud region — see the deployment
> guide for the distinction.

---

## 3. Access control (RBAC)

### 3.1 Model

- **Roles** are created by administrators (e.g. HR, Legal, Engineering). They
  are dynamic, not hardcoded.
- **Documents** are tagged with one or more roles. A user sees a document only
  if they are a member of at least one of its roles (set union).
- **Users** are assigned role memberships. A user with no roles retrieves
  nothing (fail-closed).
- **Role-admins** manage only the roles they own (their documents, members, and
  persona). **Superusers** bypass role filters for administration.

### 3.2 Enforcement point — the key design decision

Retrieval is filtered **in the SQL query** that fetches document chunks. The
relevant query restricts results to chunks whose document is tagged with one of
the caller's role IDs (`DocumentChunkRepository.get_top_chunks_for_roles`). The
language model is only ever handed text that already passed this filter.

This matters because the common alternative — instructing the model "do not
reveal documents the user shouldn't see" — is **not a security control**. It can
be defeated by prompt injection. Mnema's prompt-level persona scoping is a
*secondary* refinement, never the primary boundary.

```
User question
   │
   ▼
Embed query ──► SQL retrieval  ◄── RBAC filter applied HERE (role-join)
   │                 │              (the model never sees filtered-out chunks)
   │                 ▼
   │         Allowed chunks only
   ▼                 │
Local LLM (Ollama) ◄─┘  ──► streamed answer
```

### 3.3 Conversation privacy

A user's conversations and messages are private to that user, enforced by
ownership filters (`user_id`) at the query layer. Role-admin or superuser status
does **not** grant visibility into other users' conversations.

---

## 4. Verifiable isolation (not just a claim)

The isolation guarantee is covered by an automated integration test
(`tests/test_rbac_isolation.py`) that runs against a real PostgreSQL + pgvector
database in CI. The decisive case deliberately makes a **forbidden** document
the single closest vector match to the query and asserts that a user without the
matching role **still never receives it** — proving the access boundary wins
over semantic similarity. The suite also verifies:

- each role sees only its own documents;
- a multi-role user sees the union of their roles' documents;
- a user with no roles retrieves nothing (fail-closed);
- superusers see the whole corpus.

CI (`.github/workflows/ci.yml`) runs this on every push and pull request, so a
regression that weakened isolation would fail the build.

---

## 5. Authentication and session security

- **Passwords** are hashed with bcrypt; plaintext is never stored.
- **Tokens** are JWTs (HS256) delivered in **HttpOnly cookies**, so client-side
  JavaScript cannot read them (mitigates token theft via XSS).
- **Refresh tokens rotate** on every use; the consumed token's identifier is
  added to a server-side denylist. Replaying an old refresh token is detected
  and rejected (reuse detection).
- **The `is_superuser` claim in a JWT is informational only** — privilege is
  re-verified against the database on every request.
- **Secret-key hardening:** the application refuses to start with a weak or
  example signing key (minimum length and known-insecure-fragment checks).

---

## 6. Application security controls

- **Transport:** designed to run behind HTTPS (the shipped Caddy reverse-proxy
  overlay terminates TLS); HSTS is emitted when secure cookies are enabled.
- **Security headers:** `X-Content-Type-Options`, `X-Frame-Options: DENY`,
  `Referrer-Policy`, and a Content-Security-Policy are sent on every response.
- **Output handling:** assistant output is sanitized with DOMPurify before
  rendering; user content is inserted as text, not HTML.
- **CORS:** an explicit origin allowlist (no wildcards).
- **Rate limiting:** per-client request limits (configurable; Redis-backed for
  multi-worker deployments).
- **Uploads:** size-capped, filename-sanitized, extension/MIME-allowlisted, and
  stored under server-generated UUID names.
- **Network exposure:** by default PostgreSQL and the LLM are bound to loopback
  and never published to the network; only the HTTPS proxy is exposed.

---

## 7. Data handling and operations

- **Encryption at rest:** provided by your storage/disk-encryption layer (e.g.
  LUKS on-prem, or your cloud provider's volume encryption). Mnema stores data
  in standard PostgreSQL and a files volume that inherit that encryption.
- **Backups:** database and upload backups are supported via the included
  scripts; you control retention and storage location.
- **Logging:** structured, request-ID-correlated logs; administrative actions
  emit audit log lines. Logs do not transmit off-host unless you configure an
  optional error-tracking integration (Sentry), which is disabled by default.
- **Migrations:** schema changes are versioned (Alembic) and applied on startup.

---

## 8. Known limitations (full disclosure)

We list these so your review is accurate:

- **Single-tenant by design.** Each customer runs an isolated deployment; there
  is no shared multi-tenant instance.
- **SSO/SAML is not yet built.** Authentication is username/password managed by
  an administrator. SSO is on the roadmap.
- **Horizontal scale.** The default deployment is a single application worker
  with local rate-limit state and bounded LLM concurrency. Multi-worker scaling
  (shared rate-limit store, request queue) is supported in configuration but
  should be load-tested for large rollouts.
- **No formal third-party certification yet** (e.g. SOC 2). The controls above
  are implemented; independent attestation is a future step.

---

## 9. Deployment hardening checklist (for your team)

- [ ] Strong `SECRET_KEY` and admin password set; `DEBUG=false`.
- [ ] Database password changed from any default.
- [ ] HTTPS in front (Caddy overlay) with `COOKIE_SECURE=true`.
- [ ] `ALLOWED_ORIGINS` set to your real URL only.
- [ ] PostgreSQL and Ollama not exposed to the network (loopback/internal only).
- [ ] Disk encryption enabled on the host/volume.
- [ ] Backups scheduled and a restore tested.
- [ ] Access reviewed: roles, memberships, and document tags match policy.

---

*This whitepaper describes the product's security architecture and is provided
for evaluation. It is not a warranty. For contractual terms see the license and
data-processing agreement.*
