from __future__ import annotations

import io
import zipfile

import httpx

from app.pennsylvania_api import PennsylvaniaApiClient
from app.settings import get_settings


def _bill_history_zip(xml_text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("PA-Bill-History-2025-RegularSession.xml", xml_text)
    return buffer.getvalue()


def test_fetch_year_bills_reads_bill_history_export() -> None:
    settings = get_settings()
    api = PennsylvaniaApiClient(settings)
    api.close()

    xml_text = """
    <historyExport>
      <session year="2025" session="0">
        <bill>
          <sessionYear>2025</sessionYear>
          <session>0</session>
          <body>H</body>
          <type description="House Bill">B</type>
          <subType>B</subType>
          <number>0017</number>
          <shortTitle>School handwriting requirement.</shortTitle>
          <cosponsorshipMemo memoUrl="https://www.palegis.us/house/co-sponsorship/memo?memoID=1&amp;document=HB17">Mandating Cursive Handwriting</cosponsorshipMemo>
          <sponsors>
            <sponsor sequenceNumber="01">WATRO</sponsor>
            <sponsor sequenceNumber="02">COOPER</sponsor>
          </sponsors>
          <printersNumberHistory>
            <number sequence="01" billTextPdfUrl="https://www.palegis.us/legislation/bills/text/PDF/2025/0/HB0017/PN0002">0002</number>
            <number sequence="02" billTextPdfUrl="https://www.palegis.us/legislation/bills/text/PDF/2025/0/HB0017/PN0001">0001</number>
          </printersNumberHistory>
          <actionHistory>
            <action sequence="01" actionChamber="H">
              <verb>Referred to</verb>
              <committee>EDUCATION</committee>
              <date>01/08/25</date>
              <fullAction>Referred to EDUCATION, Jan. 8, 2025</fullAction>
            </action>
            <action sequence="02" actionChamber="H">
              <verb>Act No.</verb>
              <date>07/07/25</date>
              <fullAction>Act No. 36, July 7, 2025</fullAction>
            </action>
          </actionHistory>
          <amendments>
            <amendment chamber="H" date="06/23/25" number="00770" amendmentUrl="https://www.palegis.us/legislation/amendments/text/2025/0/A00770">A00770</amendment>
          </amendments>
        </bill>
        <bill>
          <sessionYear>2025</sessionYear>
          <session>0</session>
          <body>S</body>
          <type description="Senate Resolution">R</type>
          <subType>C</subType>
          <number>0021</number>
          <shortTitle>Concurrent resolution on Federal convention.</shortTitle>
          <sponsors>
            <sponsor sequenceNumber="01">ARGALL</sponsor>
          </sponsors>
          <printersNumberHistory>
            <number sequence="01" billTextPdfUrl="https://www.palegis.us/legislation/bills/text/PDF/2025/0/SCR0021/PN0100">0100</number>
          </printersNumberHistory>
          <actionHistory>
            <action sequence="01" actionChamber="S">
              <verb>Introduced and referred</verb>
              <committee>RULES</committee>
              <date>03/01/25</date>
              <fullAction>Introduced and referred to RULES, March 1, 2025</fullAction>
            </action>
          </actionHistory>
          <amendments />
        </bill>
      </session>
    </historyExport>
    """

    archive_bytes = _bill_history_zip(xml_text)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/data/file"
        assert request.url.params.get("documentType") == "BillHistoryData"
        assert request.url.params.get("session") == "2025_0"
        return httpx.Response(
            200,
            content=archive_bytes,
            headers={"content-type": "application/xml"},
            request=request,
        )

    api.client = httpx.Client(
        base_url=settings.pennsylvania_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2025)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["HB17", "SCR21"]
    assert items[0]["sponsor"] == "WATRO"
    assert items[0]["billStatus"] == "Act No. 36, July 7, 2025"
    assert items[0]["lastActionDate"] == "2025-07-07"
    assert items[0]["chapter"] == "36"
    assert items[0]["detailPath"] == "https://www.palegis.us/legislation/bills/2025/HB17"
    assert items[0]["currentVersionPath"].endswith("/HB0017/PN0002")


def test_fetch_bill_detail_uses_source_record_and_extracts_amendments() -> None:
    settings = get_settings()
    api = PennsylvaniaApiClient(settings)

    source_record = {
        "bill": "HB17",
        "billNum": "HB17",
        "billType": "HB",
        "catchTitle": "School handwriting requirement.",
        "sponsor": "WATRO",
        "billTitle": "School handwriting requirement.",
        "billStatus": "Act No. 36, July 7, 2025",
        "lastAction": "Act No. 36, July 7, 2025",
        "lastActionDate": "2025-07-07",
        "signedDate": "2025-07-07",
        "effectiveDate": "",
        "chapter": "36",
        "enrolledNumber": "HB17",
        "sponsorStringHouse": "WATRO, COOPER",
        "sponsorStringSenate": None,
        "introduced": "https://www.palegis.us/legislation/bills/text/PDF/2025/0/HB0017/PN0001",
        "digest": "https://www.palegis.us/house/co-sponsorship/memo?memoID=1&document=HB17",
        "summary": "https://www.palegis.us/legislation/bills/2025/HB17",
        "currentVersionPath": "https://www.palegis.us/legislation/bills/text/PDF/2025/0/HB0017/PN0002",
        "currentVersionFingerprint": "https://www.palegis.us/legislation/bills/text/PDF/2025/0/HB0017/PN0002|0002|Act No. 36, July 7, 2025|2025-07-07|36|1",
        "summaryHTML": "<p>School handwriting requirement.</p>",
        "digestHTML": "<p>Co-sponsorship memo: Mandating Cursive Handwriting</p><p>Latest action: Act No. 36, July 7, 2025</p>",
        "currentBillHTML": "",
        "billActions": [
            {"statusDate": "2025-01-08", "statusMessage": "Referred to EDUCATION, Jan. 8, 2025", "location": "EDUCATION"},
            {"statusDate": "2025-07-07", "statusMessage": "Act No. 36, July 7, 2025", "location": "H"},
        ],
        "amendments": [
            {
                "amendmentNumber": "A00770",
                "house": "H",
                "order": "06/23/25",
                "sequence": "0001",
                "status": "06/23/25",
                "sponsor": "",
                "documentUrl": "https://www.palegis.us/legislation/amendments/text/2025/0/A00770",
            }
        ],
        "officialPage": "https://www.palegis.us/legislation/bills/2025/HB17",
    }

    try:
        detail = api.fetch_bill_detail(
            "https://www.palegis.us/legislation/bills/2025/HB17",
            {"sourceRecord": source_record},
        )
    finally:
        api.close()

    assert detail["bill"] == "HB17"
    assert detail["sponsor"] == "WATRO"
    assert detail["chapter"] == "36"
    assert detail["signedDate"] == "2025-07-07"
    assert detail["introduced"].endswith("/HB0017/PN0001")
    assert detail["currentVersionPath"].endswith("/HB0017/PN0002")
    assert len(detail["billActions"]) == 2
    assert len(detail["amendments"]) == 1
    assert detail["amendments"][0]["documentUrl"].endswith("/A00770")
