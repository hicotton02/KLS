from collections import Counter

from app.voting import build_wyoming_roll_calls
from app.wyoming_api import WyomingApiClient


def test_build_wyoming_roll_calls_resolves_initialed_and_unique_names() -> None:
    rosters = {
        "H": [
            {"firstName": "Gary", "lastName": "Brown", "name": "Gary Brown", "legID": 2140, "party": "R", "district": "H41"},
            {"firstName": "Landon", "lastName": "Brown", "name": "Landon Brown", "legID": 2034, "party": "R", "district": "H09"},
            {"firstName": "J.T.", "lastName": "Larson", "name": "J.T. Larson", "legID": 2096, "party": "R", "district": "H17"},
            {"firstName": "Scott", "lastName": "Smith", "name": "Scott Smith", "legID": 2093, "party": "R", "district": "H05"},
            {"firstName": "Mike", "lastName": "Yin", "name": "Mike Yin", "legID": 2061, "party": "D", "district": "H16"},
        ]
    }
    detail = {
        "year": 2026,
        "bill": "HB0083",
        "specialSessionValue": None,
        "rollCalls": [
            {
                "voteID": 5361,
                "chamber": "H",
                "voteDate": "2026-02-21T21:03:17",
                "yesVotesCount": 5,
                "yesVotes": "Brown, G, Brown, L, Larson, JT, Smith, Yin",
                "noVotesCount": 0,
                "noVotes": "",
                "absentVotesCount": 0,
                "absentVotes": "",
                "conflictVotesCount": 0,
                "conflictVotes": "",
                "excusedVotesCount": 0,
                "excusedVotes": "",
                "voteType": "F",
                "action": "H 3rd Reading:Passed 5-0-0-0-0",
            }
        ],
    }

    roll_calls = build_wyoming_roll_calls(detail, rosters, timestamp="2026-07-21T00:00:00+00:00")

    assert len(roll_calls) == 1
    assert roll_calls[0]["roll_call_key"] == "h-5361"
    assert roll_calls[0]["yes_count"] == 5
    assert [member["legislator_name"] for member in roll_calls[0]["members"]] == [
        "Gary Brown",
        "J.T. Larson",
        "Landon Brown",
        "Mike Yin",
        "Scott Smith",
    ]
    smith = next(member for member in roll_calls[0]["members"] if member["legislator_name"] == "Scott Smith")
    assert smith == {
        "member_key": "wy-2093",
        "source_legislator_id": "2093",
        "legislator_name": "Scott Smith",
        "vote_label": "Smith",
        "party": "R",
        "district": "H05",
        "vote_position": "yes",
    }


def test_build_wyoming_roll_calls_prefers_structured_member_votes() -> None:
    detail = {
        "year": 2026,
        "bill": "SF0001",
        "rollCalls": [
            {
                "voteID": 41,
                "chamber": "S",
                "voteDate": "2026-02-10T12:00:00",
                "rollCallLegVoteDtos": [
                    {"legislator": "Alice Able", "vote": "Yea"},
                    {"legislator": "Bob Baker", "vote": "Nay"},
                ],
            }
        ],
    }
    rosters = {
        "S": [
            {"firstName": "Alice", "lastName": "Able", "name": "Alice Able", "legID": 1, "party": "R", "district": "S01"},
            {"firstName": "Bob", "lastName": "Baker", "name": "Bob Baker", "legID": 2, "party": "D", "district": "S02"},
        ]
    }

    roll_calls = build_wyoming_roll_calls(detail, rosters, timestamp="2026-07-21T00:00:00+00:00")

    assert [(member["member_key"], member["vote_position"]) for member in roll_calls[0]["members"]] == [
        ("wy-1", "yes"),
        ("wy-2", "no"),
    ]


def test_build_wyoming_roll_calls_handles_reused_vote_ids_and_exact_duplicates() -> None:
    shared = {
        "voteID": 77,
        "chamber": "H",
        "voteType": "F",
        "yesVotesCount": 1,
        "yesVotes": "Smith",
        "noVotesCount": 0,
        "noVotes": "",
    }
    detail = {
        "year": 2026,
        "bill": "HB0178",
        "rollCalls": [
            {**shared, "voteDate": "2026-03-10T10:00:00", "action": "H 3rd Reading:Passed"},
            {**shared, "voteDate": "2026-03-11T10:00:00", "action": "Veto Override"},
            {**shared, "voteDate": "2026-03-11T10:00:00", "action": "Veto Override"},
        ],
    }
    rosters = {
        "H": [
            {
                "firstName": "Scott",
                "lastName": "Smith",
                "name": "Scott Smith",
                "legID": 2093,
                "party": "R",
                "district": "H05",
            }
        ]
    }

    first = build_wyoming_roll_calls(detail, rosters, timestamp="2026-07-21T00:00:00+00:00")
    second = build_wyoming_roll_calls(detail, rosters, timestamp="2026-07-21T00:00:00+00:00")

    assert len(first) == 2
    assert len({roll_call["roll_call_key"] for roll_call in first}) == 2
    assert all(roll_call["roll_call_key"].startswith("h-77-") for roll_call in first)
    assert [roll_call["roll_call_key"] for roll_call in first] == [
        roll_call["roll_call_key"] for roll_call in second
    ]


def test_build_wyoming_roll_calls_resolves_suffixes_and_chamber_titles() -> None:
    detail = {
        "year": 2023,
        "bill": "HB0001",
        "rollCalls": [
            {
                "voteID": 99,
                "chamber": "H",
                "voteDate": "2023-02-01T11:00:00",
                "yesVotesCount": 2,
                "yesVotes": "Burkhart, Jr, Speaker Sommers",
                "noVotesCount": 0,
                "noVotes": "",
            }
        ],
    }
    rosters = {
        "H": [
            {
                "firstName": "Donald",
                "lastName": "Burkhart",
                "name": "Donald E Burkhart Jr",
                "legID": 1973,
                "district": "H15",
            },
            {
                "firstName": "Albert",
                "lastName": "Sommers",
                "name": "Albert P Sommers Jr.",
                "legID": 1991,
                "district": "H20",
            },
        ]
    }

    roll_calls = build_wyoming_roll_calls(detail, rosters, timestamp="2026-07-21T00:00:00+00:00")

    assert [(member["member_key"], member["legislator_name"]) for member in roll_calls[0]["members"]] == [
        ("wy-1991", "Albert P Sommers Jr."),
        ("wy-1973", "Donald E Burkhart Jr"),
    ]


def test_normalize_historical_legislator_name() -> None:
    normalized = WyomingApiClient._normalize_historical_legislator(
        {
            "legID": "1973",
            "name": "Burkhart, Jr, Donald E",
            "district": "H15",
        }
    )

    assert normalized == {
        "firstName": "Donald",
        "lastName": "Burkhart",
        "name": "Donald E Burkhart Jr",
        "legID": "1973",
        "party": None,
        "district": "H15",
    }


def test_build_wyoming_roll_calls_reconciles_concatenated_vote_snapshots() -> None:
    roster = [
        {
            "firstName": name,
            "lastName": name,
            "name": name,
            "legID": index,
            "district": f"S{index:02d}",
        }
        for index, name in enumerate(("Able", "Baker", "Clark", "Dover", "Ellis"), start=1)
    ]
    detail = {
        "year": 2020,
        "bill": "SF0010",
        "rollCalls": [
            {
                "voteID": 114,
                "chamber": "S",
                "voteDate": "2020-02-10T15:58:18",
                "yesVotesCount": 3,
                "yesVotes": "Able, Baker, Clark, Able, Dover, Ellis",
                "noVotesCount": 2,
                "noVotes": "Dover, Ellis, Baker, Clark",
                "absentVotesCount": 0,
                "absentVotes": "",
                "conflictVotesCount": 0,
                "conflictVotes": "",
                "excusedVotesCount": 0,
                "excusedVotes": "",
            }
        ],
    }

    roll_call = build_wyoming_roll_calls(
        detail,
        {"S": roster},
        timestamp="2026-07-21T00:00:00+00:00",
    )[0]
    positions = Counter(member["vote_position"] for member in roll_call["members"])

    assert positions == {"yes": 3, "no": 2}
    assert len({member["member_key"] for member in roll_call["members"]}) == 5
