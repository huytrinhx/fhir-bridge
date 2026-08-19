from ingestion.sources.fhir_v2_mappings import (
    extract_segment_mapping_rows,
    segment_code_from_id,
    target_resource_type_from_id,
)

RESOURCE_TYPES_BY_LOWER = {"patient": "Patient", "relatedperson": "RelatedPerson", "provenance": "Provenance"}


def test_segment_code_from_simple_id():
    assert segment_code_from_id("segment-pid-to-patient") == "PID"


def test_segment_code_from_qualifier_id():
    assert segment_code_from_id("segment-pid-patient-to-provenance") == "PID"


def test_segment_code_from_id_returns_none_for_non_segment_id():
    assert segment_code_from_id("datatype-xpn-to-humanname") is None


def test_target_resource_type_from_simple_id():
    assert target_resource_type_from_id("segment-pid-to-patient", RESOURCE_TYPES_BY_LOWER) == "Patient"


def test_target_resource_type_from_id_resolves_multiword_resource_names():
    assert (
        target_resource_type_from_id("segment-prt-to-relatedperson", RESOURCE_TYPES_BY_LOWER)
        == "RelatedPerson"
    )


def test_target_resource_type_from_id_returns_none_when_unresolvable():
    assert target_resource_type_from_id("segment-pid-to-notarealresource", RESOURCE_TYPES_BY_LOWER) is None
    assert target_resource_type_from_id("segment-pid-no-to-token", RESOURCE_TYPES_BY_LOWER) is None


def make_concept_map(concept_map_id: str, elements: list[dict]) -> dict:
    return {"id": concept_map_id, "group": [{"element": elements}]}


def test_extract_segment_mapping_rows_happy_path():
    concept_map = make_concept_map(
        "segment-pid-to-patient",
        [
            {
                "code": "PID-7",
                "display": "Date/Time of Birth",
                "target": [{"code": "birthDate", "equivalence": "equivalent", "comment": "Patient's birth date"}],
            }
        ],
    )

    rows = extract_segment_mapping_rows(concept_map, RESOURCE_TYPES_BY_LOWER, "https://hl7.org/fhir/uv/v2mappings/STU1", "R4")

    assert len(rows) == 1
    row = rows[0]
    assert row.segment == "PID"
    assert row.field_position == 7
    assert row.field_name == "Date/Time of Birth"
    assert row.target_resource_type == "Patient"
    assert row.target_path == "birthDate"
    assert row.explanation == "Patient's birth date"
    assert row.source_url == "https://hl7.org/fhir/uv/v2mappings/STU1/ConceptMap-segment-pid-to-patient.html"
    assert row.spec_version == "R4"


def test_extract_segment_mapping_rows_emits_one_row_per_target():
    concept_map = make_concept_map(
        "segment-msh-to-messageheader",
        [
            {
                "code": "MSH-3",
                "display": "Sending Application",
                "target": [
                    {"code": "source.name", "equivalence": "equivalent"},
                    {"code": "source.endpoint", "equivalence": "relatedto"},
                ],
            }
        ],
    )

    rows = extract_segment_mapping_rows(concept_map, {"messageheader": "MessageHeader"}, "https://hl7.org/fhir/uv/v2mappings/STU1", "R4")

    assert len(rows) == 2
    assert {row.target_path for row in rows} == {"source.name", "source.endpoint"}


def test_extract_segment_mapping_rows_skips_elements_for_a_different_segment():
    # A malformed/unexpected file could have a stray code from another
    # segment -- must not attribute it to this ConceptMap's segment.
    concept_map = make_concept_map("segment-pid-to-patient", [{"code": "PV1-3", "display": "wrong segment", "target": []}])

    rows = extract_segment_mapping_rows(concept_map, RESOURCE_TYPES_BY_LOWER, "https://hl7.org/fhir/uv/v2mappings/STU1", "R4")

    assert rows == []


def test_extract_segment_mapping_rows_returns_empty_for_unresolvable_ids():
    concept_map = make_concept_map("segment-pid-patient-to-provenance-extra-token", [{"code": "PID-3", "target": [{"code": "identifier"}]}])

    rows = extract_segment_mapping_rows(concept_map, RESOURCE_TYPES_BY_LOWER, "https://hl7.org/fhir/uv/v2mappings/STU1", "R4")

    assert rows == []
