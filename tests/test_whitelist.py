from pathlib import Path

from ingestion.pipeline.whitelist import build_whitelist, extract_resource_metas
from ingestion.sources.fhir_spec import load_resource_definitions

FIXTURE = Path(__file__).parent / "fixtures" / "sample_bundle.json"


def test_load_resource_definitions_filters_abstract_and_non_resource_kinds():
    definitions = load_resource_definitions(FIXTURE)
    types = {d["type"] for d in definitions}
    assert types == {"Patient", "Observation"}


def test_extract_resource_metas_builds_citation_urls():
    definitions = load_resource_definitions(FIXTURE)
    metas = extract_resource_metas(definitions)
    by_name = {m.type_name: m for m in metas}

    assert by_name["Observation"].citation_url == "https://hl7.org/fhir/R4/observation.html"
    assert by_name["Observation"].canonical_url == "http://hl7.org/fhir/StructureDefinition/Observation"
    assert by_name["Observation"].description.startswith("Measurements")


def test_build_whitelist():
    definitions = load_resource_definitions(FIXTURE)
    metas = extract_resource_metas(definitions)
    assert build_whitelist(metas) == {"Patient", "Observation"}
