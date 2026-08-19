"""Postgres storage for deterministic structural KB tables -- exact-match
lookups (resource_type, or segment+resource_type), not vector search, so no
embedding column and no pgvector dependency (unlike
ingestion/pipeline/store.py's fhir_kb_chunks).
"""
from __future__ import annotations

import psycopg

from ingestion.config import Settings
from ingestion.pipeline.resource_elements import ResourceElement
from ingestion.sources.fhir_v2_mappings import V2SegmentMappingRow

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fhir_resource_elements (
    resource_type TEXT NOT NULL,
    path TEXT NOT NULL,
    short TEXT NOT NULL,
    type TEXT,
    min_card INTEGER NOT NULL,
    max_card TEXT NOT NULL,
    is_choice_variant BOOLEAN NOT NULL DEFAULT FALSE,
    spec_version TEXT NOT NULL,
    PRIMARY KEY (resource_type, path)
);

CREATE TABLE IF NOT EXISTS fhir_v2_segment_mappings (
    segment TEXT NOT NULL,
    field_position INTEGER NOT NULL,
    field_name TEXT NOT NULL,
    target_resource_type TEXT NOT NULL,
    target_path TEXT NOT NULL,
    explanation TEXT,
    source_url TEXT NOT NULL,
    spec_version TEXT NOT NULL,
    PRIMARY KEY (segment, field_position, target_resource_type, target_path, spec_version)
);

CREATE INDEX IF NOT EXISTS fhir_v2_segment_mappings_lookup_idx
    ON fhir_v2_segment_mappings (segment, target_resource_type);
"""

UPSERT_RESOURCE_ELEMENT_SQL = """
INSERT INTO fhir_resource_elements
    (resource_type, path, short, type, min_card, max_card, is_choice_variant, spec_version)
VALUES
    (%(resource_type)s, %(path)s, %(short)s, %(type)s, %(min_card)s, %(max_card)s,
     %(is_choice_variant)s, %(spec_version)s)
ON CONFLICT (resource_type, path) DO UPDATE SET
    short = EXCLUDED.short,
    type = EXCLUDED.type,
    min_card = EXCLUDED.min_card,
    max_card = EXCLUDED.max_card,
    is_choice_variant = EXCLUDED.is_choice_variant,
    spec_version = EXCLUDED.spec_version;
"""

UPSERT_V2_SEGMENT_MAPPING_SQL = """
INSERT INTO fhir_v2_segment_mappings
    (segment, field_position, field_name, target_resource_type, target_path,
     explanation, source_url, spec_version)
VALUES
    (%(segment)s, %(field_position)s, %(field_name)s, %(target_resource_type)s, %(target_path)s,
     %(explanation)s, %(source_url)s, %(spec_version)s)
ON CONFLICT (segment, field_position, target_resource_type, target_path, spec_version) DO UPDATE SET
    field_name = EXCLUDED.field_name,
    explanation = EXCLUDED.explanation,
    source_url = EXCLUDED.source_url;
"""


def get_connection(settings: Settings) -> psycopg.Connection:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg.connect(settings.database_url)


def ensure_schema(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
    conn.commit()


def upsert_resource_elements(conn: psycopg.Connection, elements: list[ResourceElement]) -> None:
    rows = [
        {
            "resource_type": e.resource_type,
            "path": e.path,
            "short": e.short,
            "type": e.type,
            "min_card": e.min_card,
            "max_card": e.max_card,
            "is_choice_variant": e.is_choice_variant,
            "spec_version": e.spec_version,
        }
        for e in elements
    ]
    with conn.cursor() as cur:
        cur.executemany(UPSERT_RESOURCE_ELEMENT_SQL, rows)
    conn.commit()


def upsert_v2_segment_mappings(conn: psycopg.Connection, rows: list[V2SegmentMappingRow]) -> None:
    values = [
        {
            "segment": r.segment,
            "field_position": r.field_position,
            "field_name": r.field_name,
            "target_resource_type": r.target_resource_type,
            "target_path": r.target_path,
            "explanation": r.explanation,
            "source_url": r.source_url,
            "spec_version": r.spec_version,
        }
        for r in rows
    ]
    with conn.cursor() as cur:
        cur.executemany(UPSERT_V2_SEGMENT_MAPPING_SQL, values)
    conn.commit()
