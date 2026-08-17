"""Hand-picked golden use cases for the regression eval.

Each in-scope case names a small set of resources that should obviously
appear somewhere in the answer (must_have or potentially_needed) for that
domain -- not an exhaustive expected set, since categorization is somewhat
judgment-dependent. This checks retrieval/generation hasn't regressed on
domains a reviewer already agreed are correct, not exact-match correctness.
"""

GOLDEN_CASES = [
    {
        "use_case": "raw data from vital monitors",
        "expect_present": ["Observation"],
    },
    {
        "use_case": "supply orders and charges",
        "expect_present": ["SupplyRequest", "ChargeItem"],
    },
    {
        "use_case": "lab results from an external lab system",
        "expect_present": ["Observation", "DiagnosticReport"],
    },
    {
        "use_case": "medication orders from a pharmacy system",
        "expect_present": ["MedicationRequest"],
    },
    {
        "use_case": "appointment scheduling data for outpatient clinics",
        "expect_present": ["Appointment"],
    },
    {
        "use_case": "imaging study reports from a radiology PACS system",
        "expect_present": ["ImagingStudy"],
    },
]

OUT_OF_SCOPE_CASES = [
    "write me a haiku about cats",
    "what's the weather like today",
]
