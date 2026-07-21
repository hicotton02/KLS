from fastapi.testclient import TestClient

from app import main as main_module
from app.db import (
    connect,
    init_db,
    replace_bill_amendments,
    replace_bill_relationships,
    replace_bill_roll_calls,
    update_sync_status,
    upsert_bill,
)
from app.jurisdictions import get_state_jurisdiction
from app.main import _collapse_related_relationships, app


def _assert_no_public_model_metadata(value: object) -> None:
    if isinstance(value, dict):
        assert not ({"generator_model", "interpretation_model", "model", "model_name"} & value.keys())
        for item in value.values():
            _assert_no_public_model_metadata(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_public_model_metadata(item)


def _seed_federal_bill() -> None:
    upsert_bill(
        {
            "state": "us",
            "year": 119,
            "special_session_value": None,
            "bill_num": "HR1",
            "bill_type": "HR",
            "catch_title": "Sample federal bill",
            "sponsor": "Rep. Jordan Example (WY)",
            "bill_title": "A bill to create a sample federal program.",
            "bill_status": "Presented to President.",
            "status_label": "Passed Congress",
            "status_explainer": "The latest official action shows this bill passed both chambers and was sent to the President.",
            "outcome": "passed",
            "last_action": "Presented to President.",
            "last_action_date": "2026-03-15",
            "signed_date": "",
            "effective_date": "",
            "chapter_no": "",
            "enrolled_no": "Introduced in House",
            "sponsor_string_house": None,
            "sponsor_string_senate": None,
            "introduced_path": "https://www.congress.gov/119/bills/hr1/BILLS-119hr1ih.xml",
            "digest_path": None,
            "summary_path": None,
            "current_version_path": "https://www.congress.gov/119/bills/hr1/BILLS-119hr1ih.htm",
            "official_digest_text": "",
            "official_summary_text": "This bill creates a sample federal program and sets basic rules for how it works.",
            "current_bill_text": "SECTION 1. This bill creates a sample federal program.",
            "bill_actions_json": [
                {
                    "statusDate": "2026-03-15",
                    "statusMessage": "Presented to President.",
                    "location": "President",
                }
            ],
            "interpretation_json": {
                "plain_language_title": "Sample federal bill",
                "one_sentence_summary": "This bill creates a sample federal program.",
                "what_it_does": ["It creates a sample federal program."],
                "who_it_affects": ["Federal agencies named in the bill."],
                "terms_to_know": [],
                "limits_and_unknowns": ["This test entry uses stored source text."],
                "fact_check_status": "validated",
                "fact_check_result": "passed",
                "fact_check_version": 1,
                "fact_check_notes": ["Checked against official source text during the last sync."],
            },
            "source_hash": "sample-federal-bill",
            "source_synced_at": "2026-03-16T00:00:00+00:00",
            "created_at": "2026-03-16T00:00:00+00:00",
            "updated_at": "2026-03-16T00:00:00+00:00",
        }
    )


def _seed_state_bill(
    bill_num: str,
    catch_title: str,
    *,
    year: int = 2099,
    sponsor: str = "House Labor Committee",
    outcome: str = "active",
    status_label: str = "Active",
    tags: list[str] | None = None,
    search_blob: str | None = None,
) -> None:
    upsert_bill(
        {
            "state": "wy",
            "year": year,
            "special_session_value": None,
            "bill_num": bill_num,
            "bill_type": bill_num[:2],
            "catch_title": catch_title,
            "sponsor": sponsor,
            "bill_title": catch_title,
            "bill_status": "General File",
            "status_label": status_label,
            "status_explainer": "This is a test bill entry for UI coverage.",
            "outcome": outcome,
            "last_action": "Placed on General File",
            "last_action_date": f"{year}-01-10",
            "signed_date": "",
            "effective_date": "",
            "chapter_no": "",
            "enrolled_no": "",
            "sponsor_string_house": None,
            "sponsor_string_senate": None,
            "introduced_path": None,
            "digest_path": None,
            "summary_path": None,
            "current_version_path": None,
            "official_digest_text": "",
            "official_summary_text": f"{catch_title} summary text.",
            "current_bill_text": f"{catch_title} current text.",
            "bill_actions_json": [],
            "interpretation_json": {
                "plain_language_title": catch_title,
                "one_sentence_summary": f"{catch_title} summary.",
                "what_it_does": [f"{catch_title} changes how the state handles the issue."],
                "who_it_affects": ["People named in the bill."],
                "terms_to_know": [],
                "limits_and_unknowns": [],
                "generator_model": "internal-test-model",
            },
            "bill_tags_json": tags or [],
            "search_blob": search_blob or f"{bill_num} {catch_title} {sponsor} {' '.join(tags or [])}",
            "source_hash": f"{bill_num}-{year}",
            "source_synced_at": f"{year}-01-11T00:00:00+00:00",
            "created_at": f"{year}-01-11T00:00:00+00:00",
            "updated_at": f"{year}-01-11T00:00:00+00:00",
        }
    )


def test_home_lists_jurisdiction_pages() -> None:
    init_db()
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Bills broken down in plain English." in response.text
    assert "Open Wyoming" in response.text
    assert "Open Alabama" in response.text
    assert "Open Alaska" in response.text
    assert "Open Kansas" in response.text
    assert "Open Arizona" in response.text
    assert "Open Arkansas" in response.text
    assert "Open California" in response.text
    assert "Open Georgia" in response.text
    assert "Open Hawaii" in response.text
    assert "Open Florida" in response.text
    assert "Open Idaho" in response.text
    assert "Open Indiana" in response.text
    assert "Open Illinois" in response.text
    assert "Open Iowa" in response.text
    assert "Open Kentucky" in response.text
    assert "Open Louisiana" in response.text
    assert "Open Maine" in response.text
    assert "Open Maryland" in response.text
    assert "Open Massachusetts" in response.text
    assert "Open Michigan" in response.text
    assert "Open Minnesota" in response.text
    assert "Open Mississippi" in response.text
    assert "Open Missouri" in response.text
    assert "Open Montana" in response.text
    assert "Open Nebraska" in response.text
    assert "Open Nevada" in response.text
    assert "Open New Hampshire" in response.text
    assert "Open New Jersey" in response.text
    assert "Open New Mexico" in response.text
    assert "Open New York" in response.text
    assert "Open North Carolina" in response.text
    assert "Open Connecticut" in response.text
    assert "Open Delaware" in response.text
    assert "Open District of Columbia" in response.text
    assert "Open North Dakota" in response.text
    assert "Open Ohio" in response.text
    assert "Open Oklahoma" in response.text
    assert "Open Oregon" in response.text
    assert "Open Pennsylvania" in response.text
    assert "Open Rhode Island" in response.text
    assert "Open South Carolina" in response.text
    assert "Open South Dakota" in response.text
    assert "Open Colorado" in response.text
    assert "Open Tennessee" in response.text
    assert "Open Texas" in response.text
    assert "Open Utah" in response.text
    assert "Open Vermont" in response.text
    assert "Open Virginia" in response.text
    assert "Open Washington" in response.text
    assert "Open West Virginia" in response.text
    assert "Open Wisconsin" in response.text
    assert "Open Federal" in response.text
    assert "Every place listed below has its own page" in response.text
    assert "Available" not in response.text
    assert "environment-badge" not in response.text
    assert '<link rel="canonical" href="https://www.keepinglawsimple.org">' in response.text
    assert 'application/ld+json' in response.text
    assert 'https://www.googletagmanager.com/gtag/js?id=G-W6NEFX21NR' in response.text
    assert "gtag('config', 'G-W6NEFX21NR');" in response.text

    state_labels = [
        "Open Alabama",
        "Open Alaska",
        "Open Arizona",
        "Open Arkansas",
        "Open California",
        "Open Colorado",
        "Open Connecticut",
        "Open Delaware",
        "Open District of Columbia",
        "Open Florida",
        "Open Georgia",
        "Open Hawaii",
        "Open Idaho",
        "Open Illinois",
        "Open Indiana",
        "Open Iowa",
        "Open Kansas",
        "Open Kentucky",
        "Open Louisiana",
        "Open Maine",
        "Open Maryland",
        "Open Massachusetts",
        "Open Michigan",
        "Open Minnesota",
        "Open Mississippi",
        "Open Missouri",
        "Open Montana",
        "Open Nebraska",
        "Open Nevada",
        "Open New Hampshire",
        "Open New Jersey",
        "Open New Mexico",
        "Open New York",
        "Open North Carolina",
        "Open North Dakota",
        "Open Ohio",
        "Open Oklahoma",
        "Open Oregon",
        "Open Pennsylvania",
        "Open Rhode Island",
        "Open South Carolina",
        "Open South Dakota",
        "Open Tennessee",
        "Open Texas",
        "Open Utah",
        "Open Vermont",
        "Open Virginia",
        "Open Washington",
        "Open West Virginia",
        "Open Wisconsin",
        "Open Wyoming",
    ]
    positions = [response.text.index(label) for label in state_labels]
    assert positions == sorted(positions)
    assert response.text.index("Open Federal") > positions[-1]


def test_public_api_exposes_coverage_and_last_scan_without_model_metadata() -> None:
    init_db()
    update_sync_status(
        "wy",
        is_running=False,
        finished_at="2026-07-19T15:42:00+00:00",
        last_success_at="2026-07-19T15:42:00+00:00",
    )
    client = TestClient(app)

    response = client.get("/api/v1/overview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["site_name"] == "Keeping Law Simple"
    wyoming = next(item for item in payload["jurisdictions"] if item["slug"] == "wyoming")
    assert wyoming["last_scanned_at"] == "2026-07-19T15:42:00+00:00"
    _assert_no_public_model_metadata(payload)
    assert response.headers["access-control-allow-origin"] == "*"


def test_public_api_search_area_and_bill_detail() -> None:
    init_db()
    _seed_state_bill("HB2098", "Modern navigation test", year=2098, tags=["education"])
    update_sync_status(
        "wy",
        is_running=False,
        finished_at="2026-07-19T16:05:00+00:00",
        last_success_at="2026-07-19T16:05:00+00:00",
    )
    client = TestClient(app)

    search_response = client.get("/api/v1/search", params={"q": "Modern navigation", "area": "wy"})
    area_response = client.get("/api/v1/areas/wyoming", params={"year": 2098, "limit": 10})
    detail_response = client.get("/api/v1/areas/wyoming/bills/2098/HB2098")

    assert search_response.status_code == 200
    assert search_response.json()["results"][0]["bill_num"] == "HB2098"
    assert area_response.status_code == 200
    assert area_response.json()["bills"][0]["summary"] == "Modern navigation test summary."
    assert area_response.json()["jurisdiction"]["last_scanned_at"] == "2026-07-19T16:05:00+00:00"
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["bill"]["catch_title"] == "Modern navigation test"
    assert detail["interpretation"]["one_sentence_summary"] == "Modern navigation test summary."
    assert detail["bill"]["tags"][0] == {"value": "education", "label": "Education"}
    assert detail["jurisdiction"]["last_scanned_at"] == "2026-07-19T16:05:00+00:00"
    _assert_no_public_model_metadata(search_response.json())
    _assert_no_public_model_metadata(area_response.json())
    _assert_no_public_model_metadata(detail)


def test_public_api_exposes_bill_roll_calls_and_legislator_record() -> None:
    init_db()
    _seed_state_bill("HB2098", "Recorded vote test", year=2098)
    _seed_state_bill("HB2099", "Second recorded vote", year=2098)
    timestamp = "2098-02-21T21:03:17+00:00"
    common_member = {
        "member_key": "wy-2093",
        "source_legislator_id": "2093",
        "legislator_name": "Scott Smith",
        "vote_label": "Smith",
        "party": "R",
        "district": "H05",
    }
    for bill_num, position, vote_id in (("HB2098", "yes", "5361"), ("HB2099", "no", "5362")):
        replace_bill_roll_calls(
            "wy",
            2098,
            bill_num,
            payloads=[
                {
                    "roll_call_key": f"h-{vote_id}",
                    "vote_id": vote_id,
                    "chamber": "H",
                    "vote_date": timestamp,
                    "vote_type": "F",
                    "action": f"H 3rd Reading:{position.title()}",
                    "amendment_number": None,
                    "yes_count": 1 if position == "yes" else 0,
                    "no_count": 1 if position == "no" else 0,
                    "absent_count": 0,
                    "conflict_count": 0,
                    "excused_count": 0,
                    "members": [{**common_member, "vote_position": position}],
                    "source_synced_at": timestamp,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                }
            ],
        )

    client = TestClient(app)
    detail_response = client.get("/api/v1/areas/wyoming/bills/2098/HB2098")
    directory_response = client.get("/api/v1/areas/wyoming/legislators", params={"q": "smith"})
    record_response = client.get("/api/v1/areas/wyoming/legislators/wy-2093")

    assert detail_response.status_code == 200
    roll_call = detail_response.json()["roll_calls"][0]
    assert roll_call["counts"] == {"yes": 1, "no": 0, "absent": 0, "conflict": 0, "excused": 0}
    assert roll_call["members"][0]["name"] == "Scott Smith"
    assert roll_call["members"][0]["profile_href"] == "/area/wyoming/legislators/wy-2093"

    assert directory_response.status_code == 200
    assert directory_response.json()["legislators"][0]["legislator_name"] == "Scott Smith"
    assert directory_response.json()["legislators"][0]["total_votes"] == 2

    assert record_response.status_code == 200
    record = record_response.json()
    assert record["legislator"]["title"] == "Representative"
    assert record["counts"]["yes"] == 1
    assert record["counts"]["no"] == 1
    assert record["counts"]["total"] == 2
    assert {vote["bill_num"] for vote in record["votes"]} == {"HB2098", "HB2099"}
    _assert_no_public_model_metadata(record)


def test_home_shows_compact_sync_status() -> None:
    init_db()
    update_sync_status(
        "co",
        years_json=[2026],
        is_running=True,
        current_year=2026,
        current_bill_num="HB26-1001",
        seen=3,
        updated=1,
        skipped=2,
        interpreted=1,
        validated=1,
        failed=0,
        source_total=576,
        stored_total=25,
        started_at="2026-04-13T20:00:00+00:00",
        last_message="Updated HB26-1001 (2026).",
    )
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Background sync is running for 2026." in response.text
    assert "Checked 3 bills so far." in response.text
    assert "Working on HB26-1001." in response.text
    assert "Stored 25 of 576 official bills so far." in response.text


def test_state_page_has_own_route() -> None:
    init_db()
    _seed_state_bill("SF0007", "Clinic reporting rules.")
    _seed_state_bill("SF0008", "Clinic enforcement penalties.")
    replace_bill_relationships(
        "wy",
        2099,
        [
            {
                "state": "wy",
                "year": 2099,
                "special_session_value_a": None,
                "bill_num_a": "SF0007",
                "special_session_value_b": None,
                "bill_num_b": "SF0008",
                "relationship_type": "complementary",
                "relationship_strength": "high",
                "confidence_score": 0.84,
                "candidate_score": 9.2,
                "needs_human_review": True,
                "pair_summary": "Both bills deal with clinic rules and how they are enforced.",
                "combined_effect": "Together they could tighten both reporting requirements and penalties for the same clinics.",
                "why_review": "The bills touch the same issue from different angles.",
                "bill_a_evidence_json": ["SF0007 adds clinic reporting duties."],
                "bill_b_evidence_json": ["SF0008 adds penalties for breaking clinic rules."],
                "limits_and_unknowns_json": [],
                "heuristic_reasons_json": ["Shared issue tags: healthcare"],
                "analysis_version": 1,
                "source_synced_at": "2099-01-11T00:00:00+00:00",
                "created_at": "2099-01-11T00:00:00+00:00",
                "updated_at": "2099-01-11T00:00:00+00:00",
            }
        ],
    )
    client = TestClient(app)

    response = client.get("/states/wyoming")
    normalized = " ".join(response.text.split())

    assert response.status_code == 200
    assert "plain English breakdown" in response.text
    assert "Bills Worth Reading Together" not in response.text
    assert '<strong><a class="bill-link" href="/states/wyoming/bills/2099/SF0007">Clinic reporting rules.</a></strong>' in normalized
    assert 'index,follow,max-image-preview:large' in response.text


def test_state_page_shows_background_sync_status() -> None:
    init_db()
    _seed_state_bill("HB0001", "School funding update.")
    update_sync_status(
        "wy",
        years_json=[2099],
        is_running=False,
        current_year=2099,
        seen=12,
        updated=4,
        skipped=8,
        interpreted=4,
        validated=4,
        failed=0,
        source_total=12,
        stored_total=12,
        started_at="2026-04-13T19:30:00+00:00",
        finished_at="2026-04-13T19:42:00+00:00",
        last_success_at="2026-04-13T19:42:00+00:00",
        last_message="Last run checked 12 bills, updated 4, and skipped 8 unchanged bills.",
    )
    client = TestClient(app)

    response = client.get("/states/wyoming")

    assert response.status_code == 200
    assert "Background sync last finished 2026-04-13 19:42 UTC." in response.text
    assert "Last run checked 12 bills and updated 4." in response.text
    assert "Stored all 12 official bills." in response.text


def test_state_page_marks_stale_background_sync_as_stalled() -> None:
    init_db()
    _seed_state_bill("HB0001", "School funding update.")
    update_sync_status(
        "wy",
        years_json=[2099],
        is_running=True,
        current_year=2099,
        current_bill_num="HB0001",
        seen=42,
        updated=5,
        skipped=37,
        interpreted=5,
        validated=5,
        failed=0,
        source_total=42,
        stored_total=42,
        started_at="2026-04-16T18:00:00+00:00",
        last_message="Updated HB0001 (2099).",
    )
    with connect() as connection:
        connection.execute(
            "UPDATE sync_status SET updated_at = ?, started_at = ? WHERE state = ?",
            ("2026-04-16T18:05:00+00:00", "2026-04-16T18:00:00+00:00", "wy"),
        )
        connection.commit()
    client = TestClient(app)

    response = client.get("/states/wyoming")

    assert response.status_code == 200
    assert "Background sync for 2099 looks stalled." in response.text
    assert "Last progress was on HB0001." in response.text
    assert "No new progress since 2026-04-16 18:05 UTC." in response.text


def test_filtered_state_page_is_noindex_with_clean_canonical() -> None:
    init_db()
    client = TestClient(app)

    response = client.get("/states/wyoming?q=tax&status=passed")

    assert response.status_code == 200
    assert 'content="noindex,follow,max-image-preview:large"' in response.text
    assert '<link rel="canonical" href="https://www.keepinglawsimple.org/states/wyoming">' in response.text


def test_federal_page_exists() -> None:
    init_db()
    _seed_federal_bill()
    client = TestClient(app)

    response = client.get("/federal")

    assert response.status_code == 200
    assert "This page tracks recent bills in Congress" in response.text
    assert 'content="index,follow,max-image-preview:large"' in response.text
    assert ">Congress<" in response.text


def test_apex_redirect_uses_https_canonical_host() -> None:
    init_db()
    client = TestClient(app)

    response = client.get(
        "https://keepinglawsimple.org/bills/2026/HB0001?status=active",
        headers={"host": "keepinglawsimple.org"},
        follow_redirects=False,
    )

    assert response.status_code == 307
    assert response.headers["location"] == "https://www.keepinglawsimple.org/bills/2026/HB0001?status=active"


def test_legacy_bill_url_redirects_to_state_route() -> None:
    init_db()
    client = TestClient(app)

    response = client.get("/bills/2026/HB0001?special_session=0", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/states/wyoming/bills/2026/HB0001?special_session=0"


def test_robots_txt_points_to_sitemap() -> None:
    init_db()
    client = TestClient(app)

    response = client.get("/robots.txt")

    assert response.status_code == 200
    assert "Disallow: /healthz" in response.text
    assert "Sitemap: https://www.keepinglawsimple.org/sitemap.xml" in response.text


def test_healthz_is_process_only(monkeypatch) -> None:
    def fail_list_years(state: str = "wy") -> list[int]:
        raise AssertionError("healthz should not query app data")

    monkeypatch.setattr(main_module, "list_years", fail_list_years)
    client = TestClient(app)

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_reports_data_health() -> None:
    init_db()
    _seed_state_bill("HB0001", "School funding update.")
    client = TestClient(app)

    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "years": [2099], "latest_year_total": 1}


def test_favicon_is_linked_and_redirected() -> None:
    init_db()
    client = TestClient(app)

    home = client.get("/")
    favicon = client.get("/favicon.ico", follow_redirects=False)

    assert home.status_code == 200
    assert '/static/favicon.svg?v=' in home.text
    assert '/static/styles.css?v=' in home.text
    assert favicon.status_code == 307
    assert favicon.headers["location"] == "/static/favicon.svg"


def test_sitemap_index_lists_child_sitemaps() -> None:
    init_db()
    client = TestClient(app)

    response = client.get("/sitemap.xml")

    assert response.status_code == 200
    assert "<sitemapindex" in response.text
    assert "https://www.keepinglawsimple.org/sitemaps/core.xml" in response.text
    assert "https://www.keepinglawsimple.org/sitemaps/wyoming.xml" in response.text
    assert "https://www.keepinglawsimple.org/sitemaps/federal.xml" in response.text


def test_state_sitemap_lists_state_and_bill_pages() -> None:
    init_db()
    _seed_state_bill("HB0001", "School funding update.")
    client = TestClient(app)

    response = client.get("/sitemaps/wyoming.xml")

    assert response.status_code == 200
    assert "<urlset" in response.text
    assert "https://www.keepinglawsimple.org/states/wyoming" in response.text
    assert "https://www.keepinglawsimple.org/states/wyoming/bills/2099/HB0001" in response.text


def test_federal_bill_page_has_own_route() -> None:
    init_db()
    _seed_federal_bill()
    client = TestClient(app)

    response = client.get("/federal/bills/119/HR1")

    assert response.status_code == 200
    assert "Sample federal bill" in response.text
    assert "Congress.gov bill page" in response.text
    assert '<link rel="canonical" href="https://www.keepinglawsimple.org/federal/bills/119/HR1">' in response.text


def test_related_relationships_are_collapsed_per_peer_bill() -> None:
    jurisdiction = get_state_jurisdiction("wyoming")

    assert jurisdiction is not None

    collapsed = _collapse_related_relationships(
        jurisdiction,
        2026,
        "HB0008",
        None,
        [
            {
                "year": 2026,
                "bill_num_a": "HB0008",
                "special_session_key_a": -1,
                "special_session_value_a": None,
                "bill_a_catch_title": "Licensing update.",
                "bill_a_outcome": "active",
                "bill_a_status_label": "Active",
                "bill_num_b": "HB0009",
                "special_session_key_b": -1,
                "special_session_value_b": None,
                "bill_b_catch_title": "Licensing penalties.",
                "bill_b_outcome": "active",
                "bill_b_status_label": "Active",
                "relationship_strength": "medium",
                "needs_human_review": False,
                "pair_summary": "Both bills deal with the same licensing rules.",
                "combined_effect": "Together they could expand the rule and add enforcement.",
                "why_review": "One bill changes the rules while the other adds penalties.",
                "bill_a_evidence_json": ["HB0008 changes the main licensing process."],
                "bill_b_evidence_json": ["HB0009 adds penalties for violations."],
            },
            {
                "year": 2026,
                "bill_num_a": "HB0008",
                "special_session_key_a": -1,
                "special_session_value_a": None,
                "bill_a_catch_title": "Licensing update.",
                "bill_a_outcome": "active",
                "bill_a_status_label": "Active",
                "bill_num_b": "HB0009",
                "special_session_key_b": -1,
                "special_session_value_b": None,
                "bill_b_catch_title": "Licensing penalties.",
                "bill_b_outcome": "active",
                "bill_b_status_label": "Active",
                "relationship_strength": "high",
                "needs_human_review": True,
                "pair_summary": "The bills also hit the same licensed businesses.",
                "combined_effect": "Taken together, licensed businesses could face tighter rules and stronger penalties.",
                "why_review": "The overlap could matter more if both bills pass.",
                "bill_a_evidence_json": ["HB0008 changes the main licensing process."],
                "bill_b_evidence_json": ["HB0009 lets the state enforce those rules with penalties."],
            },
        ],
    )

    assert len(collapsed) == 1
    assert collapsed[0]["peer"]["bill_num"] == "HB0009"
    assert collapsed[0]["peer_href"] == "/states/wyoming/bills/2026/HB0009"
    assert collapsed[0]["relationship_strength"] == "high"
    assert collapsed[0]["needs_human_review"] is True
    assert collapsed[0]["pair_summaries"] == [
        "Both bills deal with the same licensing rules.",
        "The bills also hit the same licensed businesses.",
    ]
    assert collapsed[0]["combined_effects"] == [
        "Together they could expand the rule and add enforcement.",
        "Taken together, licensed businesses could face tighter rules and stronger penalties.",
    ]
    assert collapsed[0]["why_reviews"] == [
        "One bill changes the rules while the other adds penalties.",
        "The overlap could matter more if both bills pass.",
    ]
    assert collapsed[0]["evidence_items"] == [
        "HB0008 changes the main licensing process.",
        "HB0009 adds penalties for violations.",
        "HB0009 lets the state enforce those rules with penalties.",
    ]


def test_bill_detail_shows_tags_and_amendments() -> None:
    init_db()
    _seed_state_bill(
        "HB0400",
        "Firearm protections for small businesses.",
        year=2098,
        tags=["firearms", "small-business"],
        search_blob="HB0400 firearm protections small business",
    )
    replace_bill_amendments(
        "wy",
        2098,
        "HB0400",
        payloads=[
            {
                "state": "wy",
                "year": 2098,
                "special_session_value": None,
                "bill_num": "HB0400",
                "amendment_number": "HB0400H2001",
                "chamber": "H",
                "reading_order": "2nd reading",
                "sequence": "001",
                "status": "Adopted",
                "sponsor": "Representative Tester",
                "document_url": "https://www.wyoleg.gov/2098/Amends/HB0400H2001.pdf",
                "document_text": "Delete the old line and add a new business exception.",
                "interpretation_json": {
                    "one_sentence_summary": "This amendment adds a business exception to the bill.",
                    "changes": ["It adds a new exception for some small businesses."],
                    "limits_and_unknowns": [],
                    "generator_model": "internal-test-model",
                },
                "source_hash": "hb0400-h2001",
                "source_synced_at": "2098-01-11T00:00:00+00:00",
                "created_at": "2098-01-11T00:00:00+00:00",
                "updated_at": "2098-01-11T00:00:00+00:00",
            }
        ],
    )
    client = TestClient(app)

    response = client.get("/states/wyoming/bills/2098/HB0400")

    assert response.status_code == 200
    assert "Firearms" in response.text
    assert "Small Business" in response.text
    assert "HB0400H2001" in response.text
    assert "This amendment adds a business exception to the bill." in response.text

    api_response = client.get("/api/v1/areas/wyoming/bills/2098/HB0400")
    assert api_response.status_code == 200
    _assert_no_public_model_metadata(api_response.json())


def test_state_archive_note_and_tag_filter_render() -> None:
    init_db()
    _seed_state_bill("HB0500", "Archived tax bill.", year=2097, tags=["taxes"], search_blob="HB0500 taxes archived")
    _seed_state_bill("HB0501", "Current tax bill.", year=2098, tags=["taxes"], search_blob="HB0501 taxes current")
    client = TestClient(app)

    response = client.get("/states/wyoming?year=2097&tag=taxes")

    assert response.status_code == 200
    assert "archived 2097 session" in response.text
    assert 'name="tag"' in response.text
    assert "Archived tax bill." in response.text


def test_search_page_finds_bills_by_tag_and_sponsor() -> None:
    init_db()
    _seed_state_bill(
        "HB0600",
        "Clinic licensing rules.",
        year=2098,
        sponsor="Representative Smith",
        tags=["healthcare"],
        search_blob="HB0600 clinic licensing healthcare Representative Smith",
    )
    client = TestClient(app)

    response = client.get("/search?q=smith&tag=healthcare&area=wy")

    assert response.status_code == 200
    assert "Clinic licensing rules." in response.text
    assert "Representative Smith" in response.text
    assert "Healthcare" in response.text


def test_search_page_accepts_blank_year_and_tag_only() -> None:
    init_db()
    _seed_state_bill(
        "HB0700",
        "Pregnancy reporting rules.",
        year=2098,
        sponsor="Representative Carter",
        tags=["abortion"],
        search_blob="HB0700 pregnancy reporting abortion Carter",
    )
    client = TestClient(app)

    response = client.get("/search?q=&area=all&year=&status=all&tag=abortion")

    assert response.status_code == 200
    assert "Pregnancy reporting rules." in response.text
    assert "Abortion" in response.text


def test_search_page_handles_comma_separated_query_terms() -> None:
    init_db()
    _seed_state_bill(
        "HB0126",
        "Human heartbeat act.",
        year=2098,
        sponsor="Representative Neiman",
        tags=["abortion"],
        search_blob="HB0126 Human heartbeat act Representative Neiman abortion heartbeat pregnancy",
    )
    _seed_state_bill(
        "HB0200",
        "Firearm carry updates.",
        year=2098,
        sponsor="Representative Carter",
        tags=["firearms"],
        search_blob="HB0200 Firearm carry updates Representative Carter firearms gun carry",
    )
    client = TestClient(app)

    response = client.get("/search?q=HB0126,%20Neiman,%20firearms,%20b")

    assert response.status_code == 200
    assert "Human heartbeat act." in response.text
    assert "Firearm carry updates." in response.text
    assert "No bills matched." not in response.text
