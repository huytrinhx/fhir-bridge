# FHIR Bud

FHIR Bud takes a plain-English description of a healthcare data source (e.g.
*"raw data from vital monitors"*) and returns a grounded, cited list of FHIR
R4 resources needed to model it — split into **must-have** and **potentially
needed**, via a multi-turn Q&A conversation with an LLM.

The core design constraint: the LLM can never invent a resource type or
terminology code. Every recommendation is checked against a whitelist built
directly from the real FHIR R4 spec (plus the US Core IG), and every claim is
retrieval-grounded and cited back to source. If the model can't find support
for something, it's dropped rather than guessed.

## Architecture at a glance

```
frontend/          React 19 + TypeScript + Vite chat UI (no router/state lib)
backend/            FastAPI app: auth, conversation orchestration, guardrails
ingestion/          One-off pipelines that build the retrieval corpus
eval/               Golden-case eval harness for the agent's recommendations
tests/              pytest unit tests (backend + ingestion)
data/               Ingestion outputs (whitelist, embeddings source) — gitignored
```

**Backend** (`backend/`)
- `api.py` — FastAPI routes: auth, conversation start/continue, history,
  feedback, admin. In-memory `_sessions` dict holds live conversations;
  `persistence.py` (psycopg3, plain `connect()` per call) persists
  conversations/feedback/users to Postgres for authenticated users only.
- `agent.py` — `FhirBridgeSession`, the multi-turn orchestration: intent
  classification → retrieval → synthesis → guardrail checks, calling an LLM
  via the Kyma API (see below).
- `retrieval.py` — vector search against the FHIR knowledge base (pgvector).
- `guardrails.py` / `code_guard.py` — whitelist enforcement (only real FHIR
  R4 resource types can be recommended) and terminology-code redaction
  (unverified codes are stripped from LLM output rather than shown).
- `phi_redaction.py` — regex-based PHI redaction (SSNs, emails, phone
  numbers, dates, and labeled fields like "MRN:"/"Patient Name:"), applied to
  authenticated users' input *before* it reaches the LLM and *before* it's
  persisted. Guest users are never redacted because nothing of theirs is
  persisted or logged in the first place.
- `auth.py` / `google_oauth.py` — JWT session tokens (7-day expiry, bcrypt
  password hashing) and Google OAuth2 login.
- `notifications.py` — emails the admin (via Resend) when a user reports a
  quality issue.

**Frontend** (`frontend/src/`)
- `App.tsx` — all state lives here via `useState`/`useEffect`; no Redux/router.
  Landing page → guest or authenticated chat UI, resizable 3-pane layout
  (history sidebar / chat / recommendation panel).
- `components/` — `LandingPage`, `AuthPanel`, `HistorySidebar`, `InputPanel`,
  `FeedbackModal`, `AdminFeedback`.
- `api.ts` — fetch wrapper, attaches `Authorization: Bearer <token>` when a
  user is signed in.

**Ingestion** (`ingestion/`) — run once (and re-run whenever the corpus needs
refreshing), not part of the live request path:
- `run_ingest.py` — downloads the FHIR R4 core spec, derives the resource
  **whitelist** (the guardrail's source of truth) and the base knowledge-base
  chunks, embeds them (OpenAI, text-embedding-3-small, 1536-dim), and stores
  them in `fhir_kb_chunks` (pgvector, exact search, no ANN index).
- `run_ingest_ig.py --package us_core` — additive: ingests US Core IG
  profiles into the same table, keyed to their base FHIR resource type. Never
  touches the whitelist — only the core spec can expand what the guardrail
  will accept.

## Prerequisites

- Python 3.11+
- Node 20+
- Docker (for local Postgres + `pgvector` — see Setup below; no local
  Postgres install needed)
- A [Kyma](https://kymaapi.com) API key (LLM inference + the chat-model
  catalog) and an [OpenAI](https://platform.openai.com) API key (KB
  embeddings)
- Optional: [Resend](https://resend.com) API key for feedback-report emails,
  Google OAuth client credentials for social login

## Setup

```bash
# from repo root
cp .env.example .env        # fill in KYMA_API_KEY, OPENAI_API_KEY, SECRET_KEY at minimum
                             # (DATABASE_URL already matches docker-compose.yml)
pip install -e ".[dev]"

# start Postgres + pgvector in Docker (first run pulls the image)
docker compose up -d

# one-time: build the retrieval corpus (downloads spec + IG, embeds, stores)
python -m ingestion.scripts.run_ingest
python -m ingestion.scripts.run_ingest_ig --package us_core

# backend
uvicorn backend.api:app --reload --port 8000

# frontend (separate terminal)
cd frontend
npm install
npm run dev      # http://localhost:5173
```

The `postgres` service in `docker-compose.yml` persists data in a named
Docker volume (`postgres_data`), so it survives `docker compose down` —
use `docker compose down -v` if you actually want to wipe it. The
container's `pgvector` extension is enabled automatically by the image;
the ingestion pipeline still runs `CREATE EXTENSION IF NOT EXISTS vector`
itself, so nothing extra is needed on first run.

`SECRET_KEY` can be any long random string — it signs session JWTs; changing
it invalidates all issued tokens. See `.env.example` for every variable and
what it's for (Google OAuth redirect URI, Resend sender rules, etc).

## Running tests

```bash
python -m pytest -q
```

There's also a golden-case eval harness for the agent's actual
recommendation quality (separate from unit correctness):

```bash
python -m eval.run_eval
```

## Quick manual check

```bash
python -m backend.cli "raw data from vital monitors"
```

Runs a single-turn conversation against the real agent from the terminal —
useful for iterating on prompts/guardrails without the frontend.

## Two ways to use the app

- **Guest mode**: zero persistence. Nothing is written to Postgres; the
  in-memory session is cleared on browser-tab close or after 20 minutes
  idle. No history, no quality-report feature (nothing to report against).
- **Signed in** (username/password or Google): conversations and feedback
  are persisted, scoped to your account, and visible in History / Quality
  Reports. Input is PHI-redacted before it's sent to the LLM or saved.

See `product.md` for the full feature/rules rundown and `agents.md` for
conventions and decisions worth knowing before touching this codebase.

## Deploying to Railway

The root `Dockerfile` builds the frontend and serves it (plus the API) out
of one FastAPI process, so this ships as a **single Railway service**. No
separate frontend host or CORS setup needed in production — `railway.toml`
tells Railway to build with that Dockerfile.

1. **Database.** This app needs Postgres with `pgvector`, which Railway's
   default Postgres plugin doesn't include. Either:
   - Use [Supabase](https://supabase.com) (has `pgvector` built in — point
     `DATABASE_URL` at its connection string), or
   - Deploy Railway's `pgvector`-flavored Postgres template instead of the
     plain Postgres plugin.
2. **Create the Railway service** from this GitHub repo. Railway will pick
   up `railway.toml`/`Dockerfile` automatically.
3. **Attach a volume**, e.g. mounted at `/data`, and set
   `INGEST_DATA_DIR=/data`. The backend reads `whitelist_r4.json` (the
   guardrail's source of truth) from this path at request time
   (`backend/config.py`); without a volume it would need re-ingesting after
   every redeploy, since container filesystems are ephemeral otherwise.
4. **Set environment variables** on the service (see `.env.example` for what
   each does): `DATABASE_URL`, `KYMA_API_KEY`, `OPENAI_API_KEY` (KB
   embeddings), `SECRET_KEY`, and once you know the service's Railway
   domain, `FRONTEND_ORIGIN=https://<that domain>` and
   `GOOGLE_REDIRECT_URI=https://<that domain>/api/auth/google/callback`
   (also register the same redirect URI in the Google Cloud Console OAuth
   client). Add `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`/`RESEND_API_KEY`
   if you want social login / feedback emails.
5. **Deploy.** The app starts and serves the UI fine even before ingestion
   has run — only actual recommendation requests need the whitelist/KB data.
6. **Run ingestion once**, inside the running container so the output lands
   on the volume from step 3 (the Railway CLI's `railway run` executes
   *locally* with Railway's env vars, which would write the whitelist to
   your own machine instead — use `railway ssh` so it runs in the deployed
   container):
   ```bash
   railway ssh
   # inside the container:
   python -m ingestion.scripts.run_ingest
   python -m ingestion.scripts.run_ingest_ig --package us_core
   ```
   Re-run these only when the corpus needs refreshing — the `data/`
   directory persists on the volume across redeploys as long as it stays
   attached.

   If `fhir_kb_chunks` already exists from an ingest run before the switch
   to OpenAI embeddings (`VECTOR(4096)`, Kyma/Qwen3), drop it first --
   `CREATE TABLE IF NOT EXISTS` won't retrofit the column to the new
   `VECTOR(1536)` width, and old and new embeddings aren't comparable
   anyway. No `psql` client in the container image, so drop it via Python:
   ```bash
   railway ssh
   # inside the container:
   python -c "
   import os, psycopg
   psycopg.connect(os.environ['DATABASE_URL'], autocommit=True).execute('DROP TABLE IF EXISTS fhir_kb_chunks;')
   "
   python -m ingestion.scripts.run_ingest
   python -m ingestion.scripts.run_ingest_ig --package us_core
   ```
   Skip the drop on a brand-new deployment that's never been ingested --
   `ensure_schema` creates the `VECTOR(1536)` table correctly on its own.

Note: `_sessions` (guest conversations) and OAuth CSRF state are in-memory
dicts (`backend/api.py`, `backend/google_oauth.py`), so this only works
correctly as a **single replica** — don't scale the service horizontally
without moving that state to Postgres/Redis first.
