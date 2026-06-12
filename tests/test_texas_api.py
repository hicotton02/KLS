from __future__ import annotations

from app.settings import get_settings
from app.texas_api import TexasApiClient


def test_fetch_year_bills_reads_texas_billhistory_tree() -> None:
    settings = get_settings()
    api = TexasApiClient(settings)
    api.close()

    def fake_listdir(path: str) -> list[str]:
        mapping = {
            "/bills/89R/billhistory/house_bills": ["HB00001_HB00099"],
            "/bills/89R/billhistory/house_bills/HB00001_HB00099": ["HB 1.xml"],
        }
        return mapping.get(path, [])

    api._ftp_listdir = fake_listdir  # type: ignore[method-assign]
    api._ftp_read_text = lambda path: (_ for _ in ()).throw(AssertionError("fetch_year_bills should not read XML during list build"))  # type: ignore[method-assign]

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert len(items) == 1
    assert items[0]["billNum"] == "HB1"
    assert items[0]["billTitle"] == "HB1"
    assert items[0]["detailPath"] == "/bills/89R/billhistory/house_bills/HB00001_HB00099/HB 1.xml"


def test_fetch_bill_detail_extracts_versions_and_actions() -> None:
    settings = get_settings()
    api = TexasApiClient(settings)
    api.close()

    detail_xml = """
    <bill bill="89(R) SB 2" lastUpdate="2026-02-14">
      <caption>Tax update</caption>
      <authors>Sen. Beta</authors>
      <coauthors>Sen. Gamma</coauthors>
      <lastaction>Senate passed</lastaction>
      <subjects><subject>Taxes</subject></subjects>
      <committees><senate name="Finance" status="Reported" /></committees>
      <actions>
        <action><date>01/10/2026</date><description>Filed</description></action>
        <action><date>02/14/2026</date><description>Signed by the governor</description></action>
      </actions>
      <billtext>
        <docTypes>
          <bill>
            <versions>
              <version><WebHTMLURL>https://capitol.texas.gov/tlodocs/89R/billtext/html/SB00002I.htm</WebHTMLURL></version>
              <version><WebPDFURL>https://capitol.texas.gov/tlodocs/89R/billtext/pdf/SB00002F.pdf</WebPDFURL></version>
            </versions>
          </bill>
          <analysis>
            <versions>
              <version><WebHTMLURL>https://capitol.texas.gov/tlodocs/89R/analysis/html/SB00002E.htm</WebHTMLURL></version>
            </versions>
          </analysis>
        </docTypes>
      </billtext>
    </bill>
    """

    api._ftp_read_text = lambda path: detail_xml  # type: ignore[method-assign]

    try:
        detail = api.fetch_bill_detail("/bills/89R/billhistory/senate_bills/SB00001_SB00099/SB 2.xml")
    finally:
        api.close()

    assert detail["bill"] == "SB2"
    assert detail["sponsor"] == "Sen. Beta"
    assert detail["signedDate"] == "2026-02-14"
    assert detail["introduced"] == "https://capitol.texas.gov/tlodocs/89R/billtext/html/SB00002I.htm"
    assert detail["currentVersionPath"] == "https://capitol.texas.gov/tlodocs/89R/billtext/pdf/SB00002F.pdf"
    assert detail["digest"] == "https://capitol.texas.gov/tlodocs/89R/analysis/html/SB00002E.htm"
    assert "Finance (Reported)" in detail["digestHTML"]
    assert len(detail["billActions"]) == 2
