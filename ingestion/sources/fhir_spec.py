"""Download and parse the official FHIR R4 resource StructureDefinitions."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import requests

from ingestion.config import FHIR_R4_DEFINITIONS_URL

RESOURCE_BUNDLE_FILENAME = "profiles-resources.json"


def download_definitions(dest_dir: Path) -> Path:
    """Download the FHIR R4 definitions bundle and extract the resources file.

    Returns the path to the extracted profiles-resources.json.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / RESOURCE_BUNDLE_FILENAME
    if target.exists():
        return target

    response = requests.get(FHIR_R4_DEFINITIONS_URL, timeout=60)
    response.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        with archive.open(RESOURCE_BUNDLE_FILENAME) as src, target.open("wb") as dst:
            dst.write(src.read())

    return target


def load_resource_definitions(bundle_path: Path) -> list[dict[str, Any]]:
    """Load the profiles-resources.json Bundle and return concrete resource
    StructureDefinitions only (kind == "resource", abstract == False)."""
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))

    definitions: list[dict[str, Any]] = []
    for entry in bundle.get("entry", []):
        resource = entry.get("resource", {})
        if resource.get("resourceType") != "StructureDefinition":
            continue
        if resource.get("kind") != "resource":
            continue
        if resource.get("abstract", False):
            continue
        definitions.append(resource)

    return definitions
