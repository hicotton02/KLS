"""Explicit, one-time recovery of held Wyoming recordings without replacing evidence."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.db import PostgresConnection, connect, normalize_special_session
from app.settings import get_settings
from app.text_utils import iso_now
from app.wyoming_api import WyomingApiClient
from app.wyoming_media_sources import normalize_wyoming_media_url
from app.wyoming_vote_explanations import fetch_media_transcript
from scripts.review_held_summaries import _require_durable_backup


IDENTITY_FIELDS = (
    'id', 'state', 'year', 'special_session_key', 'session_date', 'chamber', 'time_of_day',
    'source_url', 'duplicate_of_id', 'transcript_status', 'transcript_json',
    'transcript_source', 'transcript_updated_at', 'explanation_scan_status',
)


def _write_json(path: Path, value: dict) -> None:
    with path.open('x', encoding='utf-8') as handle:
        _require_durable_backup(handle)
        json.dump(value, handle, sort_keys=True, default=str)
        handle.flush()
        os.fsync(handle.fileno())


def _official_identity(media: dict, sessions: list[dict]) -> bool:
    for session in sessions:
        if (str(session.get('sessionDate') or '')[:10] != media['session_date']
                or normalize_special_session(session.get('specialSessionValue')) != media['special_session_key']):
            continue
        for item in session.get('chamberAudioFiles') or []:
            if (str(item.get('chamber') or '').strip().upper() == media['chamber']
                    and str(item.get('timeofDay') or '').strip().upper() == (media['time_of_day'] or '')
                    and normalize_wyoming_media_url(item.get('filePath')) == normalize_wyoming_media_url(media['source_url'])):
                return True
    return False


def generate_candidate(media_id: int, archive: Path, *, settings=None, logger=print) -> str:
    with connect() as connection:
        row = connection.execute('SELECT * FROM legislative_media WHERE id=?', (media_id,)).fetchone()
    media = dict(row) if row else None
    if (media is None or media['state'] != 'wy' or media['transcript_status'] != 'quality_failed'
            or media['transcript_json'] or media['duplicate_of_id']
            or media['explanation_scan_status'] in ('scanning', 'complete')):
        return 'not_eligible'
    config = settings or get_settings()
    api = WyomingApiClient(config)
    try:
        sessions = api.fetch_chamber_audio(media['year'], media['special_session_value'])
        if not _official_identity(media, sessions):
            return 'source_identity_hold'
    finally:
        api.close()
    archive.mkdir(parents=True, exist_ok=True)
    # An exclusive, durable backup also prevents a second generation in this run.
    _write_json(archive / f'{media_id}-before.json', {'media': media, 'saved_at': iso_now()})
    result = fetch_media_transcript(media, config, logger=logger)
    candidate = {'media_id': media_id, 'status': result.status, 'source': result.source,
                 'segments': result.segments, 'duration_seconds': result.duration_seconds,
                 'error': result.error, 'finished_at': iso_now()}
    _write_json(archive / f'{media_id}-candidate.json', candidate)
    return result.status


def publish_candidate(media_id: int, archive: Path) -> str:
    before_path = archive / f'{media_id}-before.json'
    with before_path.open(encoding='utf-8') as handle:
        _require_durable_backup(handle)
        original = json.load(handle)['media']
    with (archive / f'{media_id}-candidate.json').open(encoding='utf-8') as handle:
        _require_durable_backup(handle)
        candidate = json.load(handle)
    if candidate['media_id'] != media_id or original['id'] != media_id:
        raise ValueError('Recording identity did not match the recovery files')
    if candidate['status'] != 'available':
        return 'held'
    segments = candidate.get('segments')
    duration = candidate.get('duration_seconds')
    if (not isinstance(segments, list) or not segments or type(duration) is not int or duration <= 0
            or candidate.get('source') not in ('speech_to_text', 'youtube_captions')):
        raise ValueError('Recovered transcript has no usable timestamped speech')
    previous = -1
    for segment in segments:
        start, end = segment.get('start'), segment.get('end')
        if (type(start) is not int or type(end) is not int or start < previous or start < 0
                or end <= start or end > duration + 2 or not str(segment.get('text') or '').strip()):
            raise ValueError('Recovered transcript has invalid segments')
        previous = start
    encoded = json.dumps(segments)
    with connect() as connection:
        if not isinstance(connection, PostgresConnection):
            connection.execute('BEGIN IMMEDIATE')
        lock = ' FOR UPDATE' if isinstance(connection, PostgresConnection) else ''
        current = connection.execute('SELECT * FROM legislative_media WHERE id=?' + lock, (media_id,)).fetchone()
        if current is None:
            return 'changed'
        if (current['transcript_status'] == 'available' and current['transcript_json'] == encoded
                and all(current[key] == original[key] for key in IDENTITY_FIELDS[:9])):
            return 'preserved'
        if any(current[key] != original[key] for key in IDENTITY_FIELDS):
            return 'changed'
        if original['state'] != 'wy' or original['transcript_status'] != 'quality_failed' or original['transcript_json']:
            return 'not_eligible'
        stamp = iso_now()
        connection.execute("""UPDATE legislative_media SET transcript_status='available',
            transcript_source=?,transcript_json=?,duration_seconds=?,transcript_error=NULL,
            transcript_updated_at=?,explanation_scan_status='pending',explanation_scan_error=NULL,
            updated_at=? WHERE id=?""", (candidate['source'], encoded, duration, stamp, stamp, media_id))
        connection.commit()
    return 'saved'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--media-id', type=int, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--stage', choices=('generate', 'publish'), required=True)
    args = parser.parse_args()
    os.umask(0o077)
    result = (generate_candidate if args.stage == 'generate' else publish_candidate)(args.media_id, args.archive)
    print(json.dumps({'media_id': args.media_id, 'stage': args.stage, 'result': result}), flush=True)


if __name__ == '__main__':
    main()
