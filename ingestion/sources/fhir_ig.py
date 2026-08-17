"""Download and parse FHIR Implementation Guide packages via the FHIR package
registry -- the same npm-style .tgz format every published IG ships as, so
this same mechanism covers US Core now and mCODE / Da Vinci / the HL7v2-to-FHIR
mapping guide later without a new fetcher per IG.
"""
from __future__ import annotations

import json
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


@dataclass(frozen=True)
class IgPackageSource:
    name: str  # slug used for chunk ids / filenames, e.g. "us_core"
    label: str  # human label, e.g. "US Core"
    package_id: str  # FHIR package id, e.g. "hl7.fhir.us.core"
    version: str
    tarball_url: str
    citation_base_url: str  # version-pinned page base, e.g. .../STU9 (not "current" -- must stay resolvable)
    spec_version: str = "R4"


# Verified live before use: tarball_url returns a real gzip tarball, and
# citation_base_url + "/StructureDefinition-us-core-patient.html" resolves to
# the actual STU9 Patient profile page (not the floating "current" alias,
# which would silently break citations whenever US Core republishes).
US_CORE = IgPackageSource(
    name="us_core",
    label="US Core",
    package_id="hl7.fhir.us.core",
    version="9.0.0",
    tarball_url="https://packages.simplifier.net/hl7.fhir.us.core/9.0.0",
    citation_base_url="https://hl7.org/fhir/us/core/STU9",
)


def download_ig_package(source: IgPackageSource, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"{source.name}.tgz"
    if target.exists():
        return target

    response = requests.get(source.tarball_url, timeout=60)
    response.raise_for_status()
    target.write_bytes(response.content)
    return target


def load_ig_profiles(tarball_path: Path) -> list[dict[str, Any]]:
    """Return concrete resource profiles from a FHIR IG package tarball.

    kind == "resource" and derivation == "constraint" selects profiles that
    constrain a base FHIR resource type (e.g. us-core-patient constrains
    Patient) -- not abstract types, not primitive/complex data types.
    """
    profiles: list[dict[str, Any]] = []
    with tarfile.open(tarball_path, mode="r:gz") as archive:
        for member in archive.getmembers():
            if not member.name.startswith("package/StructureDefinition-"):
                continue
            file = archive.extractfile(member)
            if file is None:
                continue
            definition = json.loads(file.read().decode("utf-8"))
            if definition.get("kind") != "resource":
                continue
            if definition.get("derivation") != "constraint":
                continue
            profiles.append(definition)
    return profiles
