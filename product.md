# FHIR Bud — Product

## What it's for

A healthcare engineer or analyst has some data source (device feed, claims
extract, EHR export, whatever) and needs to know: *which FHIR R4 resources do
I need to model this?* FHIR Bud answers that conversationally, in plain
English in, cited FHIR resource list out.

## Core rules (non-negotiable, guardrail-enforced)

These are the constraints the whole system is designed around — not
aspirational, actually enforced in code (`backend/guardrails.py`,
`backend/code_guard.py`):

1. **No invented resource types.** Every recommended resource type must
   exist in the FHIR R4 whitelist, which is derived mechanically from the
   real FHIR R4 core spec (`ingestion/scripts/run_ingest.py`) — never typed
   in by hand, never expanded by an IG. Anything the model proposes that
   isn't on the whitelist is dropped, not shown.
2. **No invented terminology codes.** The system doesn't have a verified
   terminology corpus yet, so if a rationale mentions something that looks
   like a specific code, it's stripped from the output rather than
   presented as fact.
3. **Every recommendation is cited.** Each resource comes with a source URL
   back to the spec/IG text that grounded it — recommendations are
   retrieval-grounded, not free-associated.
4. **Out-of-scope input is detected, not guessed at.** If a use case isn't
   really a healthcare-data-modeling question, the agent says so instead of
   forcing a FHIR answer.
5. **Ambiguous input triggers clarifying questions**, not an assumption —
   the agent is a multi-turn conversation, not a one-shot classifier.

## Key features

### Conversational recommendation flow
Describe a use case (optionally with a sample data row/payload, a source
format, and a target terminology system) → the agent either asks clarifying
questions, flags the request as out of scope, or returns a categorized,
cited resource list. Any finished conversation can be **rerun** (re-executed
from its original input, e.g. after a prompt/model change) without losing
the original.

### Model choice
Users can pick from the full catalog of chat-capable models Kyma exposes,
per conversation.

### Guest mode vs. accounts
- **Guest**: no signup, nothing persisted anywhere (not Postgres, not logs).
  Session lives in server memory only, cleared on tab close or after 20 min
  idle. No history, no quality reports — there's nothing to look back at by
  design.
- **Account** (username/password or Google OAuth): conversations and
  feedback are saved and scoped to that user, visible under History /
  Quality Reports. This is also the path that triggers **PHI redaction**
  (see below) — accounts are the only place where content is durably stored,
  so that's where the extra protection applies.

### PHI redaction
Regex/pattern-based only (no LLM pass, so it's deterministic and auditable).
Two kinds of pattern:
- **Direct**: SSNs, emails, phone numbers, dates.
- **Labeled fields**: `SSN:`, `DOB:`, `MRN:`, `Patient Name:`, `Address:`,
  `Phone:`, `Email:` followed by a value.

Applied to authenticated users' message and sample-data input **before** it
reaches the LLM and **before** anything is written to Postgres — not just at
the DB boundary. Known, accepted limitation: a bare unlabeled name in prose
("John Smith was admitted...") is not caught, only structured identifiers and
labeled fields are. Guests are never redacted because guest input is never
sent anywhere persistent.

### History and search
Authenticated users get their last 10 conversations by default in the
sidebar; a regex search box searches the full history beyond that without
having to load it all up front.

### Quality reporting
After a finished conversation, a signed-in user can report a quality issue
(what they expected vs. what they got). This persists a snapshot of the
transcript and outcome, and emails the admin (via Resend) so it's not
something that silently sits unread in a table. Admin review UI is the
"Quality Reports" screen — list + detail, transcript + outcome replay.

### Resizable, persistent-layout UI
Three-pane layout (history / chat / recommendation panel) with drag-to-resize
handles and a collapsible history sidebar — not fixed-width, since use cases
vary widely in how much room the chat vs. the resource list needs.

## Explicit non-goals / scope boundaries

- No password reset or email verification flow — bare-bones self-serve
  signup only.
- No refresh-token rotation — a single 7-day JWT; re-login after expiry.
- No terminology-code verification yet (codes are stripped, not validated
  against a real terminology server) — a known gap, not a bug.
- PHI redaction will not catch unlabeled names/addresses in free prose — a
  known, accepted limitation of the regex-only approach, not a target for
  silent improvement without discussing the tradeoff first (an LLM-based
  pass was explicitly ruled out).
