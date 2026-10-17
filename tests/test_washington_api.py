from __future__ import annotations

import httpx
import pytest

from app.settings import get_settings
from app.washington_api import WashingtonApiClient


def test_fetch_year_bills_dedupes_versions_into_canonical_bill_numbers() -> None:
    settings = get_settings()
    api = WashingtonApiClient(settings)
    api.close()

    # The old feature endpoint is deliberately never called: it has a server-side SQL timeout.
    year_xml = '''<ArrayOfLegislationInfo xmlns="http://WSLWebServices.leg.wa.gov/">
      <LegislationInfo><Biennium>2025-26</Biennium><BillId>HB 1014</BillId><BillNumber>1014</BillNumber></LegislationInfo>
      <LegislationInfo><Biennium>2025-26</Biennium><BillId>EHB 1014</BillId><BillNumber>1014</BillNumber></LegislationInfo>
      <LegislationInfo><Biennium>2025-26</Biennium><BillId>HCR 4400</BillId><BillNumber>4400</BillNumber></LegislationInfo>
    </ArrayOfLegislationInfo>'''
    requests = []
    def year_handler(request):
        if request.url.path.endswith('/GetLegislationIntroducedSince'):
            return httpx.Response(200,text=year_xml.replace('LegislationInfo','Legislation'),request=request)
        if request.url.path.endswith('/GetLegislationNotYetIntroducedInHouseOfOrigin'):
            return httpx.Response(200,text='<ArrayOfLegislationInfo xmlns="http://WSLWebServices.leg.wa.gov/"/>',request=request)
        assert request.url.path.endswith('/GetLegislationByYear')
        requests.append(request.url.params['year'])
        return httpx.Response(200, text=year_xml, request=request)
    api.service_client = httpx.Client(
        base_url="https://wslwebservices.leg.wa.gov",
        follow_redirects=True,
        transport=httpx.MockTransport(year_handler),
    )

    try:
        bills = api.fetch_year_bills(2026)
        assert api.fetch_year_bills(2026) == bills
    finally:
        api.close()

    assert [item["billNum"] for item in bills] == ["HB1014", "HCR4400"]
    assert requests == ['2025','2026']
    assert bills[0]["baseBillId"] == "HB 1014"
    assert bills[1]["detailPath"].endswith("BillNumber=4400&Year=2026&Initiative=false")


@pytest.mark.parametrize('content', [
    '<html>Server maintenance</html>',
    '<ArrayOfLegislationInfo xmlns="http://WSLWebServices.leg.wa.gov/"/>',
    '<ArrayOfLegislationInfo xmlns="http://WSLWebServices.leg.wa.gov/"><LegislationInfo><Biennium>2023-24</Biennium><BillId>HB 1</BillId><BillNumber>1</BillNumber></LegislationInfo></ArrayOfLegislationInfo>',
    '<ArrayOfLegislationInfo xmlns="http://WSLWebServices.leg.wa.gov/"><LegislationInfo><Biennium>2025-26</Biennium><BillId>HB 1</BillId><BillNumber>2</BillNumber></LegislationInfo></ArrayOfLegislationInfo>',
])
def test_year_inventory_fails_closed(content):
    api = WashingtonApiClient(get_settings())
    api.service_client.close()
    api.service_client = httpx.Client(base_url='https://wslwebservices.leg.wa.gov',
        transport=httpx.MockTransport(lambda r:httpx.Response(200,text=content,request=r)))
    try:
        with pytest.raises(ValueError):
            api.fetch_year_bills(2026)
        assert not api._feature_rows
    finally:
        api.close()


def test_year_inventory_keeps_first_year_bills_and_numbered_initiatives():
    api = WashingtonApiClient(get_settings())
    api.service_client.close()
    def handler(r):
        if r.url.path.endswith('/GetLegislationIntroducedSince'):
            return httpx.Response(200,text='''<ArrayOfLegislation xmlns="http://WSLWebServices.leg.wa.gov/">
                <Legislation><Biennium>2025-26</Biennium><BillId>HB 1</BillId><BillNumber>1</BillNumber><ShortDescription>School funding</ShortDescription><Sponsor>(Example)</Sponsor><CurrentStatus><HistoryLine>Governor signed.</HistoryLine><ActionDate>2026-04-01</ActionDate><Status>C 42 L 26</Status></CurrentStatus></Legislation>
                <Legislation><Biennium>2025-26</Biennium><BillId>HI IL26-001</BillId><BillNumber>26001</BillNumber></Legislation>
                </ArrayOfLegislation>''',request=r)
        if r.url.path.endswith('/GetLegislationNotYetIntroducedInHouseOfOrigin'):
            return httpx.Response(200,text='<ArrayOfLegislationInfo xmlns="http://WSLWebServices.leg.wa.gov/"/>',request=r)
        year = r.url.params['year']
        bill, number = ('HB 1','1') if year == '2025' else ('HI IL26-001','26001')
        return httpx.Response(200,text=f'''<ArrayOfLegislationInfo xmlns="http://WSLWebServices.leg.wa.gov/">
            <LegislationInfo><Biennium>2025-26</Biennium><BillId>{bill}</BillId><BillNumber>{number}</BillNumber></LegislationInfo>
            </ArrayOfLegislationInfo>''',request=r)
    api.service_client = httpx.Client(base_url='https://wslwebservices.leg.wa.gov',transport=httpx.MockTransport(handler))
    try:
        rows = api.fetch_year_bills(2026)
        assert [r['billNum'] for r in rows] == ['HB1','HI26001']
        assert rows[1]['baseBillId'] == 'HI 26001'
        assert rows[1]['detailPath'].endswith('Initiative=true')
        assert rows[0]['catchTitle']=='School funding' and rows[0]['sponsor']=='Example'
        assert rows[0]['lastAction']=='Governor signed.' and rows[0]['lastActionDate']=='2026-04-01'
        assert rows[0]['chapter']=='Chapter 42'
    finally:
        api.close()


def test_detail_selects_the_right_chamber_and_current_version():
    api = WashingtonApiClient(get_settings())
    api.service_client.close()
    content='''<ArrayOfLegislation xmlns="http://WSLWebServices.leg.wa.gov/">
        <Legislation><Biennium>2025-26</Biennium><BillId>HI IL26-001</BillId><BillNumber>26001</BillNumber><Active>true</Active></Legislation>
        <Legislation><Biennium>2025-26</Biennium><BillId>SI IL26-001</BillId><BillNumber>26001</BillNumber><Active>true</Active></Legislation>
        </ArrayOfLegislation>'''
    api.service_client = httpx.Client(base_url='https://wslwebservices.leg.wa.gov',
        transport=httpx.MockTransport(lambda r:httpx.Response(200,text=content,request=r)))
    try:
        from app.washington_api import WASHINGTON_XML_NS
        result=api._fetch_legislation('2025-26',26001,'SI26001')
        assert result.findtext('wa:BillId',namespaces=WASHINGTON_XML_NS)=='SI IL26-001'
        with pytest.raises(ValueError): api._fetch_legislation('2025-26',26001,'HB26001')
    finally:
        api.close()


def test_bulk_metadata_avoids_per_bill_requests_and_keeps_unintroduced_records():
    api=WashingtonApiClient(get_settings())
    api.service_client.close()
    calls=[]
    def xml(name,bill,number,title=''):
        return f'<ArrayOf{name} xmlns="http://WSLWebServices.leg.wa.gov/"><{name}><Biennium>2025-26</Biennium><BillId>{bill}</BillId><BillNumber>{number}</BillNumber><ShortDescription>{title}</ShortDescription></{name}></ArrayOf{name}>'
    def handler(r):
        method=r.url.path.rsplit('/',1)[-1]
        calls.append(method)
        if method=='GetLegislationByYear': text=xml('LegislationInfo','HB 1','1')
        elif method=='GetLegislationNotYetIntroducedInHouseOfOrigin': text=xml('LegislationInfo','HR 1','1')
        elif method=='GetLegislationIntroducedSince': text=xml('Legislation','HB 1','1','School funding')
        elif method=='GetLegislation': text=xml('Legislation','HR 1','1','House rules')
        else: raise AssertionError(method)
        return httpx.Response(200,text=text,request=r)
    api.service_client=httpx.Client(base_url='https://wslwebservices.leg.wa.gov',transport=httpx.MockTransport(handler))
    try:
        rows=api.fetch_year_bills(2026)
        assert [r['catchTitle'] for r in rows]==['School funding','House rules']
        api.fetch_year_bills(2026)
        assert calls.count('GetLegislationIntroducedSince')==1
        assert calls.count('GetLegislation')==1
    finally:
        api.close()


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
