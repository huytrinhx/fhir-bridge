from backend.guardrails import CitationLedger, WhitelistEntry, validate_recommendations
from backend.retrieval import RetrievedChunk


def make_chunk(resource_type: str, source_url: str | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        resource_type=resource_type,
        text=f"{resource_type}: description",
        source_url=source_url or f"https://hl7.org/fhir/R4/{resource_type.lower()}.html",
        spec_version="R4",
        distance=0.1,
    )


def make_whitelist(*resource_types: str) -> dict[str, WhitelistEntry]:
    return {
        rt: WhitelistEntry(citation_url=f"https://hl7.org/fhir/R4/{rt.lower()}.html", spec_version="R4")
        for rt in resource_types
    }


def test_accepts_whitelisted_and_cited_resource():
    ledger = CitationLedger()
    ledger.record([make_chunk("Observation")])
    whitelist = make_whitelist("Observation", "Device")

    accepted, dropped, redacted = validate_recommendations(
        [{"resource_type": "Observation", "rationale": "core reading"}],
        "must_have",
        whitelist,
        ledger,
    )

    assert len(accepted) == 1
    assert accepted[0].resource_type == "Observation"
    assert accepted[0].citation.url == "https://hl7.org/fhir/R4/observation.html"
    assert dropped == []
    assert redacted == []


def test_drops_resource_not_in_whitelist():
    ledger = CitationLedger()
    ledger.record([make_chunk("Observation")])
    whitelist = make_whitelist("Observation")

    accepted, dropped, redacted = validate_recommendations(
        [{"resource_type": "MadeUpResource", "rationale": "invented"}],
        "must_have",
        whitelist,
        ledger,
    )

    assert accepted == []
    assert len(dropped) == 1
    assert "not a valid FHIR R4 resource type" in dropped[0]


def test_drops_whitelisted_resource_never_retrieved():
    ledger = CitationLedger()  # nothing retrieved this turn
    whitelist = make_whitelist("Observation")

    accepted, dropped, redacted = validate_recommendations(
        [{"resource_type": "Observation", "rationale": "core reading"}],
        "must_have",
        whitelist,
        ledger,
    )

    assert accepted == []
    assert len(dropped) == 1
    assert "not retrieved this turn" in dropped[0]


def test_drops_resource_when_citation_is_not_from_a_trusted_host():
    ledger = CitationLedger()
    ledger.record([make_chunk("Observation", source_url="https://example.com/wrong-page.html")])
    whitelist = make_whitelist("Observation")

    accepted, dropped, redacted = validate_recommendations(
        [{"resource_type": "Observation", "rationale": "core reading"}],
        "must_have",
        whitelist,
        ledger,
    )

    assert accepted == []
    assert len(dropped) == 1
    assert "not from a trusted FHIR source" in dropped[0]


def test_accepts_citation_from_a_non_core_hl7_source_like_an_ig_profile():
    # Once the KB has multiple legitimate sources per resource type (core
    # spec, US Core, later IGs), a citation doesn't have to match one static
    # canonical URL -- it just has to genuinely be from hl7.org/fhir/.
    ledger = CitationLedger()
    ledger.record(
        [
            make_chunk(
                "Observation",
                source_url="https://hl7.org/fhir/us/core/STU9/StructureDefinition-us-core-vital-signs.html",
            )
        ]
    )
    whitelist = make_whitelist("Observation")

    accepted, dropped, redacted = validate_recommendations(
        [{"resource_type": "Observation", "rationale": "vital sign reading"}],
        "must_have",
        whitelist,
        ledger,
    )

    assert len(accepted) == 1
    assert accepted[0].citation.url.endswith("us-core-vital-signs.html")
    assert dropped == []


def test_redacts_code_shaped_token_near_code_system_keyword():
    ledger = CitationLedger()
    ledger.record([make_chunk("Observation")])
    whitelist = make_whitelist("Observation")

    accepted, dropped, redacted = validate_recommendations(
        [
            {
                "resource_type": "Observation",
                "rationale": "Use LOINC 2345-7 to code the glucose result.",
            }
        ],
        "must_have",
        whitelist,
        ledger,
    )

    assert len(accepted) == 1
    assert "2345-7" not in accepted[0].rationale
    assert "[code redacted]" in accepted[0].rationale
    assert len(redacted) == 1


def test_does_not_redact_clean_rationale():
    ledger = CitationLedger()
    ledger.record([make_chunk("Observation")])
    whitelist = make_whitelist("Observation")

    accepted, dropped, redacted = validate_recommendations(
        [{"resource_type": "Observation", "rationale": "Represents each vital sign reading."}],
        "must_have",
        whitelist,
        ledger,
    )

    assert accepted[0].rationale == "Represents each vital sign reading."
    assert redacted == []


def test_drops_malformed_item_instead_of_crashing():
    ledger = CitationLedger()
    ledger.record([make_chunk("Observation")])
    whitelist = make_whitelist("Observation")

    # The model can send a schema-violating item (e.g. a bare string instead
    # of an object) despite the tool's input_schema -- must degrade, not crash.
    accepted, dropped, redacted = validate_recommendations(
        ["Observation", {"resource_type": "Observation", "rationale": "fine"}],
        "must_have",
        whitelist,
        ledger,
    )

    assert len(accepted) == 1
    assert len(dropped) == 1
    assert "malformed recommendation item" in dropped[0]


def test_drops_whole_field_when_not_a_list_instead_of_iterating_characters():
    ledger = CitationLedger()
    ledger.record([make_chunk("Observation")])
    whitelist = make_whitelist("Observation")

    # Seen live: the model returned a bare string for this field instead of a
    # JSON array. Strings are iterable, so a naive `for item in raw_items`
    # would silently produce one bogus "dropped" entry per character.
    accepted, dropped, redacted = validate_recommendations(
        "Observation",
        "potentially_needed",
        whitelist,
        ledger,
    )

    assert accepted == []
    assert len(dropped) == 1
    assert "malformed, expected a list" in dropped[0]
