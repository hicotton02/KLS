from app.status import classify_bill_status, classify_federal_bill_status


def test_signed_bill_is_enacted() -> None:
    result = classify_bill_status(
        bill_status="enrolled",
        last_action="Assigned Chapter Number 42",
        signed_date="2026-03-05T00:00:00",
        chapter_no="0042",
        enrolled_no="16",
    )
    assert result["label"] == "Enacted"
    assert result["outcome"] == "passed"


def test_mirror_bill_is_marked_replaced() -> None:
    result = classify_bill_status(
        bill_status="inactive",
        last_action="H See Mirror Bill SF0001",
        signed_date=None,
        chapter_no="",
        enrolled_no="",
    )
    assert result["label"] == "Mirror Bill Used"
    assert result["outcome"] == "replaced"


def test_failed_bill_is_marked_failed() -> None:
    result = classify_bill_status(
        bill_status="inactive",
        last_action="H Failed Introduction 29-33-0-0-0",
        signed_date=None,
        chapter_no="",
        enrolled_no="",
    )
    assert result["label"] == "Did Not Pass"
    assert result["outcome"] == "failed"


def test_vetoed_bill_is_not_marked_passed_legislature() -> None:
    result = classify_bill_status(
        bill_status="enrolled",
        last_action="Governor Vetoed HEA No. 0035",
        signed_date=None,
        chapter_no="",
        enrolled_no="35",
    )
    assert result["label"] == "Vetoed"
    assert result["outcome"] == "failed"


def test_signed_status_marker_is_enacted() -> None:
    result = classify_bill_status(
        bill_status="House Date Signed by Governor",
        last_action="House Date Signed by Governor",
        signed_date=None,
        chapter_no="",
        enrolled_no="",
    )
    assert result["label"] == "Enacted"
    assert result["outcome"] == "passed"


def test_federal_public_law_is_enacted() -> None:
    result = classify_federal_bill_status("Became Public Law No: 119-7.", law_number="119-7")

    assert result["label"] == "Enacted"
    assert result["outcome"] == "passed"


def test_federal_veto_is_failed() -> None:
    result = classify_federal_bill_status("Vetoed by President.")

    assert result["label"] == "Vetoed"
    assert result["outcome"] == "failed"
