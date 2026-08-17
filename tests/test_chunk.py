from ingestion.pipeline.chunk import make_chunks
from ingestion.pipeline.whitelist import FhirResourceMeta


def test_make_chunks_produces_one_cited_chunk_per_resource():
    meta = FhirResourceMeta(
        type_name="Observation",
        canonical_url="http://hl7.org/fhir/StructureDefinition/Observation",
        citation_url="https://hl7.org/fhir/R4/observation.html",
        description="Measurements and simple assertions made about a patient.",
    )

    chunks = make_chunks(meta)

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.resource_type == "Observation"
    assert chunk.source_url == "https://hl7.org/fhir/R4/observation.html"
    assert chunk.spec_version == "R4"
    assert "Observation" in chunk.text
    assert "Measurements" in chunk.text
