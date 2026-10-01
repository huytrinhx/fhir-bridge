import pytest

from backend.phi_redaction import redact_phi


def test_redacts_ssn():
    # No "SSN" label nearby, so this exercises the direct pattern in
    # isolation (see test_redacts_labeled_ssn for the labeled variant, where
    # the labeled-field pass legitimately overwrites the more specific tag).
    text, redacted = redact_phi("Value on file: 123-45-6789.")
    assert redacted is True
    assert "123-45-6789" not in text
    assert "[REDACTED-SSN]" in text


def test_redacts_labeled_ssn():
    text, redacted = redact_phi("Patient SSN is 123-45-6789 on file.")
    assert redacted is True
    assert "123-45-6789" not in text


def test_redacts_email():
    text, redacted = redact_phi("Contact john.doe@example.com for follow-up.")
    assert redacted is True
    assert "john.doe@example.com" not in text


def test_redacts_phone():
    text, redacted = redact_phi("Call the patient at 555-123-4567 tomorrow.")
    assert redacted is True
    assert "555-123-4567" not in text


def test_redacts_iso_date():
    text, redacted = redact_phi("Collected on 2026-08-16 at the clinic.")
    assert redacted is True
    assert "2026-08-16" not in text


def test_redacts_labeled_patient_name():
    text, redacted = redact_phi("Patient Name: John Smith, admitted yesterday.")
    assert redacted is True
    assert "John Smith" not in text
    assert "Patient Name" in text  # label itself is fine to keep


def test_redacts_labeled_mrn_in_csv_style_text():
    text, redacted = redact_phi("MRN: 4471182, TestCode: GLU, Value: 95")
    assert redacted is True
    assert "4471182" not in text


def test_does_not_redact_unlabeled_name_known_limitation():
    # Documented, accepted limitation of regex-only redaction.
    text, redacted = redact_phi("John Smith was admitted for observation.")
    assert redacted is False
    assert text == "John Smith was admitted for observation."


def test_does_not_redact_ordinary_fhir_sample_data():
    text, redacted = redact_phi("PatientID,TestCode,Value,Unit\n123,GLU,95,mg/dL")
    assert redacted is False
    assert text == "PatientID,TestCode,Value,Unit\n123,GLU,95,mg/dL"


def test_empty_string_is_a_noop():
    text, redacted = redact_phi("")
    assert redacted is False
    assert text == ""


def test_clean_use_case_text_is_untouched():
    text, redacted = redact_phi("raw data from vital monitors")
    assert redacted is False
    assert text == "raw data from vital monitors"


def test_rejects_unknown_mode():
    with pytest.raises(ValueError, match="mode"):
        redact_phi("text", mode="typo")  # type: ignore[arg-type]


# -- mode="persist": length-preserving asterisk masking, same matching -----


def test_persist_mode_masks_ssn_preserving_length():
    text, redacted = redact_phi("Value on file: 123-45-6789.", mode="persist")
    assert redacted is True
    assert "123-45-6789" not in text
    assert "*" * len("123-45-6789") in text
    assert "[REDACTED-SSN]" not in text


def test_persist_mode_masks_email_preserving_length():
    text, redacted = redact_phi("Contact john.doe@example.com for follow-up.", mode="persist")
    assert redacted is True
    assert "john.doe@example.com" not in text
    assert "*" * len("john.doe@example.com") in text


def test_persist_mode_masks_labeled_field_preserving_value_length_and_keeps_label():
    text, redacted = redact_phi("Patient Name: John Smith, admitted yesterday.", mode="persist")
    assert redacted is True
    assert "John Smith" not in text
    assert "Patient Name" in text
    assert "*" * len("John Smith") in text
    assert "[REDACTED]" not in text


def test_persist_mode_masks_labeled_mrn_preserving_value_length():
    text, redacted = redact_phi("MRN: 4471182, TestCode: GLU, Value: 95", mode="persist")
    assert redacted is True
    assert "4471182" not in text
    assert "*" * len("4471182") in text


def test_persist_mode_matches_the_same_spans_as_llm_mode():
    # Same input, same "was anything redacted" verdict -- only the
    # replacement text differs between modes (see phi_redaction.py's mode
    # parameter docstring).
    raw = "Patient SSN is 123-45-6789 on file, contact jane@example.com."
    llm_text, llm_redacted = redact_phi(raw, mode="llm")
    persist_text, persist_redacted = redact_phi(raw, mode="persist")
    assert llm_redacted is True
    assert persist_redacted is True
    assert "123-45-6789" not in llm_text
    assert "123-45-6789" not in persist_text
    assert "jane@example.com" not in llm_text
    assert "jane@example.com" not in persist_text


def test_persist_mode_leaves_clean_text_untouched():
    text, redacted = redact_phi("raw data from vital monitors", mode="persist")
    assert redacted is False
    assert text == "raw data from vital monitors"


def test_llm_mode_is_the_default():
    raw = "Value on file: 123-45-6789."
    default_text, _ = redact_phi(raw)
    explicit_llm_text, _ = redact_phi(raw, mode="llm")
    assert default_text == explicit_llm_text
    assert "[REDACTED-SSN]" in default_text
