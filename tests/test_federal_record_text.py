from dataclasses import replace

import httpx
import pytest

from app.federal_api import CongressApiClient
from app.federal_record_text import cited_record_locations, extract_record_bill_text
from app.settings import get_settings


ACTION={'statusDate':'2026-11-18','statusMessage':'Read twice and referred. (text: CR S5761-5771)'}
URL='https://www.govinfo.gov/content/pkg/CREC-2026-11-18/html/CREC-2026-11-18-pt1-PgS5761.htm'


def record():
    return '''<pre>[Congressional Record Volume 172, Number 175 (Wednesday, November 18, 2026)]
[Senate]
[Pages S5761-S5771]
Mr. DURBIN. My personal remarks must not become bill text.
                                S. 5767

Be it enacted by the Senate and House of Representatives of the United States of America in Congress assembled,
SECTION 1. SHORT TITLE.
This Act may be cited as the Student Loan Borrower Bill of Rights.
SEC. 2. LOAN PROTECTIONS.
Lenders shall provide borrowers with clear information about their available repayment options.
The Secretary shall publish rules for carrying out these requirements.
                          ____________________
</pre>'''


def test_only_latest_explicit_same_chamber_citations_are_selected():
    assert cited_record_locations(119,'S',[ACTION])==[('2026-11-18','S5761',URL)]
    assert cited_record_locations(118,'S',[ACTION])==[]
    assert cited_record_locations(119,'HR',[ACTION])==[]
    assert cited_record_locations(119,'S',[ACTION,{'statusDate':'2026-11-19','statusMessage':'Amended'}])==[]
    assert cited_record_locations(119,'SRES',[ACTION])==[]


def test_speech_is_excluded_from_complete_bill_text():
    text=extract_record_bill_text(record(),kind='S',number='5767',date='2026-11-18',page='S5761')
    assert text.startswith('S5767\n\nBe it enacted')
    assert 'personal remarks' not in text and 'Secretary' in text


@pytest.mark.parametrize('old,new', [
    ('S. 5767','S. 5768'), ('November 18','November 19'), ('[Senate]','[House]'),
    ('S5761-S5771','S5762-S5771'), ('____________________',''),
    ('Be it enacted','In my opinion'), ('SEC. 2. LOAN PROTECTIONS.','By Mr. OTHER.\nS. 5768\nSEC. 2. LOAN PROTECTIONS.'),
    ('____________________','Mr. OTHER. These are my personal remarks.\n                          ____________________'),
])
def test_wrong_identity_partial_text_and_mixed_bills_are_rejected(old,new):
    assert extract_record_bill_text(record().replace(old,new),kind='S',number='5767',date='2026-11-18',page='S5761') is None


def test_public_record_request_never_receives_the_congress_api_key():
    api=CongressApiClient(replace(get_settings(),congress_api_key='never-send-this'))
    api.close()
    def handler(request):
        assert str(request.url)==URL and not request.url.query
        assert 'never-send-this' not in str(request.headers)
        return httpx.Response(200,text=record())
    api.client=httpx.Client(transport=httpx.MockTransport(handler),follow_redirects=True)
    try:
        result=api.fetch_cited_record_bill_text(119,'S','5767',[ACTION])
        assert result['bill_num']=='S5767' and result['url']==URL
    finally:api.close()


def test_access_restrictions_are_not_retried_or_bypassed():
    api=CongressApiClient(get_settings());api.close();requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(403)
    api.client=httpx.Client(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(httpx.HTTPStatusError):api.fetch_cited_record_bill_text(119,'S','5767',[ACTION])
        assert len(requests)==1
    finally:api.close()


def test_normal_refresh_keeps_the_same_record_source_without_a_second_download(monkeypatch):
    api=CongressApiClient(get_settings());api.close();calls=[]
    def handler(request):
        calls.append(str(request.url))
        if request.url.host=='www.govinfo.gov':
            return httpx.Response(200,text=record())
        if request.url.path.endswith('/text'):
            return httpx.Response(200,json={'textVersions':[]})
        return httpx.Response(200,json={'bill':{'congress':119,'type':'S','number':'5767',
            'latestAction':{'actionDate':ACTION['statusDate'],'text':ACTION['statusMessage']}}})
    monkeypatch.setattr('app.federal_api.time.sleep',lambda _:None)
    api.client=httpx.Client(base_url='https://api.congress.gov/v3',transport=httpx.MockTransport(handler))
    try:
        api.fetch_bill_detail(119,'S',5767)
        versions=api.fetch_bill_text_versions(119,'S',5767)
        selected=api.pick_text_version(versions)
        text=api.fetch_text_content(selected['url'])
        assert text.startswith('S5767\n\nBe it enacted') and 'personal remarks' not in text
        assert calls.count(URL)==1
    finally:api.close()
