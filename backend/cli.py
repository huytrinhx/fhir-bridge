"""Interactive CLI for a multi-turn conversation with the FHIR Bridge agent.

Usage:
    python -m backend.cli "raw data from vital monitors"
"""
from __future__ import annotations

import sys

from backend.agent import ClarifyingQuestion, FhirBridgeSession, FinalRecommendation, OutOfScope


def print_recommendation(outcome: FinalRecommendation) -> None:
    print("\nMust-have:")
    for rec in outcome.must_have:
        print(f"  - {rec.resource_type}: {rec.rationale}")
        print(f"    source: {rec.citation.url}")

    print("\nPotentially needed:")
    for rec in outcome.potentially_needed:
        print(f"  - {rec.resource_type}: {rec.rationale}")
        print(f"    source: {rec.citation.url}")

    if outcome.dropped:
        print("\n[guardrail] dropped unverifiable items:")
        for reason in outcome.dropped:
            print(f"  - {reason}")

    if outcome.redacted:
        print("\n[guardrail] redacted possible terminology codes:")
        for note in outcome.redacted:
            print(f"  - {note}")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python -m backend.cli <use case description>")
        return 1

    use_case = " ".join(sys.argv[1:])
    session = FhirBridgeSession()
    outcome = session.start(use_case)

    while True:
        if isinstance(outcome, OutOfScope):
            print(f"Out of scope: {outcome.reason}")
            return 0

        if isinstance(outcome, FinalRecommendation):
            print_recommendation(outcome)
            return 0

        if isinstance(outcome, ClarifyingQuestion):
            print(f"\n{outcome.question}")
            if outcome.options:
                for i, option in enumerate(outcome.options, start=1):
                    print(f"  {i}. {option}")
                print("  (or type a free-text answer)")
            answer = input("\n> ")
            outcome = session.respond(answer)
            continue

        raise AssertionError(f"unhandled outcome: {outcome!r}")


if __name__ == "__main__":
    sys.exit(main())
