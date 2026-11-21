import json
import pytest

from app.db import get_legislative_media, upsert_legislative_media, update_legislative_media_transcript
from app.wyoming_vote_explanations import TranscriptResult
from scripts import recover_held_wyoming_media as recovery


@pytest.fixture
def held(monkeypatch):
    media_id = upsert_legislative_media({'state':'wy', 'year':2018, 'session_date':'2018-02-27',
        'chamber':'H', 'time_of_day':'PM', 'source_url':'https://wyoleg.gov/2018/Audio/house/h022718pm1.mp3',
        'source_kind':'official_media'})
    update_legislative_media_transcript(media_id, status='quality_failed', error='Previous quality hold')
    class Official:
        def __init__(self, settings): pass
        def fetch_chamber_audio(self, *args):
            return [{'sessionDate':'2018-02-27T00:00:00', 'specialSessionValue':None,
                     'chamberAudioFiles':[{'chamber':'H','timeofDay':'PM',
                         'filePath':'http://wyoleg.gov/2018/Audio/house/h022718pm1.mp3'}]}]
        def close(self): pass
    monkeypatch.setattr(recovery, 'WyomingApiClient', Official)
    return media_id


def usable():
    return TranscriptResult(status='available', source='speech_to_text', duration_seconds=120,
                            segments=[{'start':1,'end':20,'text':'The committee will consider this bill.'}])


def test_recovery_is_backed_up_guarded_and_idempotent(monkeypatch, tmp_path, held):
    original = get_legislative_media(held)
    monkeypatch.setattr(recovery, 'fetch_media_transcript', lambda *a,**kw: usable())
    assert recovery.generate_candidate(held, tmp_path) == 'available'
    assert get_legislative_media(held) == original
    assert json.loads((tmp_path / f'{held}-before.json').read_text())['media']['transcript_status']=='quality_failed'
    assert recovery.publish_candidate(held, tmp_path) == 'saved'
    assert get_legislative_media(held)['transcript_status']=='available'
    assert get_legislative_media(held)['explanation_scan_status']=='pending'
    assert recovery.publish_candidate(held, tmp_path) == 'preserved'
    assert recovery.generate_candidate(held, tmp_path) == 'not_eligible'


def test_quality_failure_preserves_the_original_and_cannot_be_repeated(monkeypatch, tmp_path, held):
    original = get_legislative_media(held)
    calls=[]
    def fail(*args, **kwargs):
        calls.append(True)
        return TranscriptResult(status='quality_failed', error='No usable speech')
    monkeypatch.setattr(recovery, 'fetch_media_transcript', fail)
    assert recovery.generate_candidate(held, tmp_path)=='quality_failed'
    assert recovery.publish_candidate(held, tmp_path)=='held'
    assert get_legislative_media(held)==original
    with pytest.raises(FileExistsError):
        recovery.generate_candidate(held, tmp_path)
    assert len(calls)==1


@pytest.mark.parametrize('field,value', [('chamber','S'),('session_date','2018-02-28'),('special_session_key',1),('time_of_day','AM')])
def test_official_identity_must_match_every_session_field(held, field, value):
    original=get_legislative_media(held)
    official=[{'sessionDate':'2018-02-27','specialSessionValue':None,'chamberAudioFiles':[
        {'chamber':'H','timeofDay':'PM','filePath':original['source_url']}]}]
    assert recovery._official_identity(original, official)
    original[field]=value
    assert not recovery._official_identity(original, official)


def test_changed_source_or_existing_evidence_is_not_overwritten(monkeypatch, tmp_path, held):
    monkeypatch.setattr(recovery, 'fetch_media_transcript', lambda *a,**kw: usable())
    assert recovery.generate_candidate(held, tmp_path)=='available'
    update_legislative_media_transcript(held, status='available', segments=[{'start':0,'end':1,'text':'Existing evidence.'}])
    original=get_legislative_media(held)
    assert recovery.publish_candidate(held,tmp_path)=='changed'
    assert get_legislative_media(held)==original


def test_invalid_candidate_timestamps_are_rejected(monkeypatch, tmp_path, held):
    result=usable();result.segments[0]['end']=1210
    monkeypatch.setattr(recovery, 'fetch_media_transcript', lambda *a,**kw: result)
    assert recovery.generate_candidate(held,tmp_path)=='available'
    with pytest.raises(ValueError, match='invalid segments'):
        recovery.publish_candidate(held,tmp_path)
    assert get_legislative_media(held)['transcript_status']=='quality_failed'


def test_backup_failure_stops_before_transcription(monkeypatch, tmp_path, held):
    def reject(handle):raise ValueError('Durable backup required')
    monkeypatch.setattr(recovery,'_require_durable_backup',reject)
    monkeypatch.setattr(recovery,'fetch_media_transcript',lambda *a,**kw:pytest.fail('Unbacked recovery started'))
    with pytest.raises(ValueError,match='Durable backup'):
        recovery.generate_candidate(held,tmp_path)
    assert get_legislative_media(held)['transcript_status']=='quality_failed'
