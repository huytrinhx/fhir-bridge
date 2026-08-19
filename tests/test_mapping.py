from backend.mapping import ResourceElementRow, _apply_grounded_value, build_full_skeleton, build_skeleton


def element(path, type=None, min_card=0, max_card="1", is_choice_variant=False, short=""):
    return ResourceElementRow(
        path=path, short=short, type=type, min_card=min_card, max_card=max_card, is_choice_variant=is_choice_variant
    )


def test_build_skeleton_includes_only_required_non_choice_fields():
    elements = [
        element("Patient.identifier", type="Identifier", min_card=1, max_card="*"),
        element("Patient.name", type="HumanName", min_card=0, max_card="*"),  # not required -- excluded
        element("Observation.valueString", type="string", min_card=0, max_card="1", is_choice_variant=True),  # excluded
    ]

    skeleton = build_skeleton(elements)

    assert list(skeleton.keys()) == ["identifier"]
    assert skeleton["identifier"] == ["<Identifier>"]


def test_build_skeleton_placeholder_by_primitive_type():
    elements = [element("Patient.birthDate", type="date", min_card=1, max_card="1")]

    skeleton = build_skeleton(elements)

    assert skeleton == {"birthDate": "<date>"}


def test_build_full_skeleton_includes_optional_fields_too():
    elements = [
        element("Patient.identifier", type="Identifier", min_card=1, max_card="*"),
        element("Patient.name", type="HumanName", min_card=0, max_card="*"),
        element("Observation.valueString", type="string", min_card=0, max_card="1", is_choice_variant=True),  # still excluded
    ]

    skeleton = build_full_skeleton(elements)

    assert set(skeleton.keys()) == {"identifier", "name"}
    assert skeleton["name"] == ["<HumanName>"]


def test_build_full_skeleton_is_not_empty_for_a_resource_with_no_required_fields():
    # e.g. real Patient: every top-level element has min=0 in the base spec.
    elements = [element("Patient.name", type="HumanName", min_card=0, max_card="*")]

    assert build_skeleton(elements) == {}
    assert build_full_skeleton(elements) == {"name": ["<HumanName>"]}


def test_build_skeleton_wraps_repeating_field_in_a_list():
    elements = [element("Patient.telecom", type="ContactPoint", min_card=1, max_card="*")]

    skeleton = build_skeleton(elements)

    assert skeleton == {"telecom": ["<ContactPoint>"]}


def test_apply_grounded_value_merges_bare_primitive_field():
    skeleton = {"birthDate": "<date>"}
    elements_by_field = {"birthDate": element("Patient.birthDate", type="date", min_card=1, max_card="1")}

    applied = _apply_grounded_value(skeleton, elements_by_field, "birthDate", "19800101")

    assert applied is True
    assert skeleton["birthDate"] == "19800101"


def test_apply_grounded_value_wraps_repeating_primitive_in_a_list():
    skeleton = {}
    elements_by_field = {"identifier": element("Patient.identifier", type="string", min_card=1, max_card="*")}

    applied = _apply_grounded_value(skeleton, elements_by_field, "identifier", "123456")

    assert applied is True
    assert skeleton["identifier"] == ["123456"]


def test_apply_grounded_value_refuses_dotted_target_path():
    skeleton = {}
    elements_by_field = {}

    applied = _apply_grounded_value(skeleton, elements_by_field, "source.name", "SendingApp")

    assert applied is False
    assert skeleton == {}


def test_apply_grounded_value_refuses_this_sentinel():
    skeleton = {}
    applied = _apply_grounded_value(skeleton, {}, "$this", "whatever")
    assert applied is False


def test_apply_grounded_value_refuses_complex_typed_field():
    skeleton = {"name": "<HumanName>"}
    elements_by_field = {"name": element("Patient.name", type="HumanName", min_card=1, max_card="*")}

    applied = _apply_grounded_value(skeleton, elements_by_field, "name", "Doe^John")

    assert applied is False
    assert skeleton["name"] == "<HumanName>"  # untouched, not overwritten with raw HL7v2 text


def test_apply_grounded_value_refuses_unknown_field():
    applied = _apply_grounded_value({}, {}, "notARealField", "x")
    assert applied is False


def test_apply_grounded_value_strips_repetition_index_from_primitive_field():
    # Real v2-to-FHIR ConceptMap rows mark which repetition of a repeating
    # element they mean, e.g. "identifier[1]" -- confirmed against ingested
    # PID-to-Patient rows. This isn't a nested path, so it should still merge.
    skeleton = {}
    elements_by_field = {"telecom": element("Patient.telecom", type="string", min_card=1, max_card="*")}

    applied = _apply_grounded_value(skeleton, elements_by_field, "telecom[1]", "555-1234")

    assert applied is True
    assert skeleton["telecom"] == ["555-1234"]
