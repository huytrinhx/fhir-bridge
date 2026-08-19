from ingestion.pipeline.resource_elements import extract_resource_elements


def make_definition(resource_type: str, elements: list[dict]) -> dict:
    return {"type": resource_type, "snapshot": {"element": elements}}


def test_extracts_only_top_level_elements():
    definition = make_definition(
        "Patient",
        [
            {"path": "Patient", "short": "the resource itself", "min": 0, "max": "1"},
            {"path": "Patient.name", "short": "name", "min": 0, "max": "*", "type": [{"code": "HumanName"}]},
            {
                "path": "Patient.contact.name",
                "short": "nested, not top-level",
                "min": 0,
                "max": "1",
                "type": [{"code": "HumanName"}],
            },
        ],
    )

    elements = extract_resource_elements([definition])

    paths = {e.path for e in elements}
    assert paths == {"Patient.name"}


def test_required_and_repeating_cardinality_carried_through():
    definition = make_definition(
        "Patient",
        [
            {
                "path": "Patient.identifier",
                "short": "identifiers",
                "min": 1,
                "max": "*",
                "type": [{"code": "Identifier"}],
            },
        ],
    )

    [element] = extract_resource_elements([definition])

    assert element.resource_type == "Patient"
    assert element.type == "Identifier"
    assert element.min_card == 1
    assert element.max_card == "*"
    assert element.is_choice_variant is False


def test_choice_type_element_expands_to_one_row_per_declared_type():
    definition = make_definition(
        "Observation",
        [
            {
                "path": "Observation.value[x]",
                "short": "the observed value",
                "min": 0,
                "max": "1",
                "type": [{"code": "Quantity"}, {"code": "string"}, {"code": "CodeableConcept"}],
            },
        ],
    )

    elements = extract_resource_elements([definition])

    paths = {e.path: e for e in elements}
    assert set(paths) == {"Observation.valueQuantity", "Observation.valueString", "Observation.valueCodeableConcept"}
    for element in paths.values():
        assert element.is_choice_variant is True
    assert paths["Observation.valueString"].type == "string"
    assert paths["Observation.valueQuantity"].type == "Quantity"


def test_skips_definitions_without_a_resolvable_resource_type():
    definition = {"kind": "resource", "snapshot": {"element": []}}
    assert extract_resource_elements([definition]) == []
