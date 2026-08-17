# Build log

A chronological record of how FHIR Bud was built, in the order the work
actually happened. Written from session history rather than `git log` (the
repo was developed before being placed under version control).

## Phase 0 — Ingestion pipeline

Built `ingestion/` to turn the real FHIR R4 core spec into two things:
1. A **resource-type whitelist** (`data/whitelist_r4.json`) — the guardrail's
   entire source of truth for what the agent is allowed to recommend.
2. A **vector knowledge base** (Postgres/pgvector, `fhir_kb_chunks` table) —
   spec text chunked and embedded for retrieval grounding.

Bugs found and fixed during this phase:
- pgvector type-inference: bare Python lists need wrapping in `Vector(...)`
  outside of INSERT statement context, or psycopg can't infer the type.
- pgvector connection ordering: `register_vector(conn)` must run *after*
  `CREATE EXTENSION IF NOT EXISTS vector`, not before — the type doesn't
  exist in the DB until the extension is created.

## Model distribution: Kyma

Directed to use [kymaapi.com](https://kymaapi.com) as the API aggregator for
both embeddings (Qwen3-Embedding-8B, 4096-dim) and LLM inference (Claude via
Kyma), rather than calling providers directly.

## Phase 1 — Retrieval-as-tool agent

`backend/agent.py`: `FhirBridgeSession` — the agent classifies intent,
retrieves relevant spec chunks, synthesizes a recommendation, and runs it
through guardrails (whitelist check, code redaction) before returning it.

## Phase 2 — Multi-turn clarification

Extended the session to support back-and-forth: the agent can ask
clarifying questions instead of forcing a one-shot answer, and continue the
same session across multiple messages (`start` / `respond`).

## Phase 3 — Frontend chat UI

React + Vite chat interface: describe a use case, converse with the agent,
see the categorized recommendation appear alongside citations.

## Phase 4 — Guardrail hardening + eval harness

- Whitelist and citation checks tightened; unverifiable terminology codes
  are stripped from rationales rather than shown (`code_guard.py`).
- `eval/` golden-case harness added to track recommendation quality
  independent of unit-test correctness.

## Phase 5 — Corpus expansion

`run_ingest_ig.py --package us_core` added: ingests US Core IG profiles into
the same knowledge base, keyed to their base FHIR resource type. Deliberately
never touches the whitelist — only the core spec can expand what the
guardrail accepts. Prompting made format-aware (source data format,
terminology system fields feed into how the agent frames its search).

## UI revamp (formal plan-mode)

A larger, planned redesign:
- Resizable three-pane layout (history sidebar / chat / recommendation
  panel) with drag handles, not fixed widths.
- Model dropdown sourced from the full Kyma catalog, filtered to
  chat-capable models.
- Conversation history persisted to Postgres and browsable per-session.
- Rerun button — re-executes a finished conversation from its original
  input.
- Feedback/quality-report modal for flagging bad recommendations.
- Input panel: use-case text, optional sample-data paste/upload, source
  format and terminology-system dropdowns.

### Bugs found and fixed in this phase
- Switching models mid-request could leave the composer looking active but
  silently non-functional after an error — fixed by introducing a
  generation-counter guard (see `generationRef` in `agents.md`) so a stale
  async response can never land on top of state that's moved on, and making
  error paths properly terminal instead of silently inert.
- Collapsible history sidebar added (was always expanded before).
- Badge colors fixed: in-progress = yellow, answered = green.
- Default history load capped at 10 conversations; added a regex-only "deep
  search" bar to reach anything older without loading it all up front.
- Misaligned dropdowns: labels with different line counts were pushing
  `<select>` elements to different heights. First fix attempt
  (`display: flex` on `.field__label`) introduced a whitespace-collapsing
  regression; corrected to `min-height` reservation with `display: block`.
- "New conversation should persist the conversation being left" — turned out
  conversations were *already* persisted turn-by-turn; the real bug was a
  race condition where clicking "New conversation" mid-request let a stale
  async response corrupt the fresh session's state. Fixed with the same
  `generationRef` pattern, applied consistently across all async handlers.

## Admin: Quality Reports + email notifications

- `GET /api/feedback` (list) and detail view for admin review of reported
  quality issues.
- Real email notification via **Resend** (chosen over SendGrid/Postmark) to
  the maintainer's own address when new feedback is submitted. Verified live
  with a real triggered send, no errors.

## Major feature: accounts, guest mode, PHI redaction

Planned formally (see the plan-mode record referenced in this session).
Landing page offers two paths:
1. **Guest mode** — zero persistence, in-memory only, cleared on
   browser-session-end or 20 minutes idle.
2. **Authenticated accounts** — self-serve username/password signup or
   Google OAuth; conversations/feedback scoped per-user.

Decisions made explicitly when planning this:
- PHI redaction: regex/pattern-based only, no LLM pass.
- Redaction applied both before the LLM call and before persistence, not
  just at the DB-write boundary.

Backend additions: `users` table + `user_id` columns (added via `ALTER
TABLE ... ADD COLUMN IF NOT EXISTS` so existing local data wasn't dropped),
`auth.py` (JWT + bcrypt), `google_oauth.py` (authorization-code flow with
in-memory CSRF state), `phi_redaction.py` (direct + labeled-field regex
patterns).

Frontend additions: `LandingPage.tsx`, `AuthPanel.tsx`, `auth.ts` (token
storage), stage/authUser state and OAuth-callback handling in `App.tsx`.

### Bugs found and fixed in this phase
- Labeled-field PHI redaction was matching inside its own prior output's
  `[REDACTED-SSN]` tag (word-boundary matching on "SSN" caught the tag
  itself). Fixed by reordering the two redaction passes: labeled-field
  patterns run first, direct patterns second.

Google OAuth was verified live end-to-end (real consent-screen navigation,
correct `client_id`/`redirect_uri`/`scope`/CSRF `state`); the assistant
stopped short of completing account selection/consent, per standing policy
against acting through real credential flows on the user's behalf. The user
completed login themselves and confirmed it worked.

## Rebrand + header redesign

Prompted by: *"After logged in, user and logout button should appear in the
top right corner with more elegant interface. Change the app branding to
FHIR Bud as in buddy."*

- Renamed the product from "FHIR Knowledge-Bridge" everywhere user-facing
  and in internal strings: frontend title/header, `index.html` `<title>`,
  FastAPI app title, email copy (`notifications.py`), `pyproject.toml`
  description. The Python package slug (`name = "fhir-bridge"`) was
  deliberately left unchanged.
- Redesigned the chat header: account badge (avatar + name) and
  Log out/Sign in button separated from action buttons by a divider,
  grouped in a right-aligned wrapper. Self-caught a follow-up issue during
  visual verification — at a moderately narrow width the wrapped layout left
  the title vertically stranded between two rows; fixed by switching
  `.chat-header` to `align-items: flex-start` with `flex-wrap: wrap` and a
  small `padding-top` on the `<h1>` so both the single-row and wrapped
  two-row states look intentional. Verified at both ~1325px and 1800px
  viewports.

## Header follow-up: move account controls to the sidebar

Prompted by: *"username and logout button should [be] better in the bottom
of the history pane."*

Moved the account badge and Log out/Sign in button out of the chat header
entirely and into a footer pinned to the bottom of the history sidebar
(`margin-top: auto` inside the sidebar's flex column), including a
collapsed-sidebar variant that shows just the avatar. The chat header now
holds only conversation-scoped actions (Rerun, New conversation, Quality
Reports). Verified in both expanded and collapsed sidebar states.

## Documentation pass

Added `README.md` (setup/run instructions), `product.md` (feature list and
the rules the guardrails enforce), `agents.md` (standing decisions and
conventions for whoever works on this next), and this file — ahead of
publishing the repo to GitHub and deploying with Supabase as the Postgres
host.
