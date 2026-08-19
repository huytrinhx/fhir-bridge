from backend.hl7_parser import Hl7Field, parse_message, split_segments


def test_split_segments_handles_cr_lf_and_crlf_terminators():
    assert split_segments("MSH|a\rPID|b") == ["MSH|a", "PID|b"]
    assert split_segments("MSH|a\nPID|b") == ["MSH|a", "PID|b"]
    assert split_segments("MSH|a\r\nPID|b") == ["MSH|a", "PID|b"]


def test_split_segments_drops_blank_lines():
    assert split_segments("MSH|a\r\r\nPID|b\r") == ["MSH|a", "PID|b"]


def test_msh_fields_are_offset_by_one():
    # MSH-1 is the field-separator character itself (implicit), so the
    # first |-delimited token (encoding characters) is MSH-2.
    message = "MSH|^~\\&|SendingApp|SendingFacility|ReceivingApp|ReceivingFacility|20260818120000||ADT^A01|MSG001|P|2.5"
    fields = parse_message(message)

    assert Hl7Field(segment="MSH", field_position=2, raw_value="^~\\&") in fields
    assert Hl7Field(segment="MSH", field_position=3, raw_value="SendingApp") in fields
    assert Hl7Field(segment="MSH", field_position=4, raw_value="SendingFacility") in fields
    assert not any(f.segment == "MSH" and f.field_position == 1 for f in fields)


def test_ordinary_segment_fields_start_at_one():
    message = "PID|1||123456^^^MRN||Doe^John||19800101|M"
    fields = parse_message(message)

    assert Hl7Field(segment="PID", field_position=1, raw_value="1") in fields
    assert Hl7Field(segment="PID", field_position=3, raw_value="123456^^^MRN") in fields
    assert Hl7Field(segment="PID", field_position=5, raw_value="Doe^John") in fields
    assert Hl7Field(segment="PID", field_position=7, raw_value="19800101") in fields
    assert Hl7Field(segment="PID", field_position=8, raw_value="M") in fields


def test_empty_fields_are_skipped_not_emitted_as_blank_values():
    message = "PID|1||123456"
    fields = parse_message(message)

    assert not any(f.field_position == 2 for f in fields)


def test_multi_segment_message():
    message = (
        "MSH|^~\\&|A|B|C|D|20260818||ADT^A01|1|P|2.5\r"
        "PID|1||123^^^MRN||Doe^John|||19800101|M\r"
        "PV1|1|I"
    )
    fields = parse_message(message)
    segments_present = {f.segment for f in fields}

    assert segments_present == {"MSH", "PID", "PV1"}
    assert Hl7Field(segment="PV1", field_position=2, raw_value="I") in fields
