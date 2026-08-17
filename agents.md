# agents.md — context for whoever (human or AI) works on this repo next

This file exists to carry forward decisions and working conventions from the
sessions that built FHIR Bud, so they don't have to be re-derived or
re-litigated. If you're an AI assistant picking this repo up cold, read this
before making structural changes.

## Standing technical decisions (don't silently reverse these)

- **Kyma (kymaapi.com)** is the model distribution layer for both embeddings
  and LLM inference — a deliberate choice, not a placeholder. If it needs
  replacing, that's a call for the human maintainer, not an inferred cleanup.
- **PHI redaction is regex/pattern-based only — no LLM pass.** Explicitly
  chosen over an LLM-based redactor for determinism and auditability. It has
  a known gap (unlabeled names in free prose aren't caught); that gap was
  accepted knowingly, not missed. Don't "improve" this to an LLM pass without
  discussing the tradeoff first.
- **Redaction happens twice in the same direction**: before the LLM call
  *and* before persistence — not just at the DB-write boundary. Both call
  sites must stay in sync if `phi_redaction.py` changes shape.
- **Guests get zero persistence, full stop.** Nothing about a guest session
  touches Postgres or logs. Don't add "just for debugging" persistence for
  guest sessions.
- **pgvector uses exact search, no ANN index** — a deliberate choice at
  current corpus scale, not an oversight.
- **The resource whitelist only ever grows from the FHIR R4 core spec**
  (`run_ingest.py`), never from an IG (`run_ingest_ig.py`). IG ingestion is
  additive retrieval content only. This is what keeps the guardrail
  meaningful — don't let IG ingestion touch the whitelist.
- **The `generationRef` counter pattern in `App.tsx`** guards every async
  handler (start/continue/select-history/rerun) against stale responses
  landing after the user has moved on (new conversation, picked a different
  history item, etc). Any new async handler added to `App.tsx` should follow
  this pattern — a prior version of this app had a real bug (and a second,
  subtler one) from skipping it.
- **Email notifications go through Resend**, chosen explicitly over
  SendGrid/Postmark. `NOTIFY_TO_EMAIL` defaults to the maintainer's own
  address for now — expect this to become configurable per-deployment.
- **Package slug stays `fhir-bridge`** (`pyproject.toml` `name=`) even though
  the product-facing brand is "FHIR Bud" — that split was intentional, not
  an inconsistency to fix.

## Things to never do on the user's behalf

Established repeatedly this session, consistent with this assistant's
standing policy: never enter passwords, complete OAuth consent screens, or
otherwise act through a real credential/login flow for the user — get them
to the right screen and stop. When Google OAuth was wired up, the assistant
walked the user to the Google account chooser and then explicitly stopped
for the user to finish account selection/consent themselves.

## UI / UX conventions established

- Badge colors: in-progress = yellow, answered = green (history sidebar
  status badges).
- History sidebar loads the 10 most recent conversations by default; a
  regex-only search box is the way to reach anything older — not infinite
  scroll, not "load more."
- Account identity (avatar + name) and Log out/Sign in live at the **bottom
  of the history sidebar**, not the chat header — the header is reserved for
  conversation-scoped actions (New conversation, Rerun, Quality Reports).
  This was a deliberate relocation after the header felt cramped with both
  concerns mixed into one row.
- CSS gotcha worth remembering: don't reach for `display: flex` to fix
  label-height alignment issues — it collapses meaningful whitespace in text
  content. Use `min-height` reservation with `display: block` instead (see
  `.field__label` in `App.css`).
- Resizable panes are driven by React state (`sidebarWidth`, `chatWidth`)
  and inline `gridTemplateColumns`, not CSS-only — drag handles update state
  directly via mouse event listeners (`beginResize` in `App.tsx`).

## External services in use (and why)

| Service | Purpose | Chosen because |
|---|---|---|
| Kyma (kymaapi.com) | Embeddings + LLM inference | Directed by maintainer |
| Postgres + pgvector | Conversation/user storage + vector KB | Standard, and pgvector supports the exact-search retrieval this app needs |
| Resend | Feedback-report emails | Chosen over SendGrid/Postmark when asked |
| Google OAuth | Social login | Alongside self-serve username/password signup |

## Deployment direction (as of this writing)

The maintainer intends to publish this to GitHub and deploy with **Supabase**
as the Postgres host (Supabase supports the `pgvector` extension natively, so
the existing schema/ingestion pipeline should port without changes to the
SQL itself — just point `DATABASE_URL` at the Supabase connection string and
re-run the ingestion scripts against it, or migrate the data). Things worth
checking when that happens:
- `.env` is gitignored — confirm no secrets are committed before pushing.
- `data/` is gitignored (ingestion output) — the deployed environment needs
  its own ingestion run, or a data migration, since that directory won't
  ship with the repo.
- CORS / redirect URIs (`GOOGLE_REDIRECT_URI`, frontend `API_BASE`) are
  currently hardcoded to `localhost` — these need updating for a real
  deployment target.

## Where to look for more

- `product.md` — feature list and the rules the guardrails actually enforce.
- `build-log.md` — chronological record of how this app was built, phase by
  phase, including bugs found and fixed along the way.
- `README.md` — how to actually run the thing.
