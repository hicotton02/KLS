from fastapi.testclient import TestClient
from app.db import (connect, get_legislator_voting_record, list_bill_vote_explanations,
    list_legislator_vote_explanations, list_legislator_vote_summaries,
    replace_bill_roll_calls, replace_media_vote_explanations)
from app.main import app
from tests.test_main import _seed_state_bill


def store(year, member, *, chamber='S', position='yes'):
    _seed_state_bill('SF0001', 'Identity test', year=year)
    stamp = f'{year}-02-01T12:00:00+00:00'
    replace_bill_roll_calls('wy', year, 'SF0001', payloads=[{
        'roll_call_key':'s-1','vote_id':'1','chamber':chamber,'vote_date':stamp,
        'vote_type':'F','action':'Third reading','amendment_number':None,
        'yes_count':int(position=='yes'),'no_count':int(position=='no'),
        'absent_count':0,'conflict_count':0,'excused_count':0,
        'members':[{**member,'vote_position':position}],
        'source_synced_at':stamp,'created_at':stamp,'updated_at':stamp}])


KNOWN = {'member_key':'wy-2138','source_legislator_id':'2138',
    'legislator_name':'Laura Pearson','vote_label':'Pearson','party':'R','district':'S14'}
ALIAS = {'member_key':'wy-s-pearson','source_legislator_id':None,
    'legislator_name':'PEARSON','vote_label':'PEARSON','party':None,'district':None}


def test_new_surname_only_session_keeps_full_identity_and_original_votes():
    store(2025, KNOWN)
    store(2026, ALIAS, position='no')
    with connect() as c:
        before=[dict(r) for r in c.execute('SELECT * FROM bill_roll_call_votes ORDER BY id').fetchall()]
    rows=list_legislator_vote_summaries('wy',query='Laura',year=2026)
    assert len(rows)==1
    assert rows[0]['legislator_name']=='Laura Pearson'
    assert rows[0]['district']=='S14' and rows[0]['source_legislator_id']=='2138'
    profile=get_legislator_voting_record('wy','wy-s-pearson',latest_year_only=True)
    assert profile['legislator']['name']=='Laura Pearson'
    assert profile['counts']['total']==1 and profile['counts']['no']==1
    with connect() as c:
        assert [dict(r) for r in c.execute('SELECT * FROM bill_roll_call_votes ORDER BY id').fetchall()]==before
    response=TestClient(app).get('/api/v1/areas/wyoming/legislators',params={'q':'Laura','year':2026})
    assert len(response.json()['legislators'])==1


def test_reason_metadata_resolves_from_canonical_cached_identity():
    store(2025, KNOWN)
    store(2026, ALIAS)
    list_legislator_vote_summaries('wy')
    replace_media_vote_explanations(1,[{
        'state':'wy','year':2026,'special_session_value':None,'bill_num':'SF0001',
        'roll_call_key':'s-1','member_key':'wy-2138','lawmaker_name':'Laura Pearson',
        'vote_position':'yes','reason_summary':'She supports the stated change.',
        'evidence_text':'I support this change.','source_url':'https://www.wyoleg.gov/example',
        'source_title':'Official floor session','source_start_seconds':10,'source_end_seconds':20,
        'statement_date':'2026-02-01','source_kind':'public_floor_statement'}])
    bill_reasons=list_bill_vote_explanations('wy',2026,'SF0001')
    member_reasons=list_legislator_vote_explanations('wy','wy-2138',year=2026)
    for reasons in [bill_reasons,member_reasons]:
        assert len(reasons)==1
        assert reasons[0]['party']=='R' and reasons[0]['district']=='S14'
        assert reasons[0]['evidence_text']=='I support this change.'


def test_ambiguous_surname_is_not_assigned_to_a_known_person():
    store(2024, {**KNOWN,'member_key':'wy-999','source_legislator_id':'999','legislator_name':'Other Pearson'})
    store(2025, KNOWN)
    store(2026, ALIAS)
    rows=list_legislator_vote_summaries('wy',year=2026)
    assert len(rows)==1 and rows[0]['member_key']=='wy-s-pearson'
    assert rows[0]['source_legislator_id'] is None


def test_past_house_district_is_not_inherited_by_a_senate_alias():
    store(2025, {**KNOWN,'district':'H14'},chamber='H')
    store(2026, ALIAS)
    row=list_legislator_vote_summaries('wy',year=2026)[0]
    assert row['legislator_name']=='Laura Pearson' and row['chamber']=='S'
    assert row['district'] is None and row['party'] is None


def test_current_identified_record_takes_precedence_over_past_identity():
    store(2025, KNOWN)
    store(2026, {**KNOWN,'legislator_name':'Laura Currentname','district':'S20','party':'D'})
    row=list_legislator_vote_summaries('wy',year=2026)[0]
    assert row['legislator_name']=='Laura Currentname' and row['district']=='S20' and row['party']=='D'


def test_historical_profile_uses_that_sessions_chamber_and_district():
    store(2025, {**KNOWN,'district':'H14'},chamber='H')
    store(2026, {**KNOWN,'district':'S20'},chamber='S',position='no')
    client=TestClient(app)
    older=client.get('/api/v1/areas/wyoming/legislators/wy-2138?year=2025').json()
    latest=client.get('/api/v1/areas/wyoming/legislators/wy-2138?latest=1').json()
    assert older['legislator']['title']=='Representative'
    assert older['legislator']['chamber']=='H' and older['legislator']['district']=='H14'
    assert older['selected_year']==2025 and older['counts']['yes']==1
    assert latest['legislator']['title']=='Senator'
    assert latest['legislator']['chamber']=='S' and latest['legislator']['district']=='S20'
    assert latest['counts']['no']==1
