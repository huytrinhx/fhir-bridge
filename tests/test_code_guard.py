from backend.code_guard import sanitize_rationale


def test_redacts_loinc_shaped_code_near_keyword():
    text, redacted = sanitize_rationale("Use LOINC 2345-7 for the glucose result.")
    assert redacted is True
    assert "2345-7" not in text
    assert "[code redacted]" in text


def test_redacts_icd10_shaped_code_near_keyword():
    text, redacted = sanitize_rationale("Assign ICD-10 code E11.9 for this condition.")
    assert redacted is True
    assert "E11.9" not in text


def test_leaves_clean_text_untouched():
    text, redacted = sanitize_rationale("Represents each vital sign reading from the monitor.")
    assert redacted is False
    assert text == "Represents each vital sign reading from the monitor."


def test_does_not_redact_numbers_without_a_code_system_keyword_nearby():
    text, redacted = sanitize_rationale("There were 2345 readings collected over the encounter.")
    assert redacted is False
    assert "2345" in text


def test_mentioning_code_system_name_without_a_code_is_untouched():
    text, redacted = sanitize_rationale(
        "If codes matter, map this to LOINC separately -- no code is stated here."
    )
    assert redacted is False
