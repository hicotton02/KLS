from __future__ import annotations

import httpx

from app.delaware_api import DelawareApiClient
from app.settings import get_settings


def test_fetch_year_bills_dedupes_official_feed_union() -> None:
    settings = get_settings()
    api = DelawareApiClient(settings)
    api.close()

    introduced_payload = {
        "Items": [
            {
                "Title": "HB 270",
                "LongTitle": "AN ACT TO AMEND TITLE 29 OF THE DELAWARE CODE.",
                "Synopsis": "Makes a narrow administrative change.",
                "Link": "https://legis.delaware.gov/BillDetail?legislationId=142808",
                "IntroducedDate": "2026-01-22T12:00:00",
            }
        ]
    }
    house_passed_payload = {
        "Items": [
            {
                "Title": "HB 270",
                "LongTitle": "AN ACT TO AMEND TITLE 29 OF THE DELAWARE CODE RELATING TO CAPITAL PROJECTS.",
                "Synopsis": "Makes a narrow administrative change and updates capital projects.",
                "Link": "https://legis.delaware.gov/BillDetail?legislationId=142808",
                "IntroducedDate": "2026-01-22T12:00:00",
            },
            {
                "Title": "SB 10",
                "LongTitle": "AN ACT TO AMEND TITLE 18 OF THE DELAWARE CODE.",
                "Synopsis": "Adjusts insurance coverage standards.",
                "Link": "https://legis.delaware.gov/BillDetail?legislationId=142900",
                "IntroducedDate": "2026-02-01T08:00:00",
            },
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/json/JsonFeed/IntroducedLegislation"):
            return httpx.Response(200, json=introduced_payload, request=request)
        if request.url.path.endswith("/json/JsonFeed/HousePassedLegislation"):
            return httpx.Response(200, json=house_passed_payload, request=request)
        if "/json/JsonFeed/" in request.url.path:
            return httpx.Response(200, json={"Items": []}, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.delaware_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["HB270", "SB10"]
    assert items[0]["detailPath"] == "https://legis.delaware.gov/BillDetail?legislationId=142808"
    assert items[0]["catchTitle"] == "AN ACT TO AMEND TITLE 29 OF THE DELAWARE CODE RELATING TO CAPITAL PROJECTS."


def test_fetch_bill_detail_extracts_documents_actions_and_related_amendments() -> None:
    settings = get_settings()
    api = DelawareApiClient(settings)
    api.close()

    main_detail_html = """
    <html><body>
      <h2>House Bill 270</h2>
      <section class="section-short">
        <h3 class="section-head">Progress</h3>
        <div class="info-horizontal">
          <div class="info-group">
            <label class="info-label">Status:</label>
            <div class="info-value">Signed 1/30/26</div>
          </div>
          <div class="info-group">
            <label class="info-label">What typically happens next?</label>
            <div class="info-value">Becomes effective upon date of signature of the Governor or upon date specified</div>
          </div>
        </div>
      </section>
      <section class="section-short">
        <h3 class="section-head">Details</h3>
        <div class="info-horizontal">
          <div class="info-group">
            <label class="info-label">Introduced on:</label>
            <div class="info-value">1/22/26</div>
          </div>
          <div class="info-group">
            <label class="info-label">Primary Sponsor:</label>
            <div class="info-value"><a href="/members/1">Heffernan</a></div>
          </div>
          <div class="info-group">
            <label class="info-label">Long Title:</label>
            <div class="info-value">AN ACT TO AMEND THE LAWS OF DELAWARE RELATING TO CAPITAL IMPROVEMENTS.</div>
          </div>
          <div class="info-group">
            <label class="info-label">Original Synopsis:</label>
            <div class="info-value">This Act makes several capital project updates.</div>
          </div>
        </div>
      </section>
      <section class="section-short">
        <h3 class="section-head">Text</h3>
        <div class="info-horizontal">
          <div class="info-group">
            <label class="info-label">Original / Not Amended:</label>
            <div class="info-value">
              <a href="/json/BillDetail/GenerateHtmlDocument?legislationId=142808&amp;legislationTypeId=1&amp;docTypeId=2&amp;legislationName=HB270">View HTML</a>
              <a href="/json/BillDetail/GetPdfDocument?fileAttachmentId=652282">View PDF</a>
            </div>
          </div>
        </div>
      </section>
      <section class="section-short">
        <h3 class="section-head">Session Laws</h3>
        <div class="info-horizontal">
          <div class="info-group">
            <label class="info-label">Session Law:</label>
            <div class="info-value">
              <a href="/json/BillDetail/GenerateHtmlDocumentSessionLaw?sessionLawId=142808&amp;docTypeId=13&amp;sessionLawName=chp233">View HTML</a>
            </div>
          </div>
        </div>
      </section>
    </body></html>
    """
    amendment_detail_html = """
    <html><body>
      <h2>Senate Amendment 1 to Senate Bill 283</h2>
      <section class="section-short">
        <h3 class="section-head">Progress</h3>
        <div class="info-horizontal">
          <div class="info-group">
            <label class="info-label">Status:</label>
            <div class="info-value">Introduced 4/21/26</div>
          </div>
        </div>
      </section>
      <section class="section-short">
        <h3 class="section-head">Text</h3>
        <div class="info-horizontal">
          <div class="info-group">
            <label class="info-label">Original / Not Amended:</label>
            <div class="info-value">
              <a href="/json/BillDetail/GenerateHtmlDocument?legislationId=143130&amp;legislationTypeId=1&amp;docTypeId=2&amp;legislationName=SA1">View HTML</a>
            </div>
          </div>
        </div>
      </section>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/BillDetail":
            legislation_id = request.url.params.get("legislationId")
            if legislation_id == "142808":
                return httpx.Response(200, text=main_detail_html, request=request)
            if legislation_id == "143130":
                return httpx.Response(200, text=amendment_detail_html, request=request)
        if request.method == "POST" and request.url.path.endswith("/GetRecentReportsByLegislationId"):
            return httpx.Response(
                200,
                json={
                    "Data": [
                        {
                            "ActionDescription": "Introduced and Assigned to Capital Infrastructure Committee in House",
                            "OccuredAtDateTime": "1/22/26",
                        },
                        {
                            "ActionDescription": "Signed by Governor",
                            "OccuredAtDateTime": "1/30/26",
                        },
                    ]
                },
                request=request,
            )
        if request.method == "POST" and request.url.path.endswith("/GetVotingReportsByLegislationId"):
            return httpx.Response(
                200,
                json={
                    "Data": [
                        {
                            "ChamberName": "House",
                            "RollCallResultTypeName": "Passed",
                            "TakenAtDateTime": "2026-01-28T20:41:27.57Z",
                            "YesTotal": 41,
                            "NoTotal": 0,
                        }
                    ]
                },
                request=request,
            )
        if request.method == "POST" and request.url.path.endswith("/GetRelatedAmendmentsByLegislationId"):
            return httpx.Response(
                200,
                json={
                    "Data": [
                        {
                            "AmendmentLegislationId": 143130,
                            "ShortAmendmentCode": "SA 1",
                            "AmendmentCode": "SA 1 to SB 283",
                            "PublicStatusName": "Introduced",
                            "PrimarySponsorShortName": "Pinkney",
                            "AmendmentOrder": "1",
                            "AmendmentDepth": 1,
                        }
                    ]
                },
                request=request,
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.delaware_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail(
            "https://legis.delaware.gov/BillDetail?legislationId=142808",
            {"billNum": "HB270", "displayCode": "HB 270", "legislationId": 142808, "synopsis": "This Act makes several capital project updates."},
        )
    finally:
        api.close()

    assert detail["bill"] == "HB270"
    assert detail["billStatus"] == "Signed 1/30/26"
    assert detail["lastAction"] == "Signed by Governor"
    assert detail["lastActionDate"] == "2026-01-30"
    assert detail["chapter"] == "233"
    assert detail["currentVersionPath"].endswith("legislationName=HB270")
    assert detail["introduced"].endswith("legislationName=HB270")
    assert detail["sponsor"] == "Heffernan"
    assert detail["amendments"][0]["amendmentNumber"] == "SA 1"
    assert detail["amendments"][0]["status"] == "Introduced 4/21/26"
    assert detail["amendments"][0]["documentUrl"].endswith("legislationName=SA1")


def test_fetch_bill_detail_falls_back_when_gateway_blocks_the_page() -> None:
    settings = get_settings()
    api = DelawareApiClient(settings)
    api.close()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/BillDetail":
            return httpx.Response(
                403,
                text="<html><body><h1>403 Forbidden</h1><hr><center>Microsoft-Azure-Application-Gateway/v2</center></body></html>",
                request=request,
            )
        if request.method == "POST" and request.url.path.endswith("/GetRecentReportsByLegislationId"):
            return httpx.Response(
                200,
                json={
                    "Data": [
                        {
                            "ActionDescription": "Introduced in House",
                            "OccuredAtDateTime": "4/22/26",
                        }
                    ]
                },
                request=request,
            )
        if request.method == "POST" and request.url.path.endswith("/GetVotingReportsByLegislationId"):
            return httpx.Response(200, json={"Data": []}, request=request)
        if request.method == "POST" and request.url.path.endswith("/GetRelatedAmendmentsByLegislationId"):
            return httpx.Response(200, json={"Data": []}, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.delaware_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail(
            "https://legis.delaware.gov/BillDetail?legislationId=143159",
            {
                "billNum": "HA1TOHB306",
                "displayCode": "HA 1 to HB 306",
                "legislationId": 143159,
                "catchTitle": "This Amendment adds a safe harbor.",
                "billTitle": "This Amendment adds a safe harbor.",
                "synopsis": "This Amendment adds a safe harbor.",
            },
        )
    finally:
        api.close()

    assert detail["bill"] == "HA1TOHB306"
    assert detail["lastAction"] == "Introduced in House"
    assert detail["lastActionDate"] == "2026-04-22"
    assert detail["currentVersionPath"] is None


def test_fetch_public_document_text_treats_forbidden_document_as_unavailable() -> None:
    settings = get_settings()
    api = DelawareApiClient(settings)
    api.close()

    api.client = httpx.Client(
        base_url=settings.delaware_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(lambda request: httpx.Response(403, text="forbidden", request=request)),
    )

    try:
        text = api.fetch_public_document_text("/json/BillDetail/GenerateHtmlDocument?legislationId=1")
    finally:
        api.close()

    assert text == ""
