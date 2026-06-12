from __future__ import annotations

import httpx

from app.settings import get_settings
from app.washington_api import WashingtonApiClient


def test_fetch_year_bills_dedupes_versions_into_canonical_bill_numbers() -> None:
    settings = get_settings()
    api = WashingtonApiClient(settings)
    api.close()

    feature_xml = """<?xml version="1.0" encoding="utf-8"?>
    <DataTable xmlns="http://WSLWebServices.leg.wa.gov/">
      <diffgr:diffgram xmlns:diffgr="urn:schemas-microsoft-com:xml-diffgram-v1">
        <NewDataSet xmlns="">
          <Table>
            <prefix>HB </prefix>
            <legnum>1014</legnum>
            <sharepointtitle>HB 1014</sharepointtitle>
            <sponsor>Schmidt</sponsor>
            <status>Introduced</status>
            <title>Child support schedule</title>
            <bienYear>2025</bienYear>
          </Table>
          <Table>
            <prefix>EHB </prefix>
            <legnum>1014</legnum>
            <sharepointtitle>EHB 1014</sharepointtitle>
            <sponsor>Schmidt</sponsor>
            <status>C 272 L 25</status>
            <title>Child support schedule</title>
            <bienYear>2025</bienYear>
            <passedLegislature>Yes</passedLegislature>
          </Table>
          <Table>
            <prefix>HCR </prefix>
            <legnum>4400</legnum>
            <sharepointtitle>HCR 4400</sharepointtitle>
            <sponsor>Ormsby</sponsor>
            <status>Adopted</status>
            <title>Legislature joint sessions</title>
            <bienYear>2025</bienYear>
          </Table>
        </NewDataSet>
      </diffgr:diffgram>
    </DataTable>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/LegislationService.asmx/GetLegislativeBillListFeatureData"):
            return httpx.Response(200, text=feature_xml, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.service_client = httpx.Client(
        base_url="https://wslwebservices.leg.wa.gov",
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        bills = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in bills] == ["HB1014", "HCR4400"]
    assert bills[0]["billStatus"] == "C 272 L 25"
    assert bills[0]["baseBillId"] == "HB 1014"
    assert bills[1]["detailPath"].endswith("BillNumber=4400&Year=2026&Initiative=false")


def test_fetch_bill_detail_combines_service_and_public_page() -> None:
    settings = get_settings()
    api = WashingtonApiClient(settings)
    api.close()

    legislation_xml = """<?xml version="1.0" encoding="utf-8"?>
    <ArrayOfLegislation xmlns="http://WSLWebServices.leg.wa.gov/">
      <Legislation>
        <Biennium>2025-26</Biennium>
        <BillId>EHB 1014</BillId>
        <BillNumber>1014</BillNumber>
        <SubstituteVersion>0</SubstituteVersion>
        <EngrossedVersion>1</EngrossedVersion>
        <OriginalAgency>House</OriginalAgency>
        <ShortDescription>Child support schedule</ShortDescription>
        <LongDescription>Concerning updates to the child support schedule.</LongDescription>
        <LegalTitle>AN ACT Relating to child support.</LegalTitle>
        <StateFiscalNote>true</StateFiscalNote>
        <LocalFiscalNote>false</LocalFiscalNote>
        <CurrentStatus>
          <HistoryLine>Governor signed.</HistoryLine>
          <ActionDate>2025-05-12T00:00:00</ActionDate>
          <Status>C 272 L 25</Status>
          <Veto>false</Veto>
        </CurrentStatus>
      </Legislation>
    </ArrayOfLegislation>
    """
    sponsors_xml = """<?xml version="1.0" encoding="utf-8"?>
    <ArrayOfSponsor xmlns="http://WSLWebServices.leg.wa.gov/">
      <Sponsor><LongName>Representative Schmidt</LongName></Sponsor>
      <Sponsor><LongName>Representative Davis</LongName></Sponsor>
    </ArrayOfSponsor>
    """
    amendments_xml = """<?xml version="1.0" encoding="utf-8"?>
    <ArrayOfAmendment xmlns="http://WSLWebServices.leg.wa.gov/">
      <Amendment>
        <Name>1014 AMH RULE TEST 001</Name>
        <Agency>House</Agency>
        <FloorNumber>77</FloorNumber>
        <FloorAction>ADOPTED</FloorAction>
        <SponsorName>Rules</SponsorName>
        <PdfUrl>http://lawfilesext.leg.wa.gov/biennium/2025-26/Pdf/Amendments/House/1014%20AMH%20RULE%20TEST%20001.pdf</PdfUrl>
      </Amendment>
    </ArrayOfAmendment>
    """
    summary_html = """
    <html><body>
      <article>
        <h1>HB 1014 - 2025-26</h1>
        <h2>Current version:</h2>
        <a href="https://lawfilesext.leg.wa.gov/biennium/2025-26/Pdf/Bills/House%20Bills/1014.pdf">(View original bill)</a>
        <h2>Current status:</h2>
        <p>Governor signed.</p>
      </article>
    </body></html>
    """

    def service_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/LegislationService.asmx/GetLegislation"):
            return httpx.Response(200, text=legislation_xml, request=request)
        if request.url.path.endswith("/LegislationService.asmx/GetSponsors"):
            return httpx.Response(200, text=sponsors_xml, request=request)
        if request.url.path.endswith("/LegislationService.asmx/GetAmendmentsForBiennium"):
            return httpx.Response(200, text=amendments_xml, request=request)
        raise AssertionError(f"Unexpected service request: {request.method} {request.url}")

    api.service_client = httpx.Client(
        base_url="https://wslwebservices.leg.wa.gov",
        follow_redirects=True,
        transport=httpx.MockTransport(service_handler),
    )
    api.client = httpx.Client(
        base_url=settings.washington_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=summary_html, request=request)),
    )

    try:
        detail = api.fetch_bill_detail(
            "https://app.leg.wa.gov/billsummary?BillNumber=1014&Year=2026&Initiative=false",
            {
                "billNum": "HB1014",
                "baseBillId": "HB 1014",
                "billNumber": 1014,
                "sourceYear": 2026,
                "biennium": "2025-26",
                "sponsor": "Schmidt",
                "catchTitle": "Child support schedule",
            },
        )
    finally:
        api.close()

    assert detail["bill"] == "HB1014"
    assert detail["sponsor"] == "Representative Schmidt, Representative Davis"
    assert detail["billStatus"] == "C 272 L 25"
    assert detail["lastAction"] == "Governor signed."
    assert detail["lastActionDate"] == "2025-05-12"
    assert detail["currentVersionPath"].endswith("/House%20Bills/1014.pdf")
    assert detail["digest"].endswith("/Search/bill/1014/69")
    assert [item["amendmentNumber"] for item in detail["amendments"]] == ["1014 AMH RULE TEST 001"]
    assert detail["amendments"][0]["documentUrl"].startswith("https://lawfilesext.leg.wa.gov/")
