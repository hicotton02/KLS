from app.voting import build_wyoming_roll_calls


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
