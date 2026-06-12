from app.tagging import extract_bill_tags, tag_label


def test_extract_bill_tags_finds_high_profile_topics() -> None:
    tags = extract_bill_tags(
        catch_title="Firearm protections for small businesses.",
        interpretation={
            "one_sentence_summary": "This bill changes gun rules for some small business owners.",
            "what_it_does": [],
            "who_it_affects": [],
            "terms_to_know": [],
        },
    )

    assert "firearms" in tags
    assert "small-business" in tags


def test_extract_bill_tags_ignores_loose_boilerplate_matches() -> None:
    tags = extract_bill_tags(
        catch_title="Clean Air and Geoengineering Prohibition Act.",
        sponsor="Agriculture",
        official_summary_text=(
            "AN ACT relating to environmental quality; prohibiting the release of atmospheric contaminants "
            "into the airspace above Wyoming; providing criminal penalties; providing positions; "
            "providing appropriations; requiring rulemaking."
        ),
        interpretation={
            "one_sentence_summary": (
                "This bill aims to prohibit the release of substances into Wyoming's airspace that could alter weather, climate, or sunlight."
            ),
            "what_it_does": [
                "Prohibits intentional release of atmospheric contaminants to alter weather, climate, or solar radiation."
                " It also establishes criminal penalties for violations."
            ],
            "who_it_affects": [],
            "terms_to_know": [],
        },
    )

    assert "agriculture" in tags
    assert "budget" not in tags
    assert "crime" not in tags
    assert "labor" not in tags
    assert "technology" not in tags
    assert "water" not in tags


def test_tag_label_formats_public_text() -> None:
    assert tag_label("small-business") == "Small Business"
