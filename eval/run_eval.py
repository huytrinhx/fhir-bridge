"""Golden-case regression eval for the FHIR Bridge agent.

Runs a small, hand-picked set of use cases through the live agent (real Kyma
calls) and checks the guardrails and retrieval quality hold end to end, via
the same public API the CLI/frontend use -- not by calling internals
directly, so it also catches regressions in how the pieces are wired
together. This costs real API credits and depends on model behavior, so it's
a regression check run on demand, not a unit test run in CI.

Usage:
    python -m eval.run_eval
"""
from __future__ import annotations

import sys

from backend.agent import ClarifyingQuestion, FhirBridgeSession, FinalRecommendation, OutOfScope
from backend.config import load_settings
from backend.guardrails import TRUSTED_CITATION_PREFIX, WhitelistEntry, load_whitelist
from eval.golden_cases import GOLDEN_CASES, OUT_OF_SCOPE_CASES

MAX_AUTO_CLARIFY_ROUNDS = 3
AUTO_ANSWER = (
    "Use your best judgement based on typical usage for this scenario and finalize "
    "your recommendation now."
)


def run_case(use_case: str) -> OutOfScope | FinalRecommendation:
    session = FhirBridgeSession()
    outcome = session.start(use_case)
    rounds = 0
    while isinstance(outcome, ClarifyingQuestion) and rounds < MAX_AUTO_CLARIFY_ROUNDS:
        outcome = session.respond(AUTO_ANSWER)
        rounds += 1
    return outcome


def check_case(case: dict, whitelist: dict[str, WhitelistEntry]) -> dict:
    outcome = run_case(case["use_case"])
    notes: list[str] = []
    passed = True

    if isinstance(outcome, OutOfScope):
        return {
            "use_case": case["use_case"],
            "passed": False,
            "notes": [f"expected in-scope recommendation, got out_of_scope: {outcome.reason}"],
        }

    all_recs = outcome.must_have + outcome.potentially_needed
    seen_types = {r.resource_type for r in all_recs}

    for resource_type in case["expect_present"]:
        if resource_type not in seen_types:
            passed = False
            notes.append(f"expected resource {resource_type!r} not present")

    # Redundant with guardrails.validate_recommendations by construction, but
    # checked again here through the public outcome so a future guardrail
    # regression is caught by the eval too, not just assumed safe.
    for rec in all_recs:
        if rec.resource_type not in whitelist:
            passed = False
            notes.append(f"non-whitelisted resource leaked: {rec.resource_type!r}")
        elif not rec.citation.url.startswith(TRUSTED_CITATION_PREFIX):
            passed = False
            notes.append(f"untrusted citation host for {rec.resource_type!r}: {rec.citation.url}")

    if outcome.dropped:
        notes.append(f"guardrail dropped {len(outcome.dropped)} item(s): {outcome.dropped}")

    if outcome.redacted:
        notes.append(f"guardrail redacted {len(outcome.redacted)} code reference(s): {outcome.redacted}")

    return {"use_case": case["use_case"], "passed": passed, "notes": notes}


def check_out_of_scope(message: str) -> dict:
    outcome = run_case(message)
    passed = isinstance(outcome, OutOfScope)
    return {
        "use_case": message,
        "passed": passed,
        "notes": [] if passed else [f"expected out_of_scope, got {type(outcome).__name__}"],
    }


def main() -> int:
    settings = load_settings()
    whitelist = load_whitelist(settings.whitelist_path)

    results = [check_case(case, whitelist) for case in GOLDEN_CASES]
    results += [check_out_of_scope(msg) for msg in OUT_OF_SCOPE_CASES]

    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        print(f"[{status}] {r['use_case']}")
        for note in r["notes"]:
            print(f"    - {note}")

    failed = [r for r in results if not r["passed"]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
