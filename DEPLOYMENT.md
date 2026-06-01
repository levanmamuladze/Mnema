# Mnema — Client Deployment Guide

How to install Mnema for a paying client, end to end, for every realistic
hardware and hosting scenario.

The golden rule that shapes everything here:

> **You install Mnema onto infrastructure the *client* owns. You never become
> the host.** This keeps the privacy promise honest ("the AI runs locally, your
> data never goes to a third-party AI provider") and keeps data-processing
> liability off you. The one combination to refuse is hosting a client's
> documents on a VM *you* rent and control.

---

## 0. Pick the scenario

Ask the client two questions before anything else:

1. **"Where should this live — a server in your office, or your own cloud
   account?"**
2. **"Is there an NVIDIA GPU available (or can one be added / rented)?"**

Their answers route you to one of these:

| Scenario | Hosting | GPU | Model | Experience | Use when |
|---|---|---|---|---|---|
| **A** | On-prem server | NVIDIA | `qwen2.5:14b` | Fast, sharp | **Default / premium.** Client has or can add a GPU box. |
| **B** | On-prem server | None (CPU) | `qwen2.5:7b` or `3b` | Slow, weaker | Budget; light, patient use; existing non-GPU hardware. |
| **C** | Client's cloud account | NVIDIA (cloud) | `qwen2.5:14b` | Fast, sharp | No local hardware, cloud-friendly client. |
| **D** | Apple Silicon Mac | Apple GPU (Metal) | `qwen2.5:14b` | Fast | Mac shop, no NVIDIA. **Custom topology — see notes.** |

> **Never:** CPU + 14B (30 s–2 min per answer = an unhappy client), or a VM you
> own holding their data.

---

## 1. Hardware sizing

The hard constraint is **GPU VRAM**, not employee count. For ~20 people, peak
*concurrent* generations are only 2–4 at once.

### Scenario A / C / D — with a GPU (recommended)

| Resource | 14B (recommended) | Notes |
|---|---|---|
| **GPU VRAM** | **16 GB minimum, 24 GB ideal** | `qwen2.5:14b` (Q4) ≈ 10–11 GB loaded; embedder + reranker add 1–2 GB. 24 GB removes all worry. |
| **CPU** | 8 cores | Embedding, Postgres, app. |
| **RAM** | 32 GB (48–64 GB nicer) | Sentence-transformer models run in CPU RAM. |
| **Disk** | 120 GB SSD | Model ~9 GB + HF cache ~2 GB + Postgres + docs + index. |

Good NVIDIA cards: RTX 4060 Ti 16GB / RTX 4080 (16 GB), RTX 4090 / A5000 / L4 (24 GB).

### Scenario B — CPU only (budget)

| Resource | Spec | Notes |
|---|---|---|
| **CPU** | 16+ cores | More cores ≈ faster generation. |
| **RAM** | 32–64 GB | Model runs entirely in RAM. |
| **Disk** | 100 GB SSD | |
| **Model** | `qwen2.5:7b` (tolerable) or `qwen2.5:3b` / `llama3.2:3b` (snappier, weaker) | **Do not run 14B on CPU.** |

CPU response feel: 7B ≈ 10–40 s/answer; 3B ≈ 3–10 s/answer. Set expectations.

### How to size for a *different* client

- **Peak concurrent users** (not total headcount) → decides if one GPU suffices.
  ≤ 5 concurrent: one GPU is plenty. Mnema currently serializes LLM generation
  (single worker), so heavy simultaneous load queues — fine at 20, plan ahead
  beyond ~30–50 bursty users.
- **Total document volume (GB)** → decides disk and one-time embedding time.
  Almost never the bottleneck at SMB scale; even thousands of pages embed small.

---

## 2. Common base install (all scenarios)

Do these steps on the server regardless of scenario. Scenario-specific deltas
follow in §3.

### 2.1 Prepare the host

- OS: Ubuntu 22.04 / 24.04 LTS (easiest). Mac for Scenario D.
- Install **Docker Engine + Docker Compose v2**.
- (GPU scenarios) Install the **NVIDIA Container Toolkit** and confirm
  `docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi`
  prints the GPU.

### 2.2 Get the code onto the server

```bash
git clone <your-repo-url> mnema && cd mnema
# or scp the project folder
```

### 2.3 Create and harden `.env`

```bash
python scripts/create_env.py     # writes .env with a strong SECRET_KEY
```

Then edit `.env` and set, at minimum:

```env
# --- REQUIRED, change every one of these per client ---
OLLAMA_MODEL=qwen2.5:14b            # or 7b/3b on CPU (Scenario B)
ADMIN_USERNAME=admin
ADMIN_EMAIL=admin@clientcompany.com
ADMIN_PASSWORD=<strong-unique-password>   # weak values are refused when DEBUG=false
DEBUG=false

# --- networking (fill in once you know the URL — see §4) ---
ALLOWED_ORIGINS=https://mnema.clientcompany.com
COOKIE_SECURE=true                  # true once HTTPS is in front
COOKIE_SAMESITE=lax
```

> **Critical gotcha:** the app defaults to `qwen2.5:7b` but the Compose model
> puller defaults to `qwen2.5:14b`. If you leave `OLLAMA_MODEL` unset they
> disagree and chat returns 404. **Always set `OLLAMA_MODEL` explicitly.**

### 2.4 Change the database password

In `docker-compose.yml`, change the Postgres password from the default
`postgres` to something real (update it in **both** the `db` service
`POSTGRES_PASSWORD` and the `app` service `DATABASE_URL`).

### 2.5 Launch

```bash
docker compose up --build -d
```

First boot pulls the LLM (several GB) and downloads embedding/reranker models
into a named volume — this is slow **once**. Watch progress:

```bash
docker compose logs -f
```

### 2.6 Verify

```bash
curl -fsS http://localhost:8000/health      # -> {"status":"ok","database":"ok",...}
```

Then log in at `http://localhost:8000` with the admin credentials from `.env`.

### 2.7 Load the client's world (admin console)

1. **Roles** → create the roles the company needs (sales, HR, legal, eng…), each
   with a persona/focus.
2. **Documents** → upload files (single or whole folder), tag each with the
   roles allowed to see it.
3. **Users** → create accounts, assign roles. (No email invites yet — you set
   each user's initial password and hand it over.)

### 2.8 Backups + handover (§5)

---

## 3. Scenario-specific steps

### Scenario A — On-prem + NVIDIA GPU (default)

- Leave the `deploy:` GPU block in `docker-compose.yml` as-is (GPU is the
  default).
- `OLLAMA_MODEL=qwen2.5:14b`.
- Follow §2 in full, then network it via §4 (on-prem networking).

### Scenario B — On-prem, CPU only

- **Comment out** the `deploy:` block on the `ollama` service in
  `docker-compose.yml` (the lines reserving the NVIDIA device).
- Set a smaller model: `OLLAMA_MODEL=qwen2.5:7b` (or `qwen2.5:3b`).
- Tell the client plainly this is the budget tier: answers are slower and a bit
  weaker. Consider disabling the reranker for speed (`RERANKER_ENABLED=false`)
  if responses feel sluggish — you trade a little answer precision for latency.

### Scenario C — Client's cloud account + GPU

- Provision a **GPU instance inside the client's own cloud tenant** (AWS `g5`,
  Azure `NC`-series, GCP `g2`), 16–24 GB VRAM, Ubuntu, NVIDIA toolkit.
- Deploy exactly as Scenario A. The stack is identical.
- **It must be the client's account, not yours.** This keeps data in their
  security/compliance boundary and the legal liability with them.
- Network via §4 (cloud networking): real domain + HTTPS + locked firewall.

### Scenario D — Apple Silicon Mac (no NVIDIA)

**Gotcha:** Docker Desktop on Mac **cannot pass the Apple GPU into a
container**. Running Ollama inside Compose on a Mac would be CPU-only and waste
the GPU. So split the topology:

1. Install and run **Ollama natively on the Mac** (uses the Metal GPU):
   ```bash
   brew install ollama
   ollama serve
   ollama pull qwen2.5:14b
   ```
2. In `docker-compose.yml`, **remove the `ollama` and `ollama-init` services**
   (or don't start them), and point the app at the host Ollama. In `.env`:
   ```env
   OLLAMA_BASE_URL=http://host.docker.internal:11434
   OLLAMA_MODEL=qwen2.5:14b
   ```
3. Start only the `db` and `app` services:
   ```bash
   docker compose up --build -d db app
   ```

Flag this as a **custom install** in your notes — it's not the one-command path.

---

## 4. Networking — how the other computers get access

Mnema is a **web app**: employees just open a URL in a browser. **Nothing is
installed on employee machines.** Your job is to expose the server safely.

> **Already handled for you:** in the base `docker-compose.yml`, Postgres
> (5432), Ollama (11434), and the app (8000) are bound to `127.0.0.1` — they are
> reachable from the host but **never from the LAN/internet**. The production
> overlay adds an HTTPS proxy as the only network-facing service. You do **not**
> need to hand-edit ports anymore.

### 4.1 Put HTTPS in front (both on-prem and cloud)

The repo ships a ready-made Caddy overlay — `docker-compose.prod.yml` plus a
`Caddyfile`. To bring the stack up with HTTPS:

1. Edit `Caddyfile` and set the client's domain (or pick the on-prem / internal
   block — it's commented inline).
2. Set in `.env`:
   ```env
   COOKIE_SECURE=true
   ALLOWED_ORIGINS=https://mnema.clientcompany.com
   ```
3. Launch with both files:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
   ```

Caddy terminates TLS on 80/443 and proxies to `app:8000` over the internal
network (with SSE buffering disabled so streamed tokens flush immediately). The
app already sends HSTS once `COOKIE_SECURE=true` and runs Uvicorn with
`--proxy-headers`, so it trusts `X-Forwarded-*` from Caddy. For a public domain
Caddy fetches a Let's Encrypt cert automatically; for an internal hostname use
the `tls internal` or own-certificate block in the `Caddyfile`.

### 4.2 Internal ports are already closed

Postgres and Ollama are loopback-bound in the base compose (§4 note above), so
there's nothing to remove. With the prod overlay, **Caddy on 80/443 is the only
service exposed to the network.** Verify after launch with `docker compose ps`
— only `caddy` should list `0.0.0.0:80/443`.

### 4.3a On-prem networking

- Give the server a **static internal IP** (e.g. `192.168.1.50`).
- Add an **internal DNS record** so employees use a name, not an IP
  (`mnema.clientcompany.internal`).
- Remote / WFH employees reach it over the **company VPN** (IT already has one).
- Employees open `https://mnema.clientcompany.internal` → log in → chat.

### 4.3b Cloud networking (Scenario C)

- Point a **domain** at the instance: `mnema.clientcompany.com`.
- Caddy gets a Let's Encrypt cert automatically.
- **Firewall: open only 80/443 to the internet. Block 5432, 11434, and 8000
  from the outside** — they are internal-only.
- Employees open `https://mnema.clientcompany.com` from anywhere → log in → chat.

### 4.4 The employee experience (identical everywhere)

Open browser → go to the URL → log in with the username/password you created →
ask questions. Their assigned role decides which documents they can see.

---

## 5. Backups, handover, and updates

### Backups

- Database backup scripts ship in `scripts/` (`backup.sh`, `backup.ps1`,
  `restore.sh`). They dump Postgres (users, roles, conversations, document
  metadata + vectors).
- **Schedule** `backup.sh` via cron / Task Scheduler (e.g. nightly).
- Also back up the **`uploads_data` volume** (the original files) and **`.env`**
  (contains `SECRET_KEY` — without it, sessions break after restore).
- **Do a restore drill once** before handing over, so you know it works.

### Handover checklist (leave with the client)

- The URL and the admin credentials (have them change the admin password).
- How to add users / documents / roles (a 1-page admin cheat-sheet).
- How to restart: `docker compose restart` / where logs are
  (`docker compose logs`).
- Who to contact for support (you) and the support SLA they bought.

### Updates

To ship a new version to a client:

```bash
cd mnema
git pull                     # or copy the new code
docker compose up --build -d # migrations run automatically on boot
```

Alembic migrations apply on startup. **Always take a backup first.**

---

## 6. Per-client install checklist (print this)

- [ ] Scenario chosen (A/B/C/D); hardware sized for it
- [ ] Docker + Compose installed; (GPU) NVIDIA toolkit verified with `nvidia-smi`
- [ ] Code on server
- [ ] `.env` created with **strong** `SECRET_KEY`, `ADMIN_PASSWORD`,
      `OLLAMA_MODEL` set explicitly, `DEBUG=false`
- [ ] Postgres password changed in `docker-compose.yml`
- [ ] (CPU) GPU `deploy:` block commented out + smaller model set
- [ ] (Mac) native Ollama + `host.docker.internal` topology
- [ ] `Caddyfile` domain set; `COOKIE_SECURE=true`, `ALLOWED_ORIGINS` = real URL
- [ ] Launched with the prod overlay:
      `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`
- [ ] First-boot model pull complete; `/health` returns ok; admin login works over HTTPS
- [ ] `docker compose ps` shows **only `caddy`** publishing `0.0.0.0:80/443`
      (db/ollama/app are loopback-only)
- [ ] Roles, documents (tagged), and users created
- [ ] Backups scheduled; restore tested; `.env` + uploads backed up
- [ ] Handover doc + admin cheat-sheet delivered; support contact set
