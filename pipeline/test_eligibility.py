"""Eligibility parser tests. Inputs are shaped like real registry text."""

import pytest

from pipeline.eligibility import checklist_text, parse_criteria, unescape

STANDARD = """Inclusion Criteria:

* Male or female, age \\>= 18 years
* Body mass index (BMI) \\>= 30.0 kg/m\\^2

Exclusion Criteria:

* HbA1c \\>= 6.5%
* Pregnant or breast-feeding
"""


def texts(items):
    return [i["text"] for i in items]


def test_standard_block():
    r = parse_criteria(STANDARD)
    assert r["parsed"] is True
    assert texts(r["inclusion"]) == ["Male or female, age >= 18 years", "Body mass index (BMI) >= 30.0 kg/m^2"]
    assert texts(r["exclusion"]) == ["HbA1c >= 6.5%", "Pregnant or breast-feeding"]
    assert r["notes"] == []
    assert all(set(i) == {"text"} for i in r["inclusion"] + r["exclusion"])


def test_registry_escapes_are_removed():
    assert unescape("age \\>18 and \\<65, eGFR \\[ml/min\\], 1\\. item, A \\& B") == (
        "age >18 and <65, eGFR [ml/min], 1. item, A & B"
    )


def test_groups_under_a_section():
    r = parse_criteria(
        """Inclusion Criteria:

Main phase:

* Age 18 or older
* BMI 30 or more

Extension phase:

* On the target dose at week 68

Exclusion Criteria:

Main phase:

* HbA1c 6.5% or more
"""
    )
    assert [(i["text"], i["group"]) for i in r["inclusion"]] == [
        ("Age 18 or older", "Main phase"),
        ("BMI 30 or more", "Main phase"),
        ("On the target dose at week 68", "Extension phase"),
    ]
    assert r["exclusion"] == [{"text": "HbA1c 6.5% or more", "group": "Main phase"}]


def test_numbered_lists_and_sub_items():
    r = parse_criteria(
        """Inclusion Criteria:

1. Type 2 diabetes
2. At least one of the following:

   * Prior heart attack
   * Prior stroke
3. Signed consent

Exclusion Criteria:

1. Type 1 diabetes
   a. including latent autoimmune diabetes
"""
    )
    assert texts(r["inclusion"]) == ["Type 2 diabetes", "At least one of the following:", "Signed consent"]
    assert r["inclusion"][1]["sub_items"] == ["Prior heart attack", "Prior stroke"]
    assert r["exclusion"] == [{"text": "Type 1 diabetes", "sub_items": ["including latent autoimmune diabetes"]}]


def test_lettered_sub_items_at_the_same_indent():
    r = parse_criteria("Inclusion Criteria:\n\n1. One of:\na. this\nb. that\n2. Consent\n")
    assert texts(r["inclusion"]) == ["One of:", "Consent"]
    assert r["inclusion"][0]["sub_items"] == ["this", "that"]


def test_wrapped_lines_continue_the_item():
    r = parse_criteria("Inclusion Criteria:\n\n* Adults with obesity\nfor at least one year\n* Consent\n")
    assert texts(r["inclusion"]) == ["Adults with obesity for at least one year", "Consent"]


def test_paragraph_criteria_without_bullets():
    r = parse_criteria(
        "Inclusion Criteria:\n\nAdults aged 18-75.\n\nStable weight.\n\nExclusion Criteria:\n\nPregnancy.\n"
    )
    assert texts(r["inclusion"]) == ["Adults aged 18-75.", "Stable weight."]
    assert texts(r["exclusion"]) == ["Pregnancy."]


@pytest.mark.parametrize(
    "heading, kind",
    [
        ("Inclusion Criteria:", "inclusion"),
        ("INCLUSION CRITERIA", "inclusion"),
        ("Key Inclusion Criteria:", "inclusion"),
        ("Main exclusion criteria:", "exclusion"),
        ("Inclusion criteria :", "inclusion"),
        ("Exclusion:", "exclusion"),
        ("* Inclusion Criteria:", "inclusion"),
        ("**Exclusion Criteria**", "exclusion"),
        ("Non-inclusion criteria:", "exclusion"),
        ("Inclusion\\_Criteria:", "inclusion"),
        ("Exclusion Criteria -", "exclusion"),
    ],
)
def test_heading_variants(heading, kind):
    r = parse_criteria(f"{heading}\n\n* the only item\n")
    assert r["parsed"] is True
    assert texts(r[kind]) == ["the only item"]


def test_heading_with_a_group_label():
    r = parse_criteria(
        "Inclusion Criteria for healthy subjects:\n\n* Age 18-45\n\n"
        "Inclusion criteria for patients\n\n* Type 2 diabetes\n"
    )
    assert [(i["text"], i["group"]) for i in r["inclusion"]] == [
        ("Age 18-45", "for healthy subjects"),
        ("Type 2 diabetes", "for patients"),
    ]


def test_heading_with_inline_text():
    r = parse_criteria("Inclusion criteria: Adults with type 2 diabetes\n\nExclusion criteria Pregnancy or lactation\n")
    assert texts(r["inclusion"]) == ["Adults with type 2 diabetes"]
    assert texts(r["exclusion"]) == ["Pregnancy or lactation"]


def test_sentences_that_mention_criteria_are_not_headings():
    r = parse_criteria(
        """Inclusion Criteria:

* Adults with obesity
* Other protocol-defined inclusion criteria may apply

Exclusion Criteria:

* Other exclusion criteria may apply.
* Inclusion in another interventional trial within 30 days
"""
    )
    assert texts(r["inclusion"]) == ["Adults with obesity", "Other protocol-defined inclusion criteria may apply"]
    assert texts(r["exclusion"]) == [
        "Other exclusion criteria may apply.",
        "Inclusion in another interventional trial within 30 days",
    ]


def test_text_before_the_first_heading_becomes_notes():
    r = parse_criteria("Two groups will be recruited.\n\nInclusion Criteria:\n\n* Age 18+\n")
    assert r["notes"] == ["Two groups will be recruited."]
    assert texts(r["inclusion"]) == ["Age 18+"]


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "   \n",
        "1. Age under 60 years\n2. Admitted to an acute cardiac unit\n",  # a list, but no section headings
        "Ascertainment criteria for probands:\n\n* age 10-20 years\n",
    ],
)
def test_unparseable_text_is_reported_not_guessed(text):
    assert parse_criteria(text) == {"parsed": False, "inclusion": [], "exclusion": [], "notes": []}


def test_only_one_section_is_still_parsed():
    r = parse_criteria("Inclusion Criteria:\n\n* Age 18+\n")
    assert r["parsed"] is True and r["exclusion"] == []


def test_no_criterion_text_is_dropped():
    r = parse_criteria(STANDARD)
    joined = checklist_text(r)
    for phrase in ("age >= 18 years", "BMI", "HbA1c >= 6.5%", "Pregnant or breast-feeding"):
        assert phrase in joined


def test_parser_is_deterministic():
    assert parse_criteria(STANDARD) == parse_criteria(STANDARD)


def test_note_lines_are_notes_not_criteria():
    r = parse_criteria(
        "Inclusion Criteria:\n\n* Adults\n\nExclusion Criteria:\n\n* Pregnancy\n\nNOTE: Other criteria apply\n"
    )
    assert texts(r["exclusion"]) == ["Pregnancy"]
    assert r["notes"] == ["NOTE: Other criteria apply"]


def test_stray_dashes_are_dropped():
    r = parse_criteria(
        "Inclusion Criteria:\n\nAdults on diet therapy -\n\nExclusion Criteria:\n\nKidney disease\n\n\\-\n"
    )
    assert texts(r["inclusion"]) == ["Adults on diet therapy"]
    assert texts(r["exclusion"]) == ["Kidney disease"]
