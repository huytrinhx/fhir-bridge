from backend.agent import build_display_transcript, compose_use_case


def test_compose_use_case_with_only_message():
    assert compose_use_case("raw data from vital monitors") == "raw data from vital monitors"


def test_compose_use_case_includes_format_and_terminology_and_sample():
    text = compose_use_case(
        "lab results",
        data_sample="A,B\n1,2",
        data_format="CSV",
        terminology_system="LOINC",
    )

    assert "lab results" in text
    assert "Source data format: CSV" in text
    assert "Desired terminology system for coded elements: LOINC" in text
    assert "A,B\n1,2" in text
    assert "```" in text


def test_compose_use_case_truncates_long_sample():
    long_sample = "x" * 10_000
    text = compose_use_case("use case", data_sample=long_sample)

    # fenced block should contain the truncated sample, not the full 10k chars
    fenced = text.split("```")[1]
    assert len(fenced.strip()) <= 4000


def test_build_display_transcript_extracts_search_question_and_answer():
    messages = [
        {"role": "user", "content": "raw data from vital monitors"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "search_fhir_kb",
                    "input": {"query": "vital signs"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": "- Observation: ..."},
            ],
        },
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_2",
                    "name": "ask_clarifying_question",
                    "input": {"question": "Continuous or periodic readings?"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_2", "content": "Periodic, every 5 minutes."},
            ],
        },
    ]

    transcript = build_display_transcript(messages)
    kinds = [entry["kind"] for entry in transcript]

    assert kinds == ["message", "search", "question", "answer"]
    assert transcript[0]["text"] == "raw data from vital monitors"
    assert "vital signs" in transcript[1]["text"]
    assert transcript[2]["text"] == "Continuous or periodic readings?"
    assert transcript[3]["text"] == "Periodic, every 5 minutes."


def test_build_display_transcript_skips_search_result_dumps():
    messages = [
        {"role": "user", "content": "x"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "t1", "name": "search_fhir_kb", "input": {"query": "q"}},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "- Observation: ..."}],
        },
    ]

    transcript = build_display_transcript(messages)
    assert [e["kind"] for e in transcript] == ["message", "search"]
