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
  chunks, embeds them (Kyma, Qwen3-Embedding-8B, 4096-dim), and stores them
  in `fhir_kb_chunks` (pgvector, exact search, no ANN index).
- `run_ingest_ig.py --package us_core` — additive: ingests US Core IG
  profiles into the same table, keyed to their base FHIR resource type. Never
  touches the whitelist — only the core spec can expand what the guardrail
  will accept.

## Prerequisites

- Python 3.11+
- Node 20+
- Postgres with the `pgvector` extension available (a local Docker container
  works fine — the ingestion pipeline runs `CREATE EXTENSION IF NOT EXISTS
  vector` for you)
- A [Kyma](https://kymaapi.com) API key (embeddings + LLM inference)
- Optional: [Resend](https://resend.com) API key for feedback-report emails,
  Google OAuth client credentials for social login

## Setup

```bash
# from repo root
cp .env.example .env        # fill in DATABASE_URL, KYMA_API_KEY, SECRET_KEY at minimum
pip install -e ".[dev]"

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
