from __future__ import annotations

import hashlib
import json
from concurrent.futures import ALL_COMPLETED, FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from typing import Any, Callable

from app.alaska_api import AlaskaApiClient
from app.alabama_api import AlabamaApiClient
from app.arkansas_api import ArkansasApiClient
from app.arizona_api import ArizonaApiClient
from app.california_api import CaliforniaApiClient
from app.connecticut_api import ConnecticutApiClient
from app.colorado_api import ColoradoApiClient
from app.delaware_api import DelawareApiClient
from app.districtcolumbia_api import DistrictOfColumbiaApiClient
from app.db import (
    count_bills_for_year,
    get_bill,
    get_existing_index,
    init_db,
    list_bill_amendments,
    list_bills,
    list_years,
    normalize_special_session,
    replace_bill_amendments,
    reset_stale_sync_statuses,
    update_sync_status,
    upsert_bill,
)
from app.florida_api import FloridaApiClient
from app.federal_api import CongressApiClient, congress_bill_identifier
from app.georgia_api import GeorgiaApiClient
from app.hawaii_api import HawaiiApiClient
from app.idaho_api import IdahoApiClient
from app.indiana_api import IndianaApiClient
from app.illinois_api import IllinoisApiClient
from app.iowa_api import IowaApiClient
from app.kansas_api import KansasApiClient
from app.kentucky_api import KentuckyApiClient
from app.louisiana_api import LouisianaApiClient
from app.maine_api import MaineApiClient
from app.maryland_api import MarylandApiClient
from app.massachusetts_api import MassachusettsApiClient
from app.michigan_api import MichiganApiClient
from app.minnesota_api import MinnesotaApiClient
from app.mississippi_api import MississippiApiClient
from app.missouri_api import MissouriApiClient
from app.montana_api import MontanaApiClient
from app.nebraska_api import NebraskaApiClient
from app.nevada_api import NevadaApiClient
from app.newhampshire_api import NewHampshireApiClient
from app.newjersey_api import NewJerseyApiClient
from app.newyork_api import NewYorkApiClient
from app.northcarolina_api import NorthCarolinaApiClient
from app.northdakota_api import NorthDakotaApiClient
from app.newmexico_api import NewMexicoApiClient
from app.oklahoma_api import OklahomaApiClient
from app.ohio_api import OhioApiClient
from app.ollama import OllamaClient
from app.oregon_api import OregonApiClient
from app.pennsylvania_api import PennsylvaniaApiClient
from app.rhodeisland_api import RhodeIslandApiClient
from app.relationship_service import analyze_bill_relationships_for_year
from app.settings import Settings, get_settings
from app.southcarolina_api import SouthCarolinaApiClient
from app.southdakota_api import SouthDakotaApiClient
from app.status import classify_bill_status, classify_federal_bill_status
from app.tagging import extract_bill_tags
from app.tennessee_api import TennesseeApiClient
from app.text_utils import clean_text, first_non_empty, html_to_text, iso_now, sentence_list
from app.texas_api import TexasApiClient
from app.utah_api import UtahApiClient
from app.vermont_api import VermontApiClient
from app.virginia_api import VirginiaApiClient
from app.washington_api import WashingtonApiClient
from app.westvirginia_api import WestVirginiaApiClient
from app.wisconsin_api import WisconsinApiClient
from app.wyoming_api import WyomingApiClient


Logger = Callable[[str], None]
FACT_CHECK_VERSION = 1


@dataclass
class SyncStats:
    years: list[int]
    seen: int = 0
    updated: int = 0
    skipped: int = 0
    interpreted: int = 0
    validated: int = 0
    relationship_candidates: int = 0
    relationship_saved: int = 0
    relationship_failed: int = 0
    fallback_interpretations: int = 0
    amendments_seen: int = 0
    amendments_updated: int = 0
    amendments_summarized: int = 0
    tagged: int = 0
    failed: int = 0


@dataclass
class RetagStats:
    state: str
    years: list[int]
    seen: int = 0
    updated: int = 0
    tagged: int = 0


@dataclass(frozen=True)
class CompletedBillSync:
    index_key: tuple[int, int, str]
    bill_num: str
    year: int
    payload: dict[str, Any]
    index_payload: dict[str, Any]
    amendments: list[dict[str, Any]]
    interpreted: int = 0
    validated: int = 0
    fallback_interpretations: int = 0
    amendments_seen: int = 0
    amendments_summarized: int = 0
    tagged: int = 0


@dataclass(frozen=True)
class PreparedBillResult:
    bill_num: str
    year: int
    completed: CompletedBillSync | None = None
    skipped: bool = False


@dataclass
class SyncProgressTracker:
    state: str
    years: list[int]
    stats: SyncStats
    started_at: str
    source_total: int | None = None
    stored_total: int | None = None

    def start(self) -> None:
        update_sync_status(
            self.state,
            years_json=list(self.years),
            is_running=True,
            current_year=self.years[0] if self.years else None,
            current_bill_num="",
            seen=0,
            updated=0,
            skipped=0,
            interpreted=0,
            validated=0,
            failed=0,
            source_total=None,
            stored_total=None,
            started_at=self.started_at,
            finished_at=None,
            last_message="Starting background sync.",
        )

    def note_year(self, year: int) -> None:
        self._write(current_year=year, current_bill_num="", last_message=f"Fetching bills for {year}.")

    def note_scope_totals(self, year: int, *, source_total: int, stored_total: int) -> None:
        self.source_total = max(0, int(source_total))
        self.stored_total = max(0, int(stored_total))
        self._write(
            current_year=year,
            current_bill_num="",
            last_message=self._coverage_message(year),
        )

    def note_checked(self, bill_num: str, year: int, *, changed: bool) -> None:
        message = f"Updated {bill_num} ({year})." if changed else f"Checked {bill_num} ({year}) with no source changes."
        self._write(current_year=year, current_bill_num=bill_num, last_message=message)

    def note_failed(self, bill_num: str, year: int, error: str) -> None:
        self._write(
            current_year=year,
            current_bill_num=bill_num,
            last_message=f"Problem on {bill_num} ({year}): {error[:180]}",
        )

    def finish(self, *, fatal_error: str | None = None) -> None:
        message = fatal_error[:180] if fatal_error else self._finished_message()
        finished_at = iso_now()
        payload = {
            "years_json": list(self.years),
            "is_running": False,
            "current_bill_num": "",
            "current_year": self.years[-1] if self.years else None,
            "seen": self.stats.seen,
            "updated": self.stats.updated,
            "skipped": self.stats.skipped,
            "interpreted": self.stats.interpreted,
            "validated": self.stats.validated,
            "failed": self.stats.failed,
            "source_total": self.source_total,
            "stored_total": self.stored_total,
            "last_message": message,
            "started_at": self.started_at,
            "finished_at": finished_at,
        }
        if fatal_error is None:
            payload["last_success_at"] = finished_at
        update_sync_status(
            self.state,
            **payload,
        )

    def _write(self, *, current_year: int | None, current_bill_num: str, last_message: str) -> None:
        update_sync_status(
            self.state,
            years_json=list(self.years),
            is_running=True,
            current_year=current_year,
            current_bill_num=current_bill_num,
            seen=self.stats.seen,
            updated=self.stats.updated,
            skipped=self.stats.skipped,
            interpreted=self.stats.interpreted,
            validated=self.stats.validated,
            failed=self.stats.failed,
            source_total=self.source_total,
            stored_total=self.stored_total,
            last_message=last_message,
            started_at=self.started_at,
            finished_at=None,
        )

    def _finished_message(self) -> str:
        message = (
            f"Last run checked {self.stats.seen} bills, updated {self.stats.updated}, "
            f"and skipped {self.stats.skipped} unchanged bills."
        )
        if self.source_total is None or self.stored_total is None:
            return message
        return f"{message} {self._coverage_summary()}"

    def _coverage_message(self, year: int) -> str:
        return f"Coverage check for {year}: {self._coverage_summary()}"

    def _coverage_summary(self) -> str:
        if self.source_total is None or self.stored_total is None:
            return "Official bill totals are not available yet."
        if self.stored_total >= self.source_total:
            return f"Stored all {self.source_total} official bills."
        return f"Stored {self.stored_total} of {self.source_total} official bills."


def sync_wyoming(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    skip_relationships: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    init_db()
    years = years or list(settings.wyoming_years)
    log = logger or (lambda message: None)
    stats = SyncStats(years=list(years))
    existing_index = get_existing_index(list(years))
    api = WyomingApiClient(settings)
    executor = _sync_executor(settings, skip_interpretation)
    progress = SyncProgressTracker("wy", list(years), stats, iso_now())
    progress.start()
    fatal_error: str | None = None

    try:
        processed = 0
        stop_after_limit = False
        for year in years:
            pending: dict[Future[CompletedBillSync], tuple[str, int]] = {}
            log(f"Fetching Wyoming bill list for {year}")
            progress.note_year(year)
            bills = api.fetch_year_bills(year)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("wy", year))
            for item in bills:
                if limit is not None and processed >= limit:
                    stop_after_limit = True
                    break

                stats.seen += 1
                processed += 1
                key = (year, normalize_special_session(item.get("specialSessionValue")), item["billNum"])
                existing = existing_index.get(key)

                if not _needs_refresh(existing, item, skip_interpretation, settings.ollama_model):
                    stats.skipped += 1
                    progress.note_checked(item["billNum"], year, changed=False)
                    continue

                try:
                    detail = api.fetch_bill_detail(year, item["billNum"], item.get("specialSessionValue"))
                    official_summary_text = html_to_text(detail.get("summaryHTML"))
                    official_digest_text = html_to_text(detail.get("digestHTML"))
                    current_bill_text = html_to_text(detail.get("currentBillHTML"))
                    status_info = classify_bill_status(
                        bill_status=detail.get("billStatus"),
                        last_action=detail.get("lastAction"),
                        signed_date=detail.get("signedDate"),
                        chapter_no=detail.get("chapter"),
                        enrolled_no=detail.get("enrolledNumber"),
                    )
                    source_hash = _compute_source_hash(detail, official_summary_text, official_digest_text, current_bill_text)
                    timestamp = iso_now()
                    base_payload = _build_wyoming_payload(
                        year=year,
                        detail=detail,
                        status_info=status_info,
                        official_summary_text=official_summary_text,
                        official_digest_text=official_digest_text,
                        current_bill_text=current_bill_text,
                        source_hash=source_hash,
                        timestamp=timestamp,
                    )
                    index_payload = _build_index_payload(
                        bill_status=detail.get("billStatus"),
                        last_action=detail.get("lastAction"),
                        last_action_date=detail.get("lastActionDate"),
                        signed_date=detail.get("signedDate"),
                        effective_date=detail.get("effectiveDate"),
                        chapter_no=detail.get("chapter"),
                        enrolled_no=detail.get("enrolledNumber"),
                        source_hash=source_hash,
                    )
                    existing_bill = get_bill("wy", year, item["billNum"], special_session_value=item.get("specialSessionValue"))
                    existing_amendments = list_bill_amendments(
                        "wy",
                        year,
                        item["billNum"],
                        special_session_value=item.get("specialSessionValue"),
                    )
                    if executor is None:
                        completed = _complete_wyoming_bill(
                            settings=settings,
                            index_key=key,
                            year=year,
                            base_payload=base_payload,
                            index_payload=index_payload,
                            detail=detail,
                            status_info=status_info,
                            official_summary_text=official_summary_text,
                            official_digest_text=official_digest_text,
                            current_bill_text=current_bill_text,
                            skip_interpretation=skip_interpretation,
                            existing_bill=existing_bill,
                            existing_amendments=existing_amendments,
                        )
                        _apply_completed_bill(completed, stats, existing_index, log, progress=progress)
                    else:
                        future = executor.submit(
                            _complete_wyoming_bill,
                            settings,
                            key,
                            year,
                            base_payload,
                            index_payload,
                            detail,
                            status_info,
                            official_summary_text,
                            official_digest_text,
                            current_bill_text,
                            skip_interpretation,
                            existing_bill,
                            existing_amendments,
                        )
                        pending[future] = (item["billNum"], year)
                        if len(pending) >= settings.sync_parallelism * 2:
                            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=False)
                except Exception as exc:  # noqa: BLE001
                    stats.failed += 1
                    log(f"Failed to sync {item['billNum']} ({year}): {exc}")
                    progress.note_failed(item["billNum"], year, str(exc))

            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=True)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("wy", year))

            if stop_after_limit:
                break

            if not skip_relationships and not skip_interpretation and limit is None:
                try:
                    relationship_stats = analyze_bill_relationships_for_year(
                        "wy",
                        year,
                        settings=settings,
                        logger=log,
                    )
                    stats.relationship_candidates += relationship_stats.candidates
                    stats.relationship_saved += relationship_stats.saved
                    stats.relationship_failed += relationship_stats.failed
                    log(
                        f"Saved {relationship_stats.saved} bill-pair review items from {relationship_stats.candidates} candidates for {year}"
                    )
                except Exception as exc:  # noqa: BLE001
                    stats.relationship_failed += 1
                    log(f"Relationship analysis skipped for {year}: {exc}")
    except Exception as exc:  # noqa: BLE001
        fatal_error = str(exc)
        raise
    finally:
        api.close()
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
        progress.finish(fatal_error=fatal_error)

    return stats


def sync_alaska(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    init_db()
    years = years or list(settings.alaska_years)
    log = logger or (lambda message: None)
    stats = SyncStats(years=list(years))
    existing_index = get_existing_index(list(years), state="ak")
    api = AlaskaApiClient(settings)
    executor = _sync_executor(settings, skip_interpretation)
    progress = SyncProgressTracker("ak", list(years), stats, iso_now())
    progress.start()
    fatal_error: str | None = None

    try:
        processed = 0
        stop_after_limit = False
        for year in years:
            pending: dict[Future[CompletedBillSync], tuple[str, int]] = {}
            log(f"Fetching Alaska bill list for {year}")
            progress.note_year(year)
            bills = api.fetch_year_bills(year)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("ak", year))
            for item in bills:
                if limit is not None and processed >= limit:
                    stop_after_limit = True
                    break

                stats.seen += 1
                processed += 1
                key = (year, normalize_special_session(None), item["billNum"])
                existing = existing_index.get(key)

                try:
                    detail = api.fetch_bill_detail(str(item.get("detailPath") or ""))
                    official_summary_text = first_non_empty(
                        html_to_text(detail.get("summaryHTML")),
                        detail.get("catchTitle"),
                    )
                    official_digest_text = first_non_empty(
                        html_to_text(detail.get("digestHTML")),
                        detail.get("billTitle"),
                    )
                    source_hash = _compute_source_hash(detail, official_summary_text, official_digest_text, "")
                    status_info = classify_bill_status(
                        bill_status=detail.get("billStatus"),
                        last_action=detail.get("lastAction"),
                        signed_date=detail.get("signedDate"),
                        chapter_no=detail.get("chapter"),
                        enrolled_no=detail.get("enrolledNumber"),
                    )
                    comparisons = _state_detail_comparisons(detail)
                    if not _needs_refresh_from_values(existing, comparisons, skip_interpretation, settings.ollama_model) and (
                        existing is not None and str(existing.get("source_hash") or "") == source_hash
                    ):
                        stats.skipped += 1
                        progress.note_checked(item["billNum"], year, changed=False)
                        continue

                    timestamp = iso_now()
                    base_payload = _build_state_payload(
                        state_code="ak",
                        year=year,
                        detail=detail,
                        status_info=status_info,
                        official_summary_text=official_summary_text,
                        official_digest_text=official_digest_text,
                        current_bill_text="",
                        source_hash=source_hash,
                        timestamp=timestamp,
                    )
                    index_payload = _build_index_payload(source_hash=source_hash, **comparisons)
                    existing_bill = get_bill("ak", year, item["billNum"])
                    existing_amendments = list_bill_amendments("ak", year, item["billNum"])

                    if executor is None:
                        completed = _complete_state_bill(
                            settings=settings,
                            index_key=key,
                            year=year,
                            state_code="ak",
                            base_payload=base_payload,
                            index_payload=index_payload,
                            detail=detail,
                            status_info=status_info,
                            official_summary_text=official_summary_text,
                            official_digest_text=official_digest_text,
                            skip_interpretation=skip_interpretation,
                            existing_bill=existing_bill,
                            existing_amendments=existing_amendments,
                        )
                        _apply_completed_bill(completed, stats, existing_index, log, progress=progress)
                    else:
                        future = executor.submit(
                            _complete_state_bill,
                            settings,
                            key,
                            year,
                            "ak",
                            base_payload,
                            index_payload,
                            detail,
                            status_info,
                            official_summary_text,
                            official_digest_text,
                            skip_interpretation,
                            existing_bill,
                            existing_amendments,
                        )
                        pending[future] = (item["billNum"], year)
                        if len(pending) >= settings.sync_parallelism * 2:
                            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=False)
                except Exception as exc:  # noqa: BLE001
                    stats.failed += 1
                    log(f"Failed to sync {item['billNum']} ({year}): {exc}")
                    progress.note_failed(item["billNum"], year, str(exc))

            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=True)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("ak", year))
            if stop_after_limit:
                break
    except Exception as exc:  # noqa: BLE001
        fatal_error = str(exc)
        raise
    finally:
        api.close()
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
        progress.finish(fatal_error=fatal_error)

    return stats


def sync_kansas(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    init_db()
    years = years or list(settings.kansas_years)
    log = logger or (lambda message: None)
    stats = SyncStats(years=list(years))
    existing_index = get_existing_index(list(years), state="ks")
    api = KansasApiClient(settings)
    executor = _sync_executor(settings, skip_interpretation)
    progress = SyncProgressTracker("ks", list(years), stats, iso_now())
    progress.start()
    fatal_error: str | None = None

    try:
        processed = 0
        stop_after_limit = False
        for year in years:
            pending: dict[Future[CompletedBillSync], tuple[str, int]] = {}
            log(f"Fetching Kansas bill list for {year}")
            progress.note_year(year)
            bills = api.fetch_year_bills(year)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("ks", year))
            for item in bills:
                if limit is not None and processed >= limit:
                    stop_after_limit = True
                    break

                stats.seen += 1
                processed += 1
                key = (year, normalize_special_session(None), item["billNum"])
                existing = existing_index.get(key)

                try:
                    detail = api.fetch_bill_detail(str(item.get("detailPath") or ""))
                    official_summary_text = first_non_empty(
                        html_to_text(detail.get("summaryHTML")),
                        detail.get("catchTitle"),
                    )
                    official_digest_text = first_non_empty(
                        html_to_text(detail.get("digestHTML")),
                        detail.get("billTitle"),
                    )
                    source_hash = _compute_source_hash(detail, official_summary_text, official_digest_text, "")
                    status_info = classify_bill_status(
                        bill_status=detail.get("billStatus"),
                        last_action=detail.get("lastAction"),
                        signed_date=detail.get("signedDate"),
                        chapter_no=detail.get("chapter"),
                        enrolled_no=detail.get("enrolledNumber"),
                    )
                    comparisons = _state_detail_comparisons(detail)
                    if not _needs_refresh_from_values(existing, comparisons, skip_interpretation, settings.ollama_model) and (
                        existing is not None and str(existing.get("source_hash") or "") == source_hash
                    ):
                        stats.skipped += 1
                        progress.note_checked(item["billNum"], year, changed=False)
                        continue

                    timestamp = iso_now()
                    base_payload = _build_state_payload(
                        state_code="ks",
                        year=year,
                        detail=detail,
                        status_info=status_info,
                        official_summary_text=official_summary_text,
                        official_digest_text=official_digest_text,
                        current_bill_text="",
                        source_hash=source_hash,
                        timestamp=timestamp,
                    )
                    index_payload = _build_index_payload(source_hash=source_hash, **comparisons)
                    existing_bill = get_bill("ks", year, item["billNum"])
                    existing_amendments = list_bill_amendments("ks", year, item["billNum"])

                    if executor is None:
                        completed = _complete_state_bill(
                            settings=settings,
                            index_key=key,
                            year=year,
                            state_code="ks",
                            base_payload=base_payload,
                            index_payload=index_payload,
                            detail=detail,
                            status_info=status_info,
                            official_summary_text=official_summary_text,
                            official_digest_text=official_digest_text,
                            skip_interpretation=skip_interpretation,
                            existing_bill=existing_bill,
                            existing_amendments=existing_amendments,
                        )
                        _apply_completed_bill(completed, stats, existing_index, log, progress=progress)
                    else:
                        future = executor.submit(
                            _complete_state_bill,
                            settings,
                            key,
                            year,
                            "ks",
                            base_payload,
                            index_payload,
                            detail,
                            status_info,
                            official_summary_text,
                            official_digest_text,
                            skip_interpretation,
                            existing_bill,
                            existing_amendments,
                        )
                        pending[future] = (item["billNum"], year)
                        if len(pending) >= settings.sync_parallelism * 2:
                            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=False)
                except Exception as exc:  # noqa: BLE001
                    stats.failed += 1
                    log(f"Failed to sync {item['billNum']} ({year}): {exc}")
                    progress.note_failed(item["billNum"], year, str(exc))

            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=True)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("ks", year))
            if stop_after_limit:
                break
    except Exception as exc:  # noqa: BLE001
        fatal_error = str(exc)
        raise
    finally:
        api.close()
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
        progress.finish(fatal_error=fatal_error)

    return stats


def sync_alabama(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    init_db()
    years = years or list(settings.alabama_years)
    log = logger or (lambda message: None)
    stats = SyncStats(years=list(years))
    existing_index = get_existing_index(list(years), state="al")
    api = AlabamaApiClient(settings)
    executor = _sync_executor(settings, skip_interpretation)
    progress = SyncProgressTracker("al", list(years), stats, iso_now())
    progress.start()
    fatal_error: str | None = None

    try:
        processed = 0
        stop_after_limit = False
        for year in years:
            pending: dict[Future[CompletedBillSync], tuple[str, int]] = {}
            log(f"Fetching Alabama bill list for {year}")
            progress.note_year(year)
            bills = api.fetch_year_bills(year)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("al", year))
            for item in bills:
                if limit is not None and processed >= limit:
                    stop_after_limit = True
                    break

                stats.seen += 1
                processed += 1
                key = (year, normalize_special_session(None), item["billNum"])
                existing = existing_index.get(key)

                if not _needs_refresh_from_values(
                    existing,
                    _state_list_comparisons(item),
                    skip_interpretation,
                    settings.ollama_model,
                ):
                    stats.skipped += 1
                    progress.note_checked(item["billNum"], year, changed=False)
                    continue

                try:
                    detail = api.fetch_bill_detail(year, str(item["billNum"]))
                    official_summary_text = first_non_empty(
                        html_to_text(detail.get("summaryHTML")),
                        detail.get("catchTitle"),
                    )
                    official_digest_text = first_non_empty(
                        html_to_text(detail.get("digestHTML")),
                        detail.get("billTitle"),
                    )
                    source_hash = _compute_source_hash(detail, official_summary_text, official_digest_text, "")
                    status_info = classify_bill_status(
                        bill_status=detail.get("billStatus"),
                        last_action=detail.get("lastAction"),
                        signed_date=detail.get("signedDate"),
                        chapter_no=detail.get("chapter"),
                        enrolled_no=detail.get("enrolledNumber"),
                    )
                    comparisons = _state_detail_comparisons(detail)
                    if not _needs_refresh_from_values(existing, comparisons, skip_interpretation, settings.ollama_model) and (
                        existing is not None and str(existing.get("source_hash") or "") == source_hash
                    ):
                        stats.skipped += 1
                        progress.note_checked(item["billNum"], year, changed=False)
                        continue

                    timestamp = iso_now()
                    base_payload = _build_state_payload(
                        state_code="al",
                        year=year,
                        detail=detail,
                        status_info=status_info,
                        official_summary_text=official_summary_text,
                        official_digest_text=official_digest_text,
                        current_bill_text="",
                        source_hash=source_hash,
                        timestamp=timestamp,
                    )
                    index_payload = _build_index_payload(source_hash=source_hash, **comparisons)
                    existing_bill = get_bill("al", year, item["billNum"])
                    existing_amendments = list_bill_amendments("al", year, item["billNum"])

                    if executor is None:
                        completed = _complete_state_bill(
                            settings=settings,
                            index_key=key,
                            year=year,
                            state_code="al",
                            base_payload=base_payload,
                            index_payload=index_payload,
                            detail=detail,
                            status_info=status_info,
                            official_summary_text=official_summary_text,
                            official_digest_text=official_digest_text,
                            skip_interpretation=skip_interpretation,
                            existing_bill=existing_bill,
                            existing_amendments=existing_amendments,
                        )
                        _apply_completed_bill(completed, stats, existing_index, log, progress=progress)
                    else:
                        future = executor.submit(
                            _complete_state_bill,
                            settings,
                            key,
                            year,
                            "al",
                            base_payload,
                            index_payload,
                            detail,
                            status_info,
                            official_summary_text,
                            official_digest_text,
                            skip_interpretation,
                            existing_bill,
                            existing_amendments,
                        )
                        pending[future] = (item["billNum"], year)
                        if len(pending) >= settings.sync_parallelism * 2:
                            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=False)
                except Exception as exc:  # noqa: BLE001
                    stats.failed += 1
                    log(f"Failed to sync {item['billNum']} ({year}): {exc}")
                    progress.note_failed(item["billNum"], year, str(exc))

            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=True)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("al", year))
            if stop_after_limit:
                break
    except Exception as exc:  # noqa: BLE001
        fatal_error = str(exc)
        raise
    finally:
        api.close()
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
        progress.finish(fatal_error=fatal_error)

    return stats


def sync_arizona(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    init_db()
    years = years or list(settings.arizona_years)
    log = logger or (lambda message: None)
    stats = SyncStats(years=list(years))
    existing_index = get_existing_index(list(years), state="az")
    api = ArizonaApiClient(settings)
    executor = _sync_executor(settings, skip_interpretation)
    progress = SyncProgressTracker("az", list(years), stats, iso_now())
    progress.start()
    fatal_error: str | None = None

    try:
        processed = 0
        stop_after_limit = False
        for year in years:
            pending: dict[Future[CompletedBillSync], tuple[str, int]] = {}
            log(f"Fetching Arizona bill list for {year}")
            progress.note_year(year)
            bills = api.fetch_year_bills(year)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("az", year))
            for item in bills:
                if limit is not None and processed >= limit:
                    stop_after_limit = True
                    break

                stats.seen += 1
                processed += 1
                key = (year, normalize_special_session(None), item["billNum"])
                existing = existing_index.get(key)

                try:
                    detail = api.fetch_bill_detail(year, str(item["billNum"]))
                    official_summary_text = first_non_empty(
                        html_to_text(detail.get("summaryHTML")),
                        detail.get("catchTitle"),
                    )
                    official_digest_text = first_non_empty(
                        html_to_text(detail.get("digestHTML")),
                        detail.get("billTitle"),
                    )
                    source_hash = _compute_source_hash(detail, official_summary_text, official_digest_text, "")
                    status_info = classify_bill_status(
                        bill_status=detail.get("billStatus"),
                        last_action=detail.get("lastAction"),
                        signed_date=detail.get("signedDate"),
                        chapter_no=detail.get("chapter"),
                        enrolled_no=detail.get("enrolledNumber"),
                    )
                    comparisons = _state_detail_comparisons(detail)
                    if not _needs_refresh_from_values(existing, comparisons, skip_interpretation, settings.ollama_model) and (
                        existing is not None and str(existing.get("source_hash") or "") == source_hash
                    ):
                        stats.skipped += 1
                        progress.note_checked(item["billNum"], year, changed=False)
                        continue

                    timestamp = iso_now()
                    base_payload = _build_state_payload(
                        state_code="az",
                        year=year,
                        detail=detail,
                        status_info=status_info,
                        official_summary_text=official_summary_text,
                        official_digest_text=official_digest_text,
                        current_bill_text="",
                        source_hash=source_hash,
                        timestamp=timestamp,
                    )
                    index_payload = _build_index_payload(source_hash=source_hash, **comparisons)
                    existing_bill = get_bill("az", year, item["billNum"])
                    existing_amendments = list_bill_amendments("az", year, item["billNum"])

                    if executor is None:
                        completed = _complete_state_bill(
                            settings=settings,
                            index_key=key,
                            year=year,
                            state_code="az",
                            base_payload=base_payload,
                            index_payload=index_payload,
                            detail=detail,
                            status_info=status_info,
                            official_summary_text=official_summary_text,
                            official_digest_text=official_digest_text,
                            skip_interpretation=skip_interpretation,
                            existing_bill=existing_bill,
                            existing_amendments=existing_amendments,
                        )
                        _apply_completed_bill(completed, stats, existing_index, log, progress=progress)
                    else:
                        future = executor.submit(
                            _complete_state_bill,
                            settings,
                            key,
                            year,
                            "az",
                            base_payload,
                            index_payload,
                            detail,
                            status_info,
                            official_summary_text,
                            official_digest_text,
                            skip_interpretation,
                            existing_bill,
                            existing_amendments,
                        )
                        pending[future] = (item["billNum"], year)
                        if len(pending) >= settings.sync_parallelism * 2:
                            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=False)
                except Exception as exc:  # noqa: BLE001
                    stats.failed += 1
                    log(f"Failed to sync {item['billNum']} ({year}): {exc}")
                    progress.note_failed(item["billNum"], year, str(exc))

            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=True)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("az", year))
            if stop_after_limit:
                break
    except Exception as exc:  # noqa: BLE001
        fatal_error = str(exc)
        raise
    finally:
        api.close()
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
        progress.finish(fatal_error=fatal_error)

    return stats


def sync_connecticut(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    init_db()
    years = years or list(settings.connecticut_years)
    log = logger or (lambda message: None)
    stats = SyncStats(years=list(years))
    existing_index = get_existing_index(list(years), state="ct")
    api = ConnecticutApiClient(settings)
    executor = _sync_executor(settings, skip_interpretation)
    progress = SyncProgressTracker("ct", list(years), stats, iso_now())
    progress.start()
    fatal_error: str | None = None

    try:
        processed = 0
        stop_after_limit = False
        for year in years:
            pending: dict[Future[CompletedBillSync], tuple[str, int]] = {}
            log(f"Fetching Connecticut bill list for {year}")
            progress.note_year(year)
            bills = api.fetch_year_bills(year)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("ct", year))
            for item in bills:
                if limit is not None and processed >= limit:
                    stop_after_limit = True
                    break

                stats.seen += 1
                processed += 1
                key = (year, normalize_special_session(None), item["billNum"])
                existing = existing_index.get(key)

                try:
                    detail = api.fetch_bill_detail(str(item.get("detailPath") or ""))
                    official_summary_text = first_non_empty(
                        html_to_text(detail.get("summaryHTML")),
                        detail.get("catchTitle"),
                    )
                    official_digest_text = first_non_empty(
                        html_to_text(detail.get("digestHTML")),
                        detail.get("billTitle"),
                    )
                    source_hash = _compute_source_hash(detail, official_summary_text, official_digest_text, "")
                    status_info = classify_bill_status(
                        bill_status=detail.get("billStatus"),
                        last_action=detail.get("lastAction"),
                        signed_date=detail.get("signedDate"),
                        chapter_no=detail.get("chapter"),
                        enrolled_no=detail.get("enrolledNumber"),
                    )
                    comparisons = _state_detail_comparisons(detail)
                    if not _needs_refresh_from_values(existing, comparisons, skip_interpretation, settings.ollama_model) and (
                        existing is not None and str(existing.get("source_hash") or "") == source_hash
                    ):
                        stats.skipped += 1
                        progress.note_checked(item["billNum"], year, changed=False)
                        continue

                    timestamp = iso_now()
                    base_payload = _build_state_payload(
                        state_code="ct",
                        year=year,
                        detail=detail,
                        status_info=status_info,
                        official_summary_text=official_summary_text,
                        official_digest_text=official_digest_text,
                        current_bill_text="",
                        source_hash=source_hash,
                        timestamp=timestamp,
                    )
                    index_payload = _build_index_payload(source_hash=source_hash, **comparisons)
                    existing_bill = get_bill("ct", year, item["billNum"])
                    existing_amendments = list_bill_amendments("ct", year, item["billNum"])

                    if executor is None:
                        completed = _complete_state_bill(
                            settings=settings,
                            index_key=key,
                            year=year,
                            state_code="ct",
                            base_payload=base_payload,
                            index_payload=index_payload,
                            detail=detail,
                            status_info=status_info,
                            official_summary_text=official_summary_text,
                            official_digest_text=official_digest_text,
                            skip_interpretation=skip_interpretation,
                            existing_bill=existing_bill,
                            existing_amendments=existing_amendments,
                        )
                        _apply_completed_bill(completed, stats, existing_index, log, progress=progress)
                    else:
                        future = executor.submit(
                            _complete_state_bill,
                            settings,
                            key,
                            year,
                            "ct",
                            base_payload,
                            index_payload,
                            detail,
                            status_info,
                            official_summary_text,
                            official_digest_text,
                            skip_interpretation,
                            existing_bill,
                            existing_amendments,
                        )
                        pending[future] = (item["billNum"], year)
                        if len(pending) >= settings.sync_parallelism * 2:
                            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=False)
                except Exception as exc:  # noqa: BLE001
                    stats.failed += 1
                    log(f"Failed to sync {item['billNum']} ({year}): {exc}")
                    progress.note_failed(item["billNum"], year, str(exc))

            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=True)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("ct", year))
            if stop_after_limit:
                break
    except Exception as exc:  # noqa: BLE001
        fatal_error = str(exc)
        raise
    finally:
        api.close()
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
        progress.finish(fatal_error=fatal_error)

    return stats


def sync_virginia(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    init_db()
    years = years or list(settings.virginia_years)
    log = logger or (lambda message: None)
    stats = SyncStats(years=list(years))
    existing_index = get_existing_index(list(years), state="va")
    api = VirginiaApiClient(settings)
    executor = _sync_executor(settings, skip_interpretation)
    progress = SyncProgressTracker("va", list(years), stats, iso_now())
    progress.start()
    fatal_error: str | None = None

    try:
        processed = 0
        stop_after_limit = False
        for year in years:
            pending: dict[Future[CompletedBillSync], tuple[str, int]] = {}
            log(f"Fetching Virginia bill list for {year}")
            progress.note_year(year)
            bills = api.fetch_year_bills(year)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("va", year))
            for item in bills:
                if limit is not None and processed >= limit:
                    stop_after_limit = True
                    break

                stats.seen += 1
                processed += 1
                key = (year, normalize_special_session(None), item["billNum"])
                existing = existing_index.get(key)

                if not _needs_refresh_from_values(
                    existing,
                    _state_list_comparisons(item),
                    skip_interpretation,
                    settings.ollama_model,
                ):
                    stats.skipped += 1
                    progress.note_checked(item["billNum"], year, changed=False)
                    continue

                try:
                    detail = api.fetch_bill_detail(year, str(item["billNum"]))
                    official_summary_text = first_non_empty(
                        html_to_text(detail.get("summaryHTML")),
                        detail.get("catchTitle"),
                    )
                    official_digest_text = first_non_empty(
                        html_to_text(detail.get("digestHTML")),
                        detail.get("billTitle"),
                    )
                    current_bill_text = html_to_text(detail.get("currentBillHTML"))
                    source_hash = _compute_source_hash(detail, official_summary_text, official_digest_text, current_bill_text)
                    status_info = classify_bill_status(
                        bill_status=detail.get("billStatus"),
                        last_action=detail.get("lastAction"),
                        signed_date=detail.get("signedDate"),
                        chapter_no=detail.get("chapter"),
                        enrolled_no=detail.get("enrolledNumber"),
                    )
                    comparisons = _state_detail_comparisons(detail)
                    if not _needs_refresh_from_values(existing, comparisons, skip_interpretation, settings.ollama_model) and (
                        existing is not None and str(existing.get("source_hash") or "") == source_hash
                    ):
                        stats.skipped += 1
                        progress.note_checked(item["billNum"], year, changed=False)
                        continue

                    timestamp = iso_now()
                    base_payload = _build_state_payload(
                        state_code="va",
                        year=year,
                        detail=detail,
                        status_info=status_info,
                        official_summary_text=official_summary_text,
                        official_digest_text=official_digest_text,
                        current_bill_text=current_bill_text,
                        source_hash=source_hash,
                        timestamp=timestamp,
                    )
                    index_payload = _build_index_payload(source_hash=source_hash, **comparisons)
                    existing_bill = get_bill("va", year, item["billNum"])
                    existing_amendments = list_bill_amendments("va", year, item["billNum"])

                    if executor is None:
                        completed = _complete_state_bill(
                            settings=settings,
                            index_key=key,
                            year=year,
                            state_code="va",
                            base_payload=base_payload,
                            index_payload=index_payload,
                            detail=detail,
                            status_info=status_info,
                            official_summary_text=official_summary_text,
                            official_digest_text=official_digest_text,
                            skip_interpretation=skip_interpretation,
                            existing_bill=existing_bill,
                            existing_amendments=existing_amendments,
                        )
                        _apply_completed_bill(completed, stats, existing_index, log, progress=progress)
                    else:
                        future = executor.submit(
                            _complete_state_bill,
                            settings,
                            key,
                            year,
                            "va",
                            base_payload,
                            index_payload,
                            detail,
                            status_info,
                            official_summary_text,
                            official_digest_text,
                            skip_interpretation,
                            existing_bill,
                            existing_amendments,
                        )
                        pending[future] = (item["billNum"], year)
                        if len(pending) >= settings.sync_parallelism * 2:
                            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=False)
                except Exception as exc:  # noqa: BLE001
                    stats.failed += 1
                    log(f"Failed to sync {item['billNum']} ({year}): {exc}")
                    progress.note_failed(item["billNum"], year, str(exc))

            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=True)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("va", year))
            if stop_after_limit:
                break
    except Exception as exc:  # noqa: BLE001
        fatal_error = str(exc)
        raise
    finally:
        api.close()
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
        progress.finish(fatal_error=fatal_error)

    return stats


def sync_florida(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    init_db()
    years = years or list(settings.florida_years)
    log = logger or (lambda message: None)
    stats = SyncStats(years=list(years))
    existing_index = get_existing_index(list(years), state="fl")
    api = FloridaApiClient(settings)
    # Florida count seeding uses index-only records, and the full-detail sync path
    # is kept single-threaded to avoid tripping the Legislature site rate limits.
    executor = None
    progress = SyncProgressTracker("fl", list(years), stats, iso_now())
    progress.start()
    fatal_error: str | None = None

    try:
        processed = 0
        stop_after_limit = False
        for year in years:
            pending: dict[Future[CompletedBillSync], tuple[str, int]] = {}
            log(f"Fetching Florida bill list for {year}")
            progress.note_year(year)
            bills = api.fetch_year_bills(year)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("fl", year))
            for item in bills:
                if limit is not None and processed >= limit:
                    stop_after_limit = True
                    break

                stats.seen += 1
                processed += 1
                key = (year, normalize_special_session(None), item["billNum"])
                existing = existing_index.get(key)

                try:
                    detail = (
                        _build_florida_index_detail(item)
                        if skip_interpretation
                        else api.fetch_bill_detail(str(item.get("detailPath") or ""))
                    )
                    official_summary_text = first_non_empty(
                        html_to_text(detail.get("summaryHTML")),
                        detail.get("catchTitle"),
                    )
                    official_digest_text = first_non_empty(
                        html_to_text(detail.get("digestHTML")),
                        detail.get("billTitle"),
                    )
                    source_hash = _compute_source_hash(detail, official_summary_text, official_digest_text, "")
                    status_info = classify_bill_status(
                        bill_status=detail.get("billStatus"),
                        last_action=detail.get("lastAction"),
                        signed_date=detail.get("signedDate"),
                        chapter_no=detail.get("chapter"),
                        enrolled_no=detail.get("enrolledNumber"),
                    )
                    comparisons = _state_detail_comparisons(detail)
                    if not _needs_refresh_from_values(existing, comparisons, skip_interpretation, settings.ollama_model) and (
                        existing is not None and str(existing.get("source_hash") or "") == source_hash
                    ):
                        stats.skipped += 1
                        progress.note_checked(item["billNum"], year, changed=False)
                        continue

                    timestamp = iso_now()
                    base_payload = _build_state_payload(
                        state_code="fl",
                        year=year,
                        detail=detail,
                        status_info=status_info,
                        official_summary_text=official_summary_text,
                        official_digest_text=official_digest_text,
                        current_bill_text="",
                        source_hash=source_hash,
                        timestamp=timestamp,
                    )
                    index_payload = _build_index_payload(source_hash=source_hash, **comparisons)
                    existing_bill = get_bill("fl", year, item["billNum"])
                    existing_amendments = list_bill_amendments("fl", year, item["billNum"])

                    if executor is None:
                        completed = _complete_state_bill(
                            settings=settings,
                            index_key=key,
                            year=year,
                            state_code="fl",
                            base_payload=base_payload,
                            index_payload=index_payload,
                            detail=detail,
                            status_info=status_info,
                            official_summary_text=official_summary_text,
                            official_digest_text=official_digest_text,
                            skip_interpretation=skip_interpretation,
                            existing_bill=existing_bill,
                            existing_amendments=existing_amendments,
                        )
                        _apply_completed_bill(completed, stats, existing_index, log, progress=progress)
                    else:
                        future = executor.submit(
                            _complete_state_bill,
                            settings,
                            key,
                            year,
                            "fl",
                            base_payload,
                            index_payload,
                            detail,
                            status_info,
                            official_summary_text,
                            official_digest_text,
                            skip_interpretation,
                            existing_bill,
                            existing_amendments,
                        )
                        pending[future] = (item["billNum"], year)
                        if len(pending) >= settings.sync_parallelism * 2:
                            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=False)
                except Exception as exc:  # noqa: BLE001
                    stats.failed += 1
                    log(f"Failed to sync {item['billNum']} ({year}): {exc}")

            if pending:
                _drain_futures(pending, stats, existing_index, log, progress=progress)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("fl", year))
            if stop_after_limit:
                break
    except Exception as exc:  # noqa: BLE001
        fatal_error = str(exc)
        raise
    finally:
        progress.finish(fatal_error=fatal_error)
        api.close()
        if executor is not None:
            executor.shutdown(wait=True)

    return stats


def sync_idaho(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="id",
        years=years,
        default_years=settings.idaho_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_north_dakota(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="nd",
        years=years,
        default_years=settings.north_dakota_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_maryland(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    init_db()
    years = years or list(settings.maryland_years)
    log = logger or (lambda message: None)
    stats = SyncStats(years=list(years))
    existing_index = get_existing_index(list(years), state="md")
    api = MarylandApiClient(settings)
    executor = _sync_executor(settings, skip_interpretation)
    progress = SyncProgressTracker("md", list(years), stats, iso_now())
    progress.start()
    fatal_error: str | None = None

    try:
        processed = 0
        stop_after_limit = False
        for year in years:
            pending: dict[Future[CompletedBillSync], tuple[str, int]] = {}
            log(f"Fetching Maryland bill list for {year}")
            progress.note_year(year)
            bills = api.fetch_year_bills(year)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("md", year))
            for item in bills:
                if limit is not None and processed >= limit:
                    stop_after_limit = True
                    break

                stats.seen += 1
                processed += 1
                key = (year, normalize_special_session(None), item["billNum"])
                existing = existing_index.get(key)

                try:
                    detail = (
                        _build_maryland_index_detail(item)
                        if skip_interpretation
                        else api.fetch_bill_detail(str(item.get("detailPath") or ""))
                    )
                    detail["chapter"] = first_non_empty(detail.get("chapter"), item.get("chapter"))
                    detail["sponsor"] = first_non_empty(detail.get("sponsor"), item.get("sponsor"))
                    detail["billStatus"] = first_non_empty(detail.get("billStatus"), item.get("billStatus"))
                    detail["lastAction"] = first_non_empty(detail.get("lastAction"), item.get("lastAction"), detail.get("billStatus"))
                    detail["crossfileBillNumber"] = first_non_empty(
                        detail.get("crossfileBillNumber"),
                        item.get("crossfileBillNumber"),
                    )
                    official_summary_text = first_non_empty(
                        html_to_text(detail.get("summaryHTML")),
                        item.get("summaryText"),
                        detail.get("catchTitle"),
                    )
                    official_digest_text = first_non_empty(
                        html_to_text(detail.get("digestHTML")),
                        detail.get("billTitle"),
                    )
                    source_hash = _compute_source_hash(detail, official_summary_text, official_digest_text, "")
                    status_info = classify_bill_status(
                        bill_status=detail.get("billStatus"),
                        last_action=detail.get("lastAction"),
                        signed_date=detail.get("signedDate"),
                        chapter_no=detail.get("chapter"),
                        enrolled_no=detail.get("enrolledNumber"),
                    )
                    comparisons = _state_detail_comparisons(detail)
                    if not _needs_refresh_from_values(existing, comparisons, skip_interpretation, settings.ollama_model) and (
                        existing is not None and str(existing.get("source_hash") or "") == source_hash
                    ):
                        stats.skipped += 1
                        progress.note_checked(item["billNum"], year, changed=False)
                        continue

                    timestamp = iso_now()
                    base_payload = _build_state_payload(
                        state_code="md",
                        year=year,
                        detail=detail,
                        status_info=status_info,
                        official_summary_text=official_summary_text,
                        official_digest_text=official_digest_text,
                        current_bill_text="",
                        source_hash=source_hash,
                        timestamp=timestamp,
                    )
                    index_payload = _build_index_payload(source_hash=source_hash, **comparisons)
                    existing_bill = get_bill("md", year, item["billNum"])
                    existing_amendments = list_bill_amendments("md", year, item["billNum"])

                    if executor is None:
                        completed = _complete_state_bill(
                            settings=settings,
                            index_key=key,
                            year=year,
                            state_code="md",
                            base_payload=base_payload,
                            index_payload=index_payload,
                            detail=detail,
                            status_info=status_info,
                            official_summary_text=official_summary_text,
                            official_digest_text=official_digest_text,
                            skip_interpretation=skip_interpretation,
                            existing_bill=existing_bill,
                            existing_amendments=existing_amendments,
                        )
                        _apply_completed_bill(completed, stats, existing_index, log, progress=progress)
                    else:
                        future = executor.submit(
                            _complete_state_bill,
                            settings,
                            key,
                            year,
                            "md",
                            base_payload,
                            index_payload,
                            detail,
                            status_info,
                            official_summary_text,
                            official_digest_text,
                            skip_interpretation,
                            existing_bill,
                            existing_amendments,
                        )
                        pending[future] = (item["billNum"], year)
                        if len(pending) >= settings.sync_parallelism * 2:
                            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=False)
                except Exception as exc:  # noqa: BLE001
                    stats.failed += 1
                    log(f"Failed to sync {item['billNum']} ({year}): {exc}")
                    progress.note_failed(item["billNum"], year, str(exc))

            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=True)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("md", year))
            if stop_after_limit:
                break
    except Exception as exc:  # noqa: BLE001
        fatal_error = str(exc)
        raise
    finally:
        progress.finish(fatal_error=fatal_error)
        api.close()
        if executor is not None:
            executor.shutdown(wait=True)

    return stats


def _build_florida_index_detail(item: dict[str, Any]) -> dict[str, Any]:
    bill_num = str(item.get("billNum") or "").strip().upper()
    bill_title = first_non_empty(item.get("catchTitle"), item.get("billTitle"), bill_num)
    sponsor = clean_text(str(item.get("sponsor") or ""))
    last_action = clean_text(str(item.get("lastAction") or item.get("billStatus") or ""))
    last_action_date = clean_text(str(item.get("lastActionDate") or ""))
    detail_path = str(item.get("detailPath") or "").strip()
    action_rows = []
    if last_action:
        action_rows.append(
            {
                "statusDate": last_action_date,
                "location": "",
                "statusMessage": last_action,
            }
        )
    return {
        "bill": bill_num,
        "billType": str(item.get("billType") or bill_num[:2]),
        "catchTitle": bill_title,
        "sponsor": sponsor,
        "billTitle": bill_title,
        "billStatus": last_action,
        "lastAction": last_action,
        "lastActionDate": last_action_date,
        "signedDate": "",
        "effectiveDate": "",
        "chapter": "",
        "enrolledNumber": "",
        "sponsorStringHouse": sponsor if bill_num.startswith("H") else None,
        "sponsorStringSenate": sponsor if bill_num.startswith("S") else None,
        "introduced": None,
        "digest": None,
        "summary": detail_path or None,
        "currentVersionPath": None,
        "currentVersionFingerprint": detail_path,
        "summaryHTML": f"<p>{bill_title}</p>" if bill_title else "",
        "digestHTML": "",
        "currentBillHTML": "",
        "billActions": action_rows,
        "amendments": [],
        "officialPage": detail_path,
    }


def _build_basic_index_detail(item: dict[str, Any]) -> dict[str, Any]:
    bill_num = str(item.get("billNum") or "").strip().upper()
    bill_title = first_non_empty(item.get("catchTitle"), item.get("billTitle"), bill_num)
    sponsor = clean_text(str(item.get("sponsor") or ""))
    detail_path = str(item.get("detailPath") or "").strip()
    current_version_path = clean_text(str(item.get("currentVersionPath") or "")) or None
    last_action = clean_text(str(item.get("lastAction") or item.get("billStatus") or ""))
    last_action_date = clean_text(str(item.get("lastActionDate") or ""))
    bill_actions = []
    if last_action:
        bill_actions.append(
            {
                "statusDate": last_action_date,
                "location": "",
                "statusMessage": last_action,
            }
        )
    return {
        "bill": bill_num,
        "billType": str(item.get("billType") or bill_num[:2]),
        "catchTitle": bill_title,
        "sponsor": sponsor,
        "billTitle": bill_title,
        "billStatus": clean_text(str(item.get("billStatus") or last_action)),
        "lastAction": last_action,
        "lastActionDate": last_action_date,
        "signedDate": clean_text(str(item.get("signedDate") or "")),
        "effectiveDate": clean_text(str(item.get("effectiveDate") or "")),
        "chapter": clean_text(str(item.get("chapter") or "")),
        "enrolledNumber": clean_text(str(item.get("enrolledNumber") or "")),
        "sponsorStringHouse": sponsor if bill_num.startswith("H") else None,
        "sponsorStringSenate": sponsor if bill_num.startswith("S") else None,
        "introduced": current_version_path,
        "digest": None,
        "summary": detail_path or None,
        "currentVersionPath": current_version_path,
        "currentVersionFingerprint": clean_text(
            str(item.get("currentVersionFingerprint") or current_version_path or detail_path)
        ),
        "summaryHTML": f"<p>{bill_title}</p>" if bill_title else "",
        "digestHTML": "",
        "currentBillHTML": "",
        "billActions": bill_actions,
        "amendments": [],
        "officialPage": detail_path,
        "specialSessionValue": item.get("specialSessionValue"),
    }


def _build_maryland_index_detail(item: dict[str, Any]) -> dict[str, Any]:
    bill_num = str(item.get("billNum") or "").strip().upper()
    bill_title = first_non_empty(item.get("catchTitle"), item.get("billTitle"), bill_num)
    sponsor = clean_text(str(item.get("sponsor") or ""))
    summary_text = clean_text(str(item.get("summaryText") or "")) or bill_title
    last_action = clean_text(str(item.get("lastAction") or item.get("billStatus") or ""))
    detail_path = str(item.get("detailPath") or "").strip()
    action_rows = []
    if last_action:
        action_rows.append(
            {
                "statusDate": clean_text(str(item.get("lastActionDate") or "")),
                "location": "",
                "statusMessage": last_action,
            }
        )
    return {
        "bill": bill_num,
        "billType": str(item.get("billType") or bill_num[:2]),
        "catchTitle": bill_title,
        "sponsor": sponsor,
        "billTitle": bill_title,
        "billStatus": clean_text(str(item.get("billStatus") or last_action)),
        "lastAction": last_action,
        "lastActionDate": clean_text(str(item.get("lastActionDate") or "")),
        "signedDate": "",
        "effectiveDate": clean_text(str(item.get("effectiveDate") or "")),
        "chapter": clean_text(str(item.get("chapter") or "")),
        "enrolledNumber": "",
        "sponsorStringHouse": sponsor if bill_num.startswith("H") else None,
        "sponsorStringSenate": sponsor if bill_num.startswith("S") else None,
        "introduced": None,
        "digest": None,
        "summary": detail_path or None,
        "currentVersionPath": None,
        "currentVersionFingerprint": detail_path,
        "summaryHTML": f"<p>{summary_text}</p>" if summary_text else "",
        "digestHTML": "",
        "currentBillHTML": "",
        "billActions": action_rows,
        "amendments": [],
        "officialPage": detail_path,
        "crossfileBillNumber": clean_text(str(item.get("crossfileBillNumber") or "")),
    }


def sync_massachusetts(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="ma",
        years=years,
        default_years=settings.massachusetts_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_michigan(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="mi",
        years=years,
        default_years=settings.michigan_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_washington(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="wa",
        years=years,
        default_years=settings.washington_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_rhode_island(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    init_db()
    years = years or list(settings.rhode_island_years)
    log = logger or (lambda message: None)
    stats = SyncStats(years=list(years))
    existing_index = get_existing_index(list(years), state="ri")
    api = RhodeIslandApiClient(settings)
    executor = _sync_executor(settings, skip_interpretation)
    progress = SyncProgressTracker("ri", list(years), stats, iso_now())
    progress.start()
    fatal_error: str | None = None

    try:
        processed = 0
        stop_after_limit = False
        for year in years:
            pending: dict[Future[CompletedBillSync], tuple[str, int]] = {}
            log(f"Fetching Rhode Island bill list for {year}")
            progress.note_year(year)
            bills = api.fetch_year_bills(year)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("ri", year))
            for item in bills:
                if limit is not None and processed >= limit:
                    stop_after_limit = True
                    break

                stats.seen += 1
                processed += 1
                key = (year, normalize_special_session(None), item["billNum"])
                existing = existing_index.get(key)

                try:
                    detail = api.fetch_bill_detail(year, item)
                    official_summary_text = first_non_empty(
                        html_to_text(detail.get("summaryHTML")),
                        detail.get("catchTitle"),
                        detail.get("billTitle"),
                    )
                    official_digest_text = first_non_empty(
                        html_to_text(detail.get("digestHTML")),
                        detail.get("billTitle"),
                    )
                    current_bill_text = api.fetch_public_document_text(detail.get("currentVersionPath"))
                    source_hash = _compute_source_hash(detail, official_summary_text, official_digest_text, current_bill_text)
                    status_info = classify_bill_status(
                        bill_status=detail.get("billStatus"),
                        last_action=detail.get("lastAction"),
                        signed_date=detail.get("signedDate"),
                        chapter_no=detail.get("chapter"),
                        enrolled_no=detail.get("enrolledNumber"),
                    )
                    comparisons = _state_detail_comparisons(detail)
                    if not _needs_refresh_from_values(existing, comparisons, skip_interpretation, settings.ollama_model) and (
                        existing is not None and str(existing.get("source_hash") or "") == source_hash
                    ):
                        stats.skipped += 1
                        progress.note_checked(item["billNum"], year, changed=False)
                        continue

                    timestamp = iso_now()
                    base_payload = _build_state_payload(
                        state_code="ri",
                        year=year,
                        detail=detail,
                        status_info=status_info,
                        official_summary_text=official_summary_text,
                        official_digest_text=official_digest_text,
                        current_bill_text=current_bill_text,
                        source_hash=source_hash,
                        timestamp=timestamp,
                    )
                    index_payload = _build_index_payload(source_hash=source_hash, **comparisons)
                    existing_bill = get_bill("ri", year, item["billNum"])
                    existing_amendments = list_bill_amendments("ri", year, item["billNum"])

                    if executor is None:
                        completed = _complete_state_bill(
                            settings=settings,
                            index_key=key,
                            year=year,
                            state_code="ri",
                            base_payload=base_payload,
                            index_payload=index_payload,
                            detail=detail,
                            status_info=status_info,
                            official_summary_text=official_summary_text,
                            official_digest_text=official_digest_text,
                            skip_interpretation=skip_interpretation,
                            existing_bill=existing_bill,
                            existing_amendments=existing_amendments,
                        )
                        _apply_completed_bill(completed, stats, existing_index, log, progress=progress)
                    else:
                        future = executor.submit(
                            _complete_state_bill,
                            settings,
                            key,
                            year,
                            "ri",
                            base_payload,
                            index_payload,
                            detail,
                            status_info,
                            official_summary_text,
                            official_digest_text,
                            skip_interpretation,
                            existing_bill,
                            existing_amendments,
                        )
                        pending[future] = (item["billNum"], year)
                        if len(pending) >= settings.sync_parallelism * 2:
                            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=False)
                except Exception as exc:  # noqa: BLE001
                    stats.failed += 1
                    log(f"Failed to sync {item['billNum']} ({year}): {exc}")
                    progress.note_failed(item["billNum"], year, str(exc))

            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=True)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("ri", year))
            if stop_after_limit:
                break
    except Exception as exc:  # noqa: BLE001
        fatal_error = str(exc)
        raise
    finally:
        api.close()
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
        progress.finish(fatal_error=fatal_error)

    return stats


def sync_minnesota(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    init_db()
    years = years or list(settings.minnesota_years)
    log = logger or (lambda message: None)
    stats = SyncStats(years=list(years))
    existing_index = get_existing_index(list(years), state="mn")
    api = MinnesotaApiClient(settings)
    executor = _sync_executor(settings, skip_interpretation)
    progress = SyncProgressTracker("mn", list(years), stats, iso_now())
    progress.start()
    fatal_error: str | None = None

    try:
        processed = 0
        stop_after_limit = False
        for year in years:
            pending: dict[Future[CompletedBillSync], tuple[str, int]] = {}
            log(f"Fetching Minnesota bill list for {year}")
            progress.note_year(year)
            bills = api.fetch_year_bills(year)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("mn", year))
            for item in bills:
                if limit is not None and processed >= limit:
                    stop_after_limit = True
                    break

                stats.seen += 1
                processed += 1
                special_session_value = item.get("specialSessionValue")
                key = (year, normalize_special_session(special_session_value), item["billNum"])
                existing = existing_index.get(key)

                try:
                    detail = api.fetch_bill_detail(str(item.get("detailPath") or ""))
                    official_summary_text = first_non_empty(
                        html_to_text(detail.get("summaryHTML")),
                        detail.get("catchTitle"),
                    )
                    official_digest_text = first_non_empty(
                        html_to_text(detail.get("digestHTML")),
                        detail.get("billTitle"),
                    )
                    source_hash = _compute_source_hash(detail, official_summary_text, official_digest_text, "")
                    status_info = classify_bill_status(
                        bill_status=detail.get("billStatus"),
                        last_action=detail.get("lastAction"),
                        signed_date=detail.get("signedDate"),
                        chapter_no=detail.get("chapter"),
                        enrolled_no=detail.get("enrolledNumber"),
                    )
                    comparisons = _state_detail_comparisons(detail)
                    if not _needs_refresh_from_values(existing, comparisons, skip_interpretation, settings.ollama_model) and (
                        existing is not None and str(existing.get("source_hash") or "") == source_hash
                    ):
                        stats.skipped += 1
                        progress.note_checked(item["billNum"], year, changed=False)
                        continue

                    timestamp = iso_now()
                    base_payload = _build_state_payload(
                        state_code="mn",
                        year=year,
                        detail=detail,
                        status_info=status_info,
                        official_summary_text=official_summary_text,
                        official_digest_text=official_digest_text,
                        current_bill_text="",
                        source_hash=source_hash,
                        timestamp=timestamp,
                    )
                    index_payload = _build_index_payload(source_hash=source_hash, **comparisons)
                    existing_bill = get_bill(
                        "mn",
                        year,
                        item["billNum"],
                        special_session_value=special_session_value,
                    )
                    existing_amendments = list_bill_amendments(
                        "mn",
                        year,
                        item["billNum"],
                        special_session_value=special_session_value,
                    )

                    if executor is None:
                        completed = _complete_state_bill(
                            settings=settings,
                            index_key=key,
                            year=year,
                            state_code="mn",
                            base_payload=base_payload,
                            index_payload=index_payload,
                            detail=detail,
                            status_info=status_info,
                            official_summary_text=official_summary_text,
                            official_digest_text=official_digest_text,
                            skip_interpretation=skip_interpretation,
                            existing_bill=existing_bill,
                            existing_amendments=existing_amendments,
                        )
                        _apply_completed_bill(completed, stats, existing_index, log, progress=progress)
                    else:
                        future = executor.submit(
                            _complete_state_bill,
                            settings,
                            key,
                            year,
                            "mn",
                            base_payload,
                            index_payload,
                            detail,
                            status_info,
                            official_summary_text,
                            official_digest_text,
                            skip_interpretation,
                            existing_bill,
                            existing_amendments,
                        )
                        pending[future] = (item["billNum"], year)
                        if len(pending) >= settings.sync_parallelism * 2:
                            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=False)
                except Exception as exc:  # noqa: BLE001
                    stats.failed += 1
                    log(f"Failed to sync {item['billNum']} ({year}): {exc}")
                    progress.note_failed(item["billNum"], year, str(exc))

            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=True)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("mn", year))
            if stop_after_limit:
                break
    except Exception as exc:  # noqa: BLE001
        fatal_error = str(exc)
        raise
    finally:
        api.close()
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
        progress.finish(fatal_error=fatal_error)

    return stats


def sync_missouri(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    init_db()
    years = years or list(settings.missouri_years)
    log = logger or (lambda message: None)
    stats = SyncStats(years=list(years))
    existing_index = get_existing_index(list(years), state="mo")
    api = MissouriApiClient(settings)
    executor = _sync_executor(settings, skip_interpretation)
    progress = SyncProgressTracker("mo", list(years), stats, iso_now())
    progress.start()
    fatal_error: str | None = None

    try:
        processed = 0
        stop_after_limit = False
        for year in years:
            pending: dict[Future[CompletedBillSync], tuple[str, int]] = {}
            log(f"Fetching Missouri bill list for {year}")
            progress.note_year(year)
            bills = api.fetch_year_bills(year)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("mo", year))
            for item in bills:
                if limit is not None and processed >= limit:
                    stop_after_limit = True
                    break

                stats.seen += 1
                processed += 1
                key = (year, normalize_special_session(None), item["billNum"])
                existing = existing_index.get(key)

                try:
                    if executor is None:
                        prepared = _prepare_missouri_bill_for_sync(
                            settings=settings,
                            index_key=key,
                            year=year,
                            item=item,
                            existing=existing,
                            skip_interpretation=skip_interpretation,
                        )
                        _apply_prepared_bill_result(prepared, stats, existing_index, log, progress=progress)
                    else:
                        future = executor.submit(
                            _prepare_missouri_bill_for_sync,
                            settings,
                            key,
                            year,
                            item,
                            existing,
                            skip_interpretation,
                        )
                        pending[future] = (item["billNum"], year)
                        if len(pending) >= settings.sync_parallelism * 2:
                            _drain_prepared_futures(
                                pending,
                                stats,
                                existing_index,
                                log,
                                progress=progress,
                                wait_for_all=False,
                            )
                except Exception as exc:  # noqa: BLE001
                    stats.failed += 1
                    log(f"Failed to sync {item['billNum']} ({year}): {exc}")
                    progress.note_failed(item["billNum"], year, str(exc))

            _drain_prepared_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=True)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("mo", year))
            if stop_after_limit:
                break
    except Exception as exc:  # noqa: BLE001
        fatal_error = str(exc)
        raise
    finally:
        api.close()
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
        progress.finish(fatal_error=fatal_error)

    return stats


def sync_west_virginia(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    init_db()
    years = years or list(settings.west_virginia_years)
    log = logger or (lambda message: None)
    stats = SyncStats(years=list(years))
    existing_index = get_existing_index(list(years), state="wv")
    api = WestVirginiaApiClient(settings)
    executor = _sync_executor(settings, skip_interpretation)
    progress = SyncProgressTracker("wv", list(years), stats, iso_now())
    progress.start()
    fatal_error: str | None = None

    try:
        processed = 0
        stop_after_limit = False
        for year in years:
            pending: dict[Future[CompletedBillSync], tuple[str, int]] = {}
            log(f"Fetching West Virginia bill list for {year}")
            progress.note_year(year)
            bills = api.fetch_year_bills(year)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("wv", year))
            for item in bills:
                if limit is not None and processed >= limit:
                    stop_after_limit = True
                    break

                stats.seen += 1
                processed += 1
                key = (year, normalize_special_session(None), item["billNum"])
                existing = existing_index.get(key)

                try:
                    detail = api.fetch_bill_detail(str(item.get("detailPath") or ""))
                    official_summary_text = first_non_empty(
                        html_to_text(detail.get("summaryHTML")),
                        detail.get("catchTitle"),
                    )
                    official_digest_text = first_non_empty(
                        html_to_text(detail.get("digestHTML")),
                        detail.get("billTitle"),
                    )
                    source_hash = _compute_source_hash(detail, official_summary_text, official_digest_text, "")
                    status_info = classify_bill_status(
                        bill_status=detail.get("billStatus"),
                        last_action=detail.get("lastAction"),
                        signed_date=detail.get("signedDate"),
                        chapter_no=detail.get("chapter"),
                        enrolled_no=detail.get("enrolledNumber"),
                    )
                    comparisons = _state_detail_comparisons(detail)
                    if not _needs_refresh_from_values(existing, comparisons, skip_interpretation, settings.ollama_model) and (
                        existing is not None and str(existing.get("source_hash") or "") == source_hash
                    ):
                        stats.skipped += 1
                        progress.note_checked(item["billNum"], year, changed=False)
                        continue

                    timestamp = iso_now()
                    base_payload = _build_state_payload(
                        state_code="wv",
                        year=year,
                        detail=detail,
                        status_info=status_info,
                        official_summary_text=official_summary_text,
                        official_digest_text=official_digest_text,
                        current_bill_text="",
                        source_hash=source_hash,
                        timestamp=timestamp,
                    )
                    index_payload = _build_index_payload(source_hash=source_hash, **comparisons)
                    existing_bill = get_bill("wv", year, item["billNum"])
                    existing_amendments = list_bill_amendments("wv", year, item["billNum"])

                    if executor is None:
                        completed = _complete_state_bill(
                            settings=settings,
                            index_key=key,
                            year=year,
                            state_code="wv",
                            base_payload=base_payload,
                            index_payload=index_payload,
                            detail=detail,
                            status_info=status_info,
                            official_summary_text=official_summary_text,
                            official_digest_text=official_digest_text,
                            skip_interpretation=skip_interpretation,
                            existing_bill=existing_bill,
                            existing_amendments=existing_amendments,
                        )
                        _apply_completed_bill(completed, stats, existing_index, log, progress=progress)
                    else:
                        future = executor.submit(
                            _complete_state_bill,
                            settings,
                            key,
                            year,
                            "wv",
                            base_payload,
                            index_payload,
                            detail,
                            status_info,
                            official_summary_text,
                            official_digest_text,
                            skip_interpretation,
                            existing_bill,
                            existing_amendments,
                        )
                        pending[future] = (item["billNum"], year)
                        if len(pending) >= settings.sync_parallelism * 2:
                            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=False)
                except Exception as exc:  # noqa: BLE001
                    stats.failed += 1
                    log(f"Failed to sync {item['billNum']} ({year}): {exc}")
                    progress.note_failed(item["billNum"], year, str(exc))

            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=True)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("wv", year))
            if stop_after_limit:
                break
    except Exception as exc:  # noqa: BLE001
        fatal_error = str(exc)
        raise
    finally:
        api.close()
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
        progress.finish(fatal_error=fatal_error)

    return stats


def sync_colorado(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    init_db()
    years = years or list(settings.colorado_years)
    log = logger or (lambda message: None)
    stats = SyncStats(years=list(years))
    existing_index = get_existing_index(list(years), state="co")
    api = ColoradoApiClient(settings)
    executor = _sync_executor(settings, skip_interpretation)
    progress = SyncProgressTracker("co", list(years), stats, iso_now())
    progress.start()
    fatal_error: str | None = None

    try:
        processed = 0
        stop_after_limit = False
        for year in years:
            pending: dict[Future[CompletedBillSync], tuple[str, int]] = {}
            log(f"Fetching Colorado bill list for {year}")
            progress.note_year(year)
            bills = api.fetch_year_bills(year)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("co", year))
            for item in bills:
                if limit is not None and processed >= limit:
                    stop_after_limit = True
                    break

                stats.seen += 1
                processed += 1
                key = (year, normalize_special_session(None), item["billNum"])
                existing = existing_index.get(key)

                if not _needs_refresh_from_values(
                    existing,
                    _state_list_comparisons(item),
                    skip_interpretation,
                    settings.ollama_model,
                ):
                    stats.skipped += 1
                    progress.note_checked(item["billNum"], year, changed=False)
                    continue

                try:
                    detail = api.fetch_bill_detail(str(item.get("detailPath") or ""))
                    official_summary_text = html_to_text(detail.get("summaryHTML"))
                    official_digest_text = html_to_text(detail.get("digestHTML"))
                    source_hash = _compute_source_hash(detail, official_summary_text, official_digest_text, "")
                    status_info = classify_bill_status(
                        bill_status=detail.get("billStatus"),
                        last_action=detail.get("lastAction"),
                        signed_date=detail.get("signedDate"),
                        chapter_no=detail.get("chapter"),
                        enrolled_no=detail.get("enrolledNumber"),
                    )
                    comparisons = _state_detail_comparisons(detail)
                    if not _needs_refresh_from_values(existing, comparisons, skip_interpretation, settings.ollama_model) and (
                        existing is not None and str(existing.get("source_hash") or "") == source_hash
                    ):
                        stats.skipped += 1
                        progress.note_checked(item["billNum"], year, changed=False)
                        continue

                    timestamp = iso_now()
                    base_payload = _build_state_payload(
                        state_code="co",
                        year=year,
                        detail=detail,
                        status_info=status_info,
                        official_summary_text=official_summary_text,
                        official_digest_text=official_digest_text,
                        current_bill_text="",
                        source_hash=source_hash,
                        timestamp=timestamp,
                    )
                    index_payload = _build_index_payload(source_hash=source_hash, **comparisons)
                    existing_bill = get_bill("co", year, item["billNum"])
                    existing_amendments = list_bill_amendments("co", year, item["billNum"])

                    if executor is None:
                        completed = _complete_state_bill(
                            settings=settings,
                            index_key=key,
                            year=year,
                            state_code="co",
                            base_payload=base_payload,
                            index_payload=index_payload,
                            detail=detail,
                            status_info=status_info,
                            official_summary_text=official_summary_text,
                            official_digest_text=official_digest_text,
                            skip_interpretation=skip_interpretation,
                            existing_bill=existing_bill,
                            existing_amendments=existing_amendments,
                        )
                        _apply_completed_bill(completed, stats, existing_index, log, progress=progress)
                    else:
                        future = executor.submit(
                            _complete_state_bill,
                            settings,
                            key,
                            year,
                            "co",
                            base_payload,
                            index_payload,
                            detail,
                            status_info,
                            official_summary_text,
                            official_digest_text,
                            skip_interpretation,
                            existing_bill,
                            existing_amendments,
                        )
                        pending[future] = (item["billNum"], year)
                        if len(pending) >= settings.sync_parallelism * 2:
                            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=False)
                except Exception as exc:  # noqa: BLE001
                    stats.failed += 1
                    log(f"Failed to sync {item['billNum']} ({year}): {exc}")
                    progress.note_failed(item["billNum"], year, str(exc))

            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=True)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("co", year))
            if stop_after_limit:
                break
    except Exception as exc:  # noqa: BLE001
        fatal_error = str(exc)
        raise
    finally:
        api.close()
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
        progress.finish(fatal_error=fatal_error)

    return stats


def sync_tennessee(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    init_db()
    years = years or list(settings.tennessee_years)
    log = logger or (lambda message: None)
    stats = SyncStats(years=list(years))
    existing_index = get_existing_index(list(years), state="tn")
    api = TennesseeApiClient(settings)
    executor = _sync_executor(settings, skip_interpretation)
    progress = SyncProgressTracker("tn", list(years), stats, iso_now())
    progress.start()
    fatal_error: str | None = None

    try:
        processed = 0
        stop_after_limit = False
        for year in years:
            pending: dict[Future[CompletedBillSync], tuple[str, int]] = {}
            log(f"Fetching Tennessee bill list for {year}")
            progress.note_year(year)
            bills = api.fetch_year_bills(year)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("tn", year))
            for item in bills:
                if limit is not None and processed >= limit:
                    stop_after_limit = True
                    break

                stats.seen += 1
                processed += 1
                key = (year, normalize_special_session(None), item["billNum"])
                existing = existing_index.get(key)

                try:
                    detail = api.fetch_bill_detail(year, str(item["billNum"]))
                    official_summary_text = first_non_empty(
                        html_to_text(detail.get("summaryHTML")),
                        detail.get("billSummaryText"),
                    )
                    official_digest_text = first_non_empty(
                        html_to_text(detail.get("digestHTML")),
                        detail.get("abstractText"),
                    )
                    source_hash = _compute_source_hash(detail, official_summary_text, official_digest_text, "")
                    status_info = classify_bill_status(
                        bill_status=detail.get("billStatus"),
                        last_action=detail.get("lastAction"),
                        signed_date=detail.get("signedDate"),
                        chapter_no=detail.get("chapter"),
                        enrolled_no=detail.get("enrolledNumber"),
                    )
                    comparisons = _state_detail_comparisons(detail)
                    if not _needs_refresh_from_values(existing, comparisons, skip_interpretation, settings.ollama_model) and (
                        existing is not None and str(existing.get("source_hash") or "") == source_hash
                    ):
                        stats.skipped += 1
                        progress.note_checked(item["billNum"], year, changed=False)
                        continue

                    timestamp = iso_now()
                    base_payload = _build_state_payload(
                        state_code="tn",
                        year=year,
                        detail=detail,
                        status_info=status_info,
                        official_summary_text=official_summary_text,
                        official_digest_text=official_digest_text,
                        current_bill_text="",
                        source_hash=source_hash,
                        timestamp=timestamp,
                    )
                    index_payload = _build_index_payload(source_hash=source_hash, **comparisons)
                    existing_bill = get_bill("tn", year, item["billNum"])
                    existing_amendments = list_bill_amendments("tn", year, item["billNum"])

                    if executor is None:
                        completed = _complete_state_bill(
                            settings=settings,
                            index_key=key,
                            year=year,
                            state_code="tn",
                            base_payload=base_payload,
                            index_payload=index_payload,
                            detail=detail,
                            status_info=status_info,
                            official_summary_text=official_summary_text,
                            official_digest_text=official_digest_text,
                            skip_interpretation=skip_interpretation,
                            existing_bill=existing_bill,
                            existing_amendments=existing_amendments,
                        )
                        _apply_completed_bill(completed, stats, existing_index, log, progress=progress)
                    else:
                        future = executor.submit(
                            _complete_state_bill,
                            settings,
                            key,
                            year,
                            "tn",
                            base_payload,
                            index_payload,
                            detail,
                            status_info,
                            official_summary_text,
                            official_digest_text,
                            skip_interpretation,
                            existing_bill,
                            existing_amendments,
                        )
                        pending[future] = (item["billNum"], year)
                        if len(pending) >= settings.sync_parallelism * 2:
                            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=False)
                except Exception as exc:  # noqa: BLE001
                    stats.failed += 1
                    log(f"Failed to sync {item['billNum']} ({year}): {exc}")
                    progress.note_failed(item["billNum"], year, str(exc))

            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=True)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("tn", year))
            if stop_after_limit:
                break
    except Exception as exc:  # noqa: BLE001
        fatal_error = str(exc)
        raise
    finally:
        api.close()
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
        progress.finish(fatal_error=fatal_error)

    return stats


def sync_mississippi(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    init_db()
    years = years or list(settings.mississippi_years)
    log = logger or (lambda message: None)
    stats = SyncStats(years=list(years))
    existing_index = get_existing_index(list(years), state="ms")
    api = MississippiApiClient(settings)
    executor = _sync_executor(settings, skip_interpretation)
    progress = SyncProgressTracker("ms", list(years), stats, iso_now())
    progress.start()
    fatal_error: str | None = None

    try:
        processed = 0
        stop_after_limit = False
        for year in years:
            pending: dict[Future[CompletedBillSync], tuple[str, int]] = {}
            log(f"Fetching Mississippi bill list for {year}")
            progress.note_year(year)
            bills = api.fetch_year_bills(year)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("ms", year))
            for item in bills:
                if limit is not None and processed >= limit:
                    stop_after_limit = True
                    break

                stats.seen += 1
                processed += 1
                key = (year, normalize_special_session(None), item["billNum"])
                existing = existing_index.get(key)

                if not _needs_refresh_from_values(
                    existing,
                    _state_list_comparisons(item),
                    skip_interpretation,
                    settings.ollama_model,
                ):
                    stats.skipped += 1
                    progress.note_checked(item["billNum"], year, changed=False)
                    continue

                try:
                    detail = api.fetch_bill_detail(year, str(item.get("detailPath") or ""), str(item.get("measurePath") or ""))
                    official_summary_text = html_to_text(detail.get("summaryHTML"))
                    official_digest_text = html_to_text(detail.get("digestHTML"))
                    source_hash = _compute_source_hash(detail, official_summary_text, official_digest_text, "")
                    status_info = classify_bill_status(
                        bill_status=detail.get("billStatus"),
                        last_action=detail.get("lastAction"),
                        signed_date=detail.get("signedDate"),
                        chapter_no=detail.get("chapter"),
                        enrolled_no=detail.get("enrolledNumber"),
                    )
                    comparisons = _state_detail_comparisons(detail)
                    if not _needs_refresh_from_values(existing, comparisons, skip_interpretation, settings.ollama_model) and (
                        existing is not None and str(existing.get("source_hash") or "") == source_hash
                    ):
                        stats.skipped += 1
                        progress.note_checked(item["billNum"], year, changed=False)
                        continue

                    timestamp = iso_now()
                    base_payload = _build_state_payload(
                        state_code="ms",
                        year=year,
                        detail=detail,
                        status_info=status_info,
                        official_summary_text=official_summary_text,
                        official_digest_text=official_digest_text,
                        current_bill_text="",
                        source_hash=source_hash,
                        timestamp=timestamp,
                    )
                    index_payload = _build_index_payload(source_hash=source_hash, **comparisons)
                    existing_bill = get_bill("ms", year, item["billNum"])
                    existing_amendments = list_bill_amendments("ms", year, item["billNum"])

                    if executor is None:
                        completed = _complete_state_bill(
                            settings=settings,
                            index_key=key,
                            year=year,
                            state_code="ms",
                            base_payload=base_payload,
                            index_payload=index_payload,
                            detail=detail,
                            status_info=status_info,
                            official_summary_text=official_summary_text,
                            official_digest_text=official_digest_text,
                            skip_interpretation=skip_interpretation,
                            existing_bill=existing_bill,
                            existing_amendments=existing_amendments,
                        )
                        _apply_completed_bill(completed, stats, existing_index, log, progress=progress)
                    else:
                        future = executor.submit(
                            _complete_state_bill,
                            settings,
                            key,
                            year,
                            "ms",
                            base_payload,
                            index_payload,
                            detail,
                            status_info,
                            official_summary_text,
                            official_digest_text,
                            skip_interpretation,
                            existing_bill,
                            existing_amendments,
                        )
                        pending[future] = (item["billNum"], year)
                        if len(pending) >= settings.sync_parallelism * 2:
                            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=False)
                except Exception as exc:  # noqa: BLE001
                    stats.failed += 1
                    log(f"Failed to sync {item['billNum']} ({year}): {exc}")
                    progress.note_failed(item["billNum"], year, str(exc))

            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=True)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year("ms", year))
            if stop_after_limit:
                break
    except Exception as exc:  # noqa: BLE001
        fatal_error = str(exc)
        raise
    finally:
        api.close()
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
        progress.finish(fatal_error=fatal_error)

    return stats


def _sync_detail_path_state(
    *,
    state_code: str,
    years: list[int] | None,
    default_years: tuple[int, ...],
    limit: int | None,
    skip_interpretation: bool,
    logger: Logger | None,
) -> SyncStats:
    settings = get_settings()
    init_db()
    years = years or list(default_years)
    log = logger or (lambda message: None)
    stats = SyncStats(years=list(years))
    existing_index = get_existing_index(list(years), state=state_code)
    api = _make_state_api(state_code, settings)
    executor = _sync_executor(settings, skip_interpretation)
    progress = SyncProgressTracker(state_code, list(years), stats, iso_now())
    progress.start()
    fatal_error: str | None = None

    try:
        processed = 0
        stop_after_limit = False
        for year in years:
            pending: dict[Future[CompletedBillSync], tuple[str, int]] = {}
            log(f"Fetching {state_code.upper()} bill list for {year}")
            progress.note_year(year)
            bills = api.fetch_year_bills(year)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year(state_code, year))
            for item in bills:
                if limit is not None and processed >= limit:
                    stop_after_limit = True
                    break

                stats.seen += 1
                processed += 1
                special_session_value = item.get("specialSessionValue")
                key = (year, normalize_special_session(special_session_value), item["billNum"])
                existing = existing_index.get(key)

                try:
                    detail = (
                        _build_basic_index_detail(item)
                        if skip_interpretation and not getattr(api, "index_requires_detail_fetch", False)
                        else api.fetch_bill_detail(str(item.get("detailPath") or ""), item)
                    )
                    detail["specialSessionValue"] = special_session_value
                    detail["bill"] = first_non_empty(detail.get("bill"), item.get("billNum"))
                    detail["billType"] = first_non_empty(detail.get("billType"), item.get("billType"))
                    detail["catchTitle"] = first_non_empty(
                        detail.get("catchTitle"),
                        item.get("catchTitle"),
                        item.get("billTitle"),
                        item.get("billNum"),
                    )
                    detail["billTitle"] = first_non_empty(
                        detail.get("billTitle"),
                        item.get("billTitle"),
                        detail.get("catchTitle"),
                    )
                    detail["sponsor"] = first_non_empty(detail.get("sponsor"), item.get("sponsor"))
                    detail["billStatus"] = first_non_empty(
                        detail.get("billStatus"),
                        item.get("billStatus"),
                        detail.get("lastAction"),
                        item.get("lastAction"),
                    )
                    detail["lastAction"] = first_non_empty(
                        detail.get("lastAction"),
                        item.get("lastAction"),
                        detail.get("billStatus"),
                    )
                    detail["lastActionDate"] = first_non_empty(detail.get("lastActionDate"), item.get("lastActionDate"))
                    detail["signedDate"] = first_non_empty(detail.get("signedDate"), item.get("signedDate"))
                    detail["effectiveDate"] = first_non_empty(detail.get("effectiveDate"), item.get("effectiveDate"))
                    detail["chapter"] = first_non_empty(detail.get("chapter"), item.get("chapter"))
                    detail["enrolledNumber"] = first_non_empty(detail.get("enrolledNumber"), item.get("enrolledNumber"))
                    detail["currentVersionPath"] = first_non_empty(
                        detail.get("currentVersionPath"),
                        item.get("currentVersionPath"),
                    )
                    detail["currentVersionFingerprint"] = first_non_empty(
                        detail.get("currentVersionFingerprint"),
                        item.get("currentVersionFingerprint"),
                        detail.get("currentVersionPath"),
                        item.get("currentVersionPath"),
                        item.get("detailPath"),
                    )
                    official_summary_text = first_non_empty(
                        html_to_text(detail.get("summaryHTML")),
                        detail.get("catchTitle"),
                        detail.get("billTitle"),
                    )
                    official_digest_text = first_non_empty(
                        html_to_text(detail.get("digestHTML")),
                        detail.get("billTitle"),
                    )
                    source_hash = _compute_source_hash(detail, official_summary_text, official_digest_text, "")
                    status_info = classify_bill_status(
                        bill_status=detail.get("billStatus"),
                        last_action=detail.get("lastAction"),
                        signed_date=detail.get("signedDate"),
                        chapter_no=detail.get("chapter"),
                        enrolled_no=detail.get("enrolledNumber"),
                    )
                    comparisons = _state_detail_comparisons(detail)
                    if not _needs_refresh_from_values(existing, comparisons, skip_interpretation, settings.ollama_model) and (
                        existing is not None and str(existing.get("source_hash") or "") == source_hash
                    ):
                        stats.skipped += 1
                        progress.note_checked(item["billNum"], year, changed=False)
                        continue

                    timestamp = iso_now()
                    base_payload = _build_state_payload(
                        state_code=state_code,
                        year=year,
                        detail=detail,
                        status_info=status_info,
                        official_summary_text=official_summary_text,
                        official_digest_text=official_digest_text,
                        current_bill_text="",
                        source_hash=source_hash,
                        timestamp=timestamp,
                    )
                    index_payload = _build_index_payload(source_hash=source_hash, **comparisons)
                    existing_bill = get_bill(
                        state_code,
                        year,
                        item["billNum"],
                        special_session_value=special_session_value,
                    )
                    existing_amendments = list_bill_amendments(
                        state_code,
                        year,
                        item["billNum"],
                        special_session_value=special_session_value,
                    )

                    if executor is None:
                        completed = _complete_state_bill(
                            settings=settings,
                            index_key=key,
                            year=year,
                            state_code=state_code,
                            base_payload=base_payload,
                            index_payload=index_payload,
                            detail=detail,
                            status_info=status_info,
                            official_summary_text=official_summary_text,
                            official_digest_text=official_digest_text,
                            skip_interpretation=skip_interpretation,
                            existing_bill=existing_bill,
                            existing_amendments=existing_amendments,
                        )
                        _apply_completed_bill(completed, stats, existing_index, log, progress=progress)
                    else:
                        future = executor.submit(
                            _complete_state_bill,
                            settings,
                            key,
                            year,
                            state_code,
                            base_payload,
                            index_payload,
                            detail,
                            status_info,
                            official_summary_text,
                            official_digest_text,
                            skip_interpretation,
                            existing_bill,
                            existing_amendments,
                        )
                        pending[future] = (item["billNum"], year)
                        if len(pending) >= settings.sync_parallelism * 2:
                            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=False)
                except Exception as exc:  # noqa: BLE001
                    stats.failed += 1
                    log(f"Failed to sync {item['billNum']} ({year}): {exc}")
                    progress.note_failed(item["billNum"], year, str(exc))

            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=True)
            progress.note_scope_totals(year, source_total=len(bills), stored_total=count_bills_for_year(state_code, year))
            if stop_after_limit:
                break
    except Exception as exc:  # noqa: BLE001
        fatal_error = str(exc)
        raise
    finally:
        api.close()
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
        progress.finish(fatal_error=fatal_error)

    return stats


def sync_arkansas(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="ar",
        years=years,
        default_years=settings.arkansas_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_california(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="ca",
        years=years,
        default_years=settings.california_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_georgia(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="ga",
        years=years,
        default_years=settings.georgia_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_illinois(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="il",
        years=years,
        default_years=settings.illinois_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_indiana(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="in",
        years=years,
        default_years=settings.indiana_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_iowa(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="ia",
        years=years,
        default_years=settings.iowa_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_kentucky(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="ky",
        years=years,
        default_years=settings.kentucky_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_louisiana(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="la",
        years=years,
        default_years=settings.louisiana_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_delaware(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="de",
        years=years,
        default_years=settings.delaware_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_new_hampshire(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="nh",
        years=years,
        default_years=settings.new_hampshire_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_hawaii(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="hi",
        years=years,
        default_years=settings.hawaii_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_new_york(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="ny",
        years=years,
        default_years=settings.new_york_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_district_of_columbia(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="dc",
        years=years,
        default_years=settings.district_of_columbia_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_maine(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="me",
        years=years,
        default_years=settings.maine_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_new_mexico(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="nm",
        years=years,
        default_years=settings.new_mexico_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_north_carolina(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="nc",
        years=years,
        default_years=settings.north_carolina_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_montana(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="mt",
        years=years,
        default_years=settings.montana_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_nevada(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="nv",
        years=years,
        default_years=settings.nevada_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_new_jersey(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="nj",
        years=years,
        default_years=settings.new_jersey_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_ohio(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="oh",
        years=years,
        default_years=settings.ohio_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_oregon(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="or",
        years=years,
        default_years=settings.oregon_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_pennsylvania(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="pa",
        years=years,
        default_years=settings.pennsylvania_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_oklahoma(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="ok",
        years=years,
        default_years=settings.oklahoma_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_nebraska(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="ne",
        years=years,
        default_years=settings.nebraska_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_south_carolina(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="sc",
        years=years,
        default_years=settings.south_carolina_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_south_dakota(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="sd",
        years=years,
        default_years=settings.south_dakota_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_texas(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="tx",
        years=years,
        default_years=settings.texas_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_vermont(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="vt",
        years=years,
        default_years=settings.vermont_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_utah(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="ut",
        years=years,
        default_years=settings.utah_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_wisconsin(
    years: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    return _sync_detail_path_state(
        state_code="wi",
        years=years,
        default_years=settings.wisconsin_years,
        limit=limit,
        skip_interpretation=skip_interpretation,
        logger=logger,
    )


def sync_federal(
    congresses: list[int] | None = None,
    limit: int | None = None,
    skip_interpretation: bool = False,
    logger: Logger | None = None,
) -> SyncStats:
    settings = get_settings()
    init_db()
    congresses = congresses or list(settings.federal_congresses)
    federal_limit = limit if limit is not None else settings.federal_sync_limit
    log = logger or (lambda message: None)
    stats = SyncStats(years=list(congresses))
    existing_index = get_existing_index(list(congresses), state="us")
    api = CongressApiClient(settings)
    executor = _sync_executor(settings, skip_interpretation)
    progress = SyncProgressTracker("us", list(congresses), stats, iso_now())
    progress.start()
    fatal_error: str | None = None

    try:
        processed = 0
        stop_after_limit = False
        for congress in congresses:
            if federal_limit <= 0:
                break

            pending: dict[Future[CompletedBillSync], tuple[str, int]] = {}
            log(f"Fetching recent Congress.gov bills for the {congress}th Congress")
            progress.note_year(congress)
            bills = api.fetch_recent_bills(congress, limit=federal_limit)
            for item in bills:
                if limit is not None and processed >= limit:
                    stop_after_limit = True
                    break

                bill_type = str(item.get("type") or "").strip().upper()
                number = str(item.get("number") or "").strip()
                if not bill_type or not number:
                    continue

                bill_num = congress_bill_identifier(bill_type, number)
                key = (congress, normalize_special_session(None), bill_num)
                existing = existing_index.get(key)
                comparisons = {
                    "bill_status": api.latest_action_text(item),
                    "last_action": api.latest_action_text(item),
                    "last_action_date": api.latest_action_date(item),
                }

                stats.seen += 1
                processed += 1
                if not _needs_refresh_from_values(existing, comparisons, skip_interpretation, settings.ollama_model):
                    stats.skipped += 1
                    progress.note_checked(bill_num, congress, changed=False)
                    continue

                try:
                    detail = api.fetch_bill_detail(congress, bill_type, number)
                    summaries = api.fetch_bill_summaries(congress, bill_type, number)
                    text_versions = api.fetch_bill_text_versions(congress, bill_type, number)
                    actions = api.fetch_bill_actions(congress, bill_type, number)
                    current_text_version, introduced_text_version, prompt_bill, status_info, source_hash, base_payload, index_payload = _prepare_federal_bill(
                        api=api,
                        congress=congress,
                        bill_num=bill_num,
                        bill_type=bill_type,
                        item=item,
                        detail=detail,
                        summaries=summaries,
                        text_versions=text_versions,
                        actions=actions,
                    )
                    official_summary_text = base_payload["official_summary_text"]
                    official_digest_text = base_payload["official_digest_text"]
                    current_bill_text = base_payload["current_bill_text"]
                    existing_bill = get_bill("us", congress, bill_num)
                    if executor is None:
                        completed = _complete_federal_bill(
                            settings=settings,
                            index_key=key,
                            congress=congress,
                            base_payload=base_payload,
                            index_payload=index_payload,
                            prompt_bill=prompt_bill,
                            status_info=status_info,
                            official_summary_text=official_summary_text,
                            official_digest_text=official_digest_text,
                            current_bill_text=current_bill_text,
                            skip_interpretation=skip_interpretation,
                            existing_bill=existing_bill,
                        )
                        _apply_completed_bill(completed, stats, existing_index, log, progress=progress)
                    else:
                        future = executor.submit(
                            _complete_federal_bill,
                            settings,
                            key,
                            congress,
                            base_payload,
                            index_payload,
                            prompt_bill,
                            status_info,
                            official_summary_text,
                            official_digest_text,
                            current_bill_text,
                            skip_interpretation,
                            existing_bill,
                        )
                        pending[future] = (bill_num, congress)
                        if len(pending) >= settings.sync_parallelism * 2:
                            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=False)
                except Exception as exc:  # noqa: BLE001
                    stats.failed += 1
                    log(f"Failed to sync {bill_num} ({congress}th Congress): {exc}")
                    progress.note_failed(bill_num, congress, str(exc))

            _drain_futures(pending, stats, existing_index, log, progress=progress, wait_for_all=True)
            if stop_after_limit:
                break
    except Exception as exc:  # noqa: BLE001
        fatal_error = str(exc)
        raise
    finally:
        api.close()
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
        progress.finish(fatal_error=fatal_error)

    return stats


def retag_bills(
    *,
    state: str,
    years: list[int] | None = None,
    logger: Logger | None = None,
) -> RetagStats:
    init_db()
    log = logger or (lambda message: None)
    selected_years = years or list_years(state)
    stats = RetagStats(state=state, years=list(selected_years))

    for year in selected_years:
        log(f"Retagging {state} bills for {year}")
        for bill in list_bills(state, year):
            stats.seen += 1
            amendments = list_bill_amendments(
                state,
                year,
                str(bill.get("bill_num") or ""),
                special_session_value=bill.get("special_session_value"),
            )
            interpretation = bill.get("interpretation_json")
            if not isinstance(interpretation, dict):
                interpretation = None
            bill_tags = extract_bill_tags(
                catch_title=str(bill.get("catch_title") or ""),
                sponsor=str(bill.get("sponsor") or ""),
                official_summary_text=str(bill.get("official_summary_text") or ""),
                official_digest_text=str(bill.get("official_digest_text") or ""),
                interpretation=interpretation,
                amendment_snippets=_amendment_search_snippets(amendments),
            )
            search_blob = _build_search_blob(bill, interpretation, bill_tags, amendments)
            if bill_tags:
                stats.tagged += 1

            existing_tags = [str(item or "").strip().lower() for item in bill.get("bill_tags_json") or []]
            if existing_tags == bill_tags and str(bill.get("search_blob") or "") == search_blob:
                continue

            payload = dict(bill)
            payload["bill_tags_json"] = bill_tags
            payload["search_blob"] = search_blob
            payload["updated_at"] = iso_now()
            upsert_bill(payload)
            stats.updated += 1
    return stats


def stats_as_json(stats: SyncStats) -> str:
    return json.dumps(asdict(stats), indent=2)


SYNC_STATE_FUNCTIONS: dict[str, Callable[..., SyncStats]] = {
    "alabama": sync_alabama,
    "alaska": sync_alaska,
    "arizona": sync_arizona,
    "arkansas": sync_arkansas,
    "california": sync_california,
    "colorado": sync_colorado,
    "connecticut": sync_connecticut,
    "delaware": sync_delaware,
    "district_of_columbia": sync_district_of_columbia,
    "federal": sync_federal,
    "florida": sync_florida,
    "georgia": sync_georgia,
    "hawaii": sync_hawaii,
    "idaho": sync_idaho,
    "illinois": sync_illinois,
    "indiana": sync_indiana,
    "iowa": sync_iowa,
    "kansas": sync_kansas,
    "kentucky": sync_kentucky,
    "louisiana": sync_louisiana,
    "maine": sync_maine,
    "maryland": sync_maryland,
    "massachusetts": sync_massachusetts,
    "michigan": sync_michigan,
    "minnesota": sync_minnesota,
    "mississippi": sync_mississippi,
    "missouri": sync_missouri,
    "montana": sync_montana,
    "nebraska": sync_nebraska,
    "nevada": sync_nevada,
    "new_hampshire": sync_new_hampshire,
    "new_jersey": sync_new_jersey,
    "new_mexico": sync_new_mexico,
    "new_york": sync_new_york,
    "north_carolina": sync_north_carolina,
    "north_dakota": sync_north_dakota,
    "ohio": sync_ohio,
    "oklahoma": sync_oklahoma,
    "oregon": sync_oregon,
    "pennsylvania": sync_pennsylvania,
    "rhode_island": sync_rhode_island,
    "south_carolina": sync_south_carolina,
    "south_dakota": sync_south_dakota,
    "tennessee": sync_tennessee,
    "texas": sync_texas,
    "utah": sync_utah,
    "vermont": sync_vermont,
    "virginia": sync_virginia,
    "washington": sync_washington,
    "west_virginia": sync_west_virginia,
    "wisconsin": sync_wisconsin,
    "wyoming": sync_wyoming,
}


def normalize_sync_state_name(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def sync_state_by_name(state_name: str, *, logger: Logger | None = None) -> SyncStats:
    normalized = normalize_sync_state_name(state_name)
    sync_func = SYNC_STATE_FUNCTIONS.get(normalized)
    if sync_func is None:
        valid_states = ", ".join(sorted(SYNC_STATE_FUNCTIONS))
        raise ValueError(f"Unknown KLS sync state {state_name!r}. Valid states: {valid_states}")
    return sync_func(logger=logger)


def sync_states(
    state_names: list[str],
    *,
    logger: Logger | None = None,
    stale_after_seconds: int = 21600,
) -> tuple[dict[str, SyncStats], dict[str, str]]:
    log = logger or (lambda message: None)
    selected_states = [normalize_sync_state_name(state_name) for state_name in state_names if normalize_sync_state_name(state_name)]
    if not selected_states:
        raise ValueError("At least one KLS sync state is required.")

    cleared = reset_stale_sync_statuses(stale_after_seconds)
    if cleared:
        log(f"Cleared {cleared} stale KLS sync status row(s).")

    completed: dict[str, SyncStats] = {}
    failed: dict[str, str] = {}
    for state_name in selected_states:
        log(f"Starting {state_name} sync")
        try:
            completed[state_name] = sync_state_by_name(state_name, logger=log)
        except Exception as exc:  # noqa: BLE001
            failed[state_name] = str(exc)
            log(f"State {state_name} sync failed: {exc}")
        else:
            log(f"Completed {state_name} sync")
    return completed, failed


def _sync_executor(settings: Settings, skip_interpretation: bool) -> ThreadPoolExecutor | None:
    if settings.sync_parallelism <= 1:
        return None
    return ThreadPoolExecutor(max_workers=settings.sync_parallelism)


def _drain_prepared_futures(
    pending: dict[Future[PreparedBillResult], tuple[str, int]],
    stats: SyncStats,
    existing_index: dict[tuple[int, int, str], dict[str, Any]],
    log: Logger,
    *,
    progress: SyncProgressTracker | None = None,
    wait_for_all: bool,
) -> None:
    if not pending:
        return
    while pending:
        done, _ = wait(tuple(pending.keys()), return_when=ALL_COMPLETED if wait_for_all else FIRST_COMPLETED)
        for future in done:
            bill_num, year = pending.pop(future)
            try:
                prepared = future.result()
            except Exception as exc:  # noqa: BLE001
                stats.failed += 1
                log(f"Failed to finish {bill_num} ({year}): {exc}")
                if progress is not None:
                    progress.note_failed(bill_num, year, str(exc))
                continue
            _apply_prepared_bill_result(prepared, stats, existing_index, log, progress=progress)
        if not wait_for_all:
            break


def _apply_prepared_bill_result(
    prepared: PreparedBillResult,
    stats: SyncStats,
    existing_index: dict[tuple[int, int, str], dict[str, Any]],
    log: Logger,
    *,
    progress: SyncProgressTracker | None = None,
) -> None:
    if prepared.skipped:
        stats.skipped += 1
        if progress is not None:
            progress.note_checked(prepared.bill_num, prepared.year, changed=False)
        return
    if prepared.completed is None:
        raise ValueError(f"Prepared bill result for {prepared.bill_num} ({prepared.year}) had no completed payload")
    _apply_completed_bill(prepared.completed, stats, existing_index, log, progress=progress)


def _drain_futures(
    pending: dict[Future[CompletedBillSync], tuple[str, int]],
    stats: SyncStats,
    existing_index: dict[tuple[int, int, str], dict[str, Any]],
    log: Logger,
    *,
    progress: SyncProgressTracker | None = None,
    wait_for_all: bool,
) -> None:
    if not pending:
        return
    while pending:
        done, _ = wait(tuple(pending.keys()), return_when=ALL_COMPLETED if wait_for_all else FIRST_COMPLETED)
        for future in done:
            bill_num, year = pending.pop(future)
            try:
                completed = future.result()
            except Exception as exc:  # noqa: BLE001
                stats.failed += 1
                log(f"Failed to finish {bill_num} ({year}): {exc}")
                if progress is not None:
                    progress.note_failed(bill_num, year, str(exc))
                continue
            _apply_completed_bill(completed, stats, existing_index, log, progress=progress)
        if not wait_for_all:
            break


def _apply_completed_bill(
    completed: CompletedBillSync,
    stats: SyncStats,
    existing_index: dict[tuple[int, int, str], dict[str, Any]],
    log: Logger,
    *,
    progress: SyncProgressTracker | None = None,
) -> None:
    upsert_bill(completed.payload)
    replace_bill_amendments(
        completed.payload["state"],
        completed.payload["year"],
        completed.payload["bill_num"],
        special_session_value=completed.payload.get("special_session_value"),
        payloads=completed.amendments,
    )
    existing_index[completed.index_key] = completed.index_payload
    stats.updated += 1
    stats.interpreted += completed.interpreted
    stats.validated += completed.validated
    stats.fallback_interpretations += completed.fallback_interpretations
    stats.amendments_seen += completed.amendments_seen
    stats.amendments_updated += len(completed.amendments)
    stats.amendments_summarized += completed.amendments_summarized
    stats.tagged += completed.tagged
    log(f"Updated {completed.bill_num} ({completed.year})")
    if progress is not None:
        progress.note_checked(completed.bill_num, completed.year, changed=True)


def _prepare_federal_bill(
    *,
    api: CongressApiClient,
    congress: int,
    bill_num: str,
    bill_type: str,
    item: dict[str, Any],
    detail: dict[str, Any],
    summaries: list[dict[str, Any]],
    text_versions: list[dict[str, Any]],
    actions: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any], dict[str, str], str, dict[str, Any], dict[str, Any]]:
    catch_title = str(detail.get("title") or item.get("title") or bill_num).strip()
    bill_title = catch_title
    sponsor = api.sponsor_name(detail)
    law = api.extract_law(detail)
    last_action = api.latest_action_text(detail) or api.latest_action_text(item)
    last_action_date = api.latest_action_date(detail) or api.latest_action_date(item)
    current_text_version = api.pick_text_version(text_versions)
    introduced_text_version = api.pick_text_version(text_versions, oldest=True)
    official_summary_text = api.latest_summary_text(summaries)
    official_digest_text = ""
    current_bill_text = api.fetch_text_content(current_text_version["url"]) if current_text_version else ""
    signed_date = ""
    if law.get("number") or "signed by president" in last_action.lower():
        signed_date = last_action_date
    status_info = classify_federal_bill_status(last_action, law.get("number"))
    prompt_bill = {
        "bill": bill_num,
        "catchTitle": catch_title,
        "billTitle": bill_title,
        "sponsor": sponsor,
        "billStatus": last_action,
        "lastAction": last_action,
        "lastActionDate": last_action_date,
        "signedDate": signed_date,
        "effectiveDate": "",
        "chapter": law.get("number"),
        "enrolledNumber": current_text_version["text_version_type"] if current_text_version else "",
        "currentVersionPath": current_text_version["url"] if current_text_version else "",
        "currentVersionFingerprint": (
            f"{current_text_version['text_version_type']}|{current_text_version['url']}"
            if current_text_version
            else ""
        ),
    }
    source_hash = _compute_source_hash(prompt_bill, official_summary_text, official_digest_text, current_bill_text)
    timestamp = iso_now()
    base_payload = _build_federal_payload(
        congress=congress,
        bill_num=bill_num,
        bill_type=bill_type,
        catch_title=catch_title,
        sponsor=sponsor,
        bill_title=bill_title,
        last_action=last_action,
        last_action_date=last_action_date,
        signed_date=signed_date,
        law_number=law.get("number"),
        current_text_version=current_text_version,
        introduced_text_version=introduced_text_version,
        official_summary_text=official_summary_text,
        official_digest_text=official_digest_text,
        current_bill_text=current_bill_text,
        status_info=status_info,
        source_hash=source_hash,
        bill_actions=api.action_rows(actions),
        timestamp=timestamp,
    )
    index_payload = _build_index_payload(
        bill_status=last_action,
        last_action=last_action,
        last_action_date=last_action_date,
        signed_date=signed_date,
        effective_date="",
        chapter_no=law.get("number"),
        enrolled_no=current_text_version["text_version_type"] if current_text_version else "",
        source_hash=source_hash,
    )
    return current_text_version, introduced_text_version, prompt_bill, status_info, source_hash, base_payload, index_payload


def _make_state_api(state_code: str, settings: Settings) -> Any:
    normalized = (state_code or "").strip().lower()
    if normalized == "ak":
        return AlaskaApiClient(settings)
    if normalized == "ks":
        return KansasApiClient(settings)
    if normalized == "ky":
        return KentuckyApiClient(settings)
    if normalized == "la":
        return LouisianaApiClient(settings)
    if normalized == "me":
        return MaineApiClient(settings)
    if normalized == "wy":
        return WyomingApiClient(settings)
    if normalized == "al":
        return AlabamaApiClient(settings)
    if normalized == "az":
        return ArizonaApiClient(settings)
    if normalized == "ar":
        return ArkansasApiClient(settings)
    if normalized == "ca":
        return CaliforniaApiClient(settings)
    if normalized == "ga":
        return GeorgiaApiClient(settings)
    if normalized == "hi":
        return HawaiiApiClient(settings)
    if normalized == "de":
        return DelawareApiClient(settings)
    if normalized == "dc":
        return DistrictOfColumbiaApiClient(settings)
    if normalized == "ct":
        return ConnecticutApiClient(settings)
    if normalized == "va":
        return VirginiaApiClient(settings)
    if normalized == "fl":
        return FloridaApiClient(settings)
    if normalized == "id":
        return IdahoApiClient(settings)
    if normalized == "in":
        return IndianaApiClient(settings)
    if normalized == "il":
        return IllinoisApiClient(settings)
    if normalized == "nd":
        return NorthDakotaApiClient(settings)
    if normalized == "ia":
        return IowaApiClient(settings)
    if normalized == "md":
        return MarylandApiClient(settings)
    if normalized == "ma":
        return MassachusettsApiClient(settings)
    if normalized == "mi":
        return MichiganApiClient(settings)
    if normalized == "wa":
        return WashingtonApiClient(settings)
    if normalized == "nc":
        return NorthCarolinaApiClient(settings)
    if normalized == "ri":
        return RhodeIslandApiClient(settings)
    if normalized == "mn":
        return MinnesotaApiClient(settings)
    if normalized == "mo":
        return MissouriApiClient(settings)
    if normalized == "mt":
        return MontanaApiClient(settings)
    if normalized == "nv":
        return NevadaApiClient(settings)
    if normalized == "nh":
        return NewHampshireApiClient(settings)
    if normalized == "nj":
        return NewJerseyApiClient(settings)
    if normalized == "ny":
        return NewYorkApiClient(settings)
    if normalized == "oh":
        return OhioApiClient(settings)
    if normalized == "ok":
        return OklahomaApiClient(settings)
    if normalized == "or":
        return OregonApiClient(settings)
    if normalized == "pa":
        return PennsylvaniaApiClient(settings)
    if normalized == "nm":
        return NewMexicoApiClient(settings)
    if normalized == "ne":
        return NebraskaApiClient(settings)
    if normalized == "sc":
        return SouthCarolinaApiClient(settings)
    if normalized == "sd":
        return SouthDakotaApiClient(settings)
    if normalized == "vt":
        return VermontApiClient(settings)
    if normalized == "ut":
        return UtahApiClient(settings)
    if normalized == "wv":
        return WestVirginiaApiClient(settings)
    if normalized == "wi":
        return WisconsinApiClient(settings)
    if normalized == "co":
        return ColoradoApiClient(settings)
    if normalized == "tx":
        return TexasApiClient(settings)
    if normalized == "tn":
        return TennesseeApiClient(settings)
    if normalized == "ms":
        return MississippiApiClient(settings)
    raise ValueError(f"Unsupported state source: {state_code}")


def _state_list_comparisons(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "bill_status": item.get("billStatus"),
        "last_action": item.get("lastAction"),
        "last_action_date": item.get("lastActionDate"),
        "signed_date": item.get("signedDate"),
        "effective_date": item.get("effectiveDate"),
        "chapter_no": item.get("chapter"),
        "enrolled_no": item.get("enrolledNumber"),
    }


def _state_detail_comparisons(detail: dict[str, Any]) -> dict[str, Any]:
    return {
        "bill_status": detail.get("billStatus"),
        "last_action": detail.get("lastAction"),
        "last_action_date": detail.get("lastActionDate"),
        "signed_date": detail.get("signedDate"),
        "effective_date": detail.get("effectiveDate"),
        "chapter_no": detail.get("chapter"),
        "enrolled_no": detail.get("enrolledNumber"),
    }


def _build_state_payload(
    *,
    state_code: str,
    year: int,
    detail: dict[str, Any],
    status_info: dict[str, str],
    official_summary_text: str,
    official_digest_text: str,
    current_bill_text: str,
    source_hash: str,
    timestamp: str,
) -> dict[str, Any]:
    return {
        "state": state_code,
        "year": year,
        "special_session_value": detail.get("specialSessionValue"),
        "bill_num": detail.get("bill"),
        "bill_type": detail.get("billType"),
        "catch_title": detail.get("catchTitle"),
        "sponsor": detail.get("sponsor"),
        "bill_title": detail.get("billTitle"),
        "bill_status": detail.get("billStatus"),
        "status_label": status_info["label"],
        "status_explainer": status_info["explanation"],
        "outcome": status_info["outcome"],
        "last_action": detail.get("lastAction"),
        "last_action_date": detail.get("lastActionDate"),
        "signed_date": detail.get("signedDate"),
        "effective_date": detail.get("effectiveDate"),
        "chapter_no": detail.get("chapter"),
        "enrolled_no": detail.get("enrolledNumber"),
        "sponsor_string_house": detail.get("sponsorStringHouse"),
        "sponsor_string_senate": detail.get("sponsorStringSenate"),
        "introduced_path": detail.get("introduced"),
        "digest_path": detail.get("digest"),
        "summary_path": detail.get("summary"),
        "current_version_path": detail.get("currentVersionPath"),
        "official_digest_text": official_digest_text,
        "official_summary_text": official_summary_text,
        "current_bill_text": current_bill_text,
        "bill_actions_json": detail.get("billActions") or [],
        "interpretation_json": None,
        "bill_tags_json": [],
        "search_blob": "",
        "source_hash": source_hash,
        "source_synced_at": timestamp,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def _reusable_interpretation(
    existing_bill: dict[str, Any] | None,
    source_hash: str,
    current_model: str,
    *,
    require_model_match: bool = True,
) -> dict[str, Any] | None:
    if existing_bill is None or str(existing_bill.get("source_hash") or "") != source_hash:
        return None
    interpretation = existing_bill.get("interpretation_json")
    if not isinstance(interpretation, dict):
        return None
    if require_model_match and str(interpretation.get("generator_model") or "").strip() != current_model.strip():
        return None
    return dict(interpretation)


def _reusable_amendment_interpretation(
    existing_amendment: dict[str, Any] | None,
    source_hash: str,
    current_model: str,
    *,
    require_model_match: bool = True,
) -> dict[str, Any] | None:
    if existing_amendment is None or str(existing_amendment.get("source_hash") or "") != source_hash:
        return None
    interpretation = existing_amendment.get("interpretation_json")
    if not isinstance(interpretation, dict):
        return None
    if require_model_match and str(interpretation.get("generator_model") or "").strip() != current_model.strip():
        return None
    return dict(interpretation)


def _complete_state_bill(
    settings: Settings,
    index_key: tuple[int, int, str],
    year: int,
    state_code: str,
    base_payload: dict[str, Any],
    index_payload: dict[str, Any],
    detail: dict[str, Any],
    status_info: dict[str, str],
    official_summary_text: str,
    official_digest_text: str,
    skip_interpretation: bool,
    existing_bill: dict[str, Any] | None = None,
    existing_amendments: list[dict[str, Any]] | None = None,
) -> CompletedBillSync:
    source_hash = str(base_payload.get("source_hash") or "")
    payload = dict(base_payload)
    current_bill_text = str(payload.get("current_bill_text") or "")
    reusable = _reusable_interpretation(
        existing_bill,
        source_hash,
        settings.ollama_model,
        require_model_match=not skip_interpretation,
    )

    api = None
    try:
        if not current_bill_text and existing_bill is not None and str(existing_bill.get("source_hash") or "") == source_hash:
            current_bill_text = str(existing_bill.get("current_bill_text") or "")
        if not current_bill_text and not skip_interpretation and payload.get("current_version_path"):
            api = _make_state_api(state_code, settings)
            current_bill_text = api.fetch_public_document_text(payload.get("current_version_path"))
        payload["current_bill_text"] = current_bill_text

        interpretation = reusable
        interpreted = 0
        validated = 0
        fallback_interpretations = 0
        if not skip_interpretation and interpretation is None:
            interpretation, interpreted, validated, fallback_interpretations = _interpret_bill_text(
                settings=settings,
                bill=detail,
                status_info=status_info,
                official_summary_text=official_summary_text,
                official_digest_text=official_digest_text,
                current_bill_text=current_bill_text,
            )

        amendment_items = detail.get("amendments") or []
        if amendment_items:
            if api is None:
                api = _make_state_api(state_code, settings)
            amendments, amendments_summarized = _build_state_amendments(
                api=api,
                state_code=state_code,
                settings=settings,
                year=year,
                bill=detail,
                amendments=amendment_items,
                skip_interpretation=skip_interpretation,
                existing_amendments=existing_amendments or [],
            )
        else:
            amendments, amendments_summarized = [], 0

        bill_tags = extract_bill_tags(
            catch_title=detail.get("catchTitle"),
            sponsor=detail.get("sponsor"),
            official_summary_text=official_summary_text,
            official_digest_text=official_digest_text,
            interpretation=interpretation,
            amendment_snippets=_amendment_search_snippets(amendments),
        )
        payload["interpretation_json"] = interpretation
        payload["bill_tags_json"] = bill_tags
        payload["search_blob"] = _build_search_blob(payload, interpretation, bill_tags, amendments)
        return CompletedBillSync(
            index_key=index_key,
            bill_num=str(base_payload["bill_num"]),
            year=year,
            payload=payload,
            index_payload=index_payload,
            amendments=amendments,
            interpreted=interpreted,
            validated=validated,
            fallback_interpretations=fallback_interpretations,
            amendments_seen=len(detail.get("amendments") or []),
            amendments_summarized=amendments_summarized,
            tagged=1 if bill_tags else 0,
        )
    finally:
        if api is not None:
            api.close()


def _prepare_missouri_bill_for_sync(
    settings: Settings,
    index_key: tuple[int, int, str],
    year: int,
    item: dict[str, Any],
    existing: dict[str, Any] | None,
    skip_interpretation: bool,
) -> PreparedBillResult:
    bill_num = str(item.get("billNum") or "")
    api = MissouriApiClient(settings)
    try:
        detail = api.fetch_bill_detail(str(item.get("detailPath") or ""))
        official_summary_text = first_non_empty(
            html_to_text(detail.get("summaryHTML")),
            detail.get("catchTitle"),
            detail.get("billTitle"),
        )
        official_digest_text = first_non_empty(
            html_to_text(detail.get("digestHTML")),
            detail.get("billTitle"),
        )
        current_bill_text = "" if skip_interpretation else api.fetch_public_document_text(detail.get("currentVersionPath"))
        source_hash = _compute_source_hash(detail, official_summary_text, official_digest_text, current_bill_text)
        status_info = classify_bill_status(
            bill_status=detail.get("billStatus"),
            last_action=detail.get("lastAction"),
            signed_date=detail.get("signedDate"),
            chapter_no=detail.get("chapter"),
            enrolled_no=detail.get("enrolledNumber"),
        )
        comparisons = _state_detail_comparisons(detail)
        if not _needs_refresh_from_values(existing, comparisons, skip_interpretation, settings.ollama_model) and (
            existing is not None and str(existing.get("source_hash") or "") == source_hash
        ):
            return PreparedBillResult(bill_num=bill_num, year=year, skipped=True)

        timestamp = iso_now()
        base_payload = _build_state_payload(
            state_code="mo",
            year=year,
            detail=detail,
            status_info=status_info,
            official_summary_text=official_summary_text,
            official_digest_text=official_digest_text,
            current_bill_text=current_bill_text,
            source_hash=source_hash,
            timestamp=timestamp,
        )
        index_payload = _build_index_payload(source_hash=source_hash, **comparisons)
        existing_bill = get_bill("mo", year, bill_num)
        existing_amendments = list_bill_amendments("mo", year, bill_num)
        completed = _complete_state_bill(
            settings=settings,
            index_key=index_key,
            year=year,
            state_code="mo",
            base_payload=base_payload,
            index_payload=index_payload,
            detail=detail,
            status_info=status_info,
            official_summary_text=official_summary_text,
            official_digest_text=official_digest_text,
            skip_interpretation=skip_interpretation,
            existing_bill=existing_bill,
            existing_amendments=existing_amendments,
        )
        return PreparedBillResult(bill_num=bill_num, year=year, completed=completed)
    finally:
        api.close()


def _complete_wyoming_bill(
    settings: Settings,
    index_key: tuple[int, int, str],
    year: int,
    base_payload: dict[str, Any],
    index_payload: dict[str, Any],
    detail: dict[str, Any],
    status_info: dict[str, str],
    official_summary_text: str,
    official_digest_text: str,
    current_bill_text: str,
    skip_interpretation: bool,
    existing_bill: dict[str, Any] | None = None,
    existing_amendments: list[dict[str, Any]] | None = None,
) -> CompletedBillSync:
    return _complete_state_bill(
        settings=settings,
        index_key=index_key,
        year=year,
        state_code="wy",
        base_payload=base_payload,
        index_payload=index_payload,
        detail=detail,
        status_info=status_info,
        official_summary_text=official_summary_text,
        official_digest_text=official_digest_text,
        skip_interpretation=skip_interpretation,
        existing_bill=existing_bill,
        existing_amendments=existing_amendments,
    )


def _complete_federal_bill(
    settings: Settings,
    index_key: tuple[int, int, str],
    congress: int,
    base_payload: dict[str, Any],
    index_payload: dict[str, Any],
    prompt_bill: dict[str, Any],
    status_info: dict[str, str],
    official_summary_text: str,
    official_digest_text: str,
    current_bill_text: str,
    skip_interpretation: bool,
    existing_bill: dict[str, Any] | None = None,
) -> CompletedBillSync:
    source_hash = str(base_payload.get("source_hash") or "")
    interpretation = _reusable_interpretation(
        existing_bill,
        source_hash,
        settings.ollama_model,
        require_model_match=not skip_interpretation,
    )
    interpreted = 0
    validated = 0
    fallback_interpretations = 0
    if not skip_interpretation and interpretation is None:
        interpretation, interpreted, validated, fallback_interpretations = _interpret_bill_text(
            settings=settings,
            bill=prompt_bill,
            status_info=status_info,
            official_summary_text=official_summary_text,
            official_digest_text=official_digest_text,
            current_bill_text=current_bill_text,
        )

    bill_tags = extract_bill_tags(
        catch_title=prompt_bill.get("catchTitle"),
        sponsor=prompt_bill.get("sponsor"),
        official_summary_text=official_summary_text,
        official_digest_text=official_digest_text,
        interpretation=interpretation,
    )
    payload = dict(base_payload)
    payload["interpretation_json"] = interpretation
    payload["bill_tags_json"] = bill_tags
    payload["search_blob"] = _build_search_blob(payload, interpretation, bill_tags, [])
    return CompletedBillSync(
        index_key=index_key,
        bill_num=str(base_payload["bill_num"]),
        year=congress,
        payload=payload,
        index_payload=index_payload,
        amendments=[],
        interpreted=interpreted,
        validated=validated,
        fallback_interpretations=fallback_interpretations,
        tagged=1 if bill_tags else 0,
    )


def _interpret_bill_text(
    *,
    settings: Settings,
    bill: dict[str, Any],
    status_info: dict[str, str],
    official_summary_text: str,
    official_digest_text: str,
    current_bill_text: str,
) -> tuple[dict[str, Any], int, int, int]:
    try:
        ollama = OllamaClient(settings)
        try:
            draft_interpretation = ollama.generate_interpretation(
                bill=bill,
                status_info=status_info,
                official_summary_text=official_summary_text,
                official_digest_text=official_digest_text,
                current_bill_text=current_bill_text,
            )
            interpretation = ollama.fact_check_interpretation(
                bill=bill,
                status_info=status_info,
                official_summary_text=official_summary_text,
                official_digest_text=official_digest_text,
                current_bill_text=current_bill_text,
                candidate_interpretation=draft_interpretation,
            )
        finally:
            ollama.close()
        interpretation = _mark_validated_interpretation(interpretation, settings.ollama_model)
        if not _has_interpretation_content(interpretation):
            raise RuntimeError("Fact-check removed unsupported generated content")
        return interpretation, 1, 1, 0
    except Exception:  # noqa: BLE001
        interpretation = _fallback_interpretation(
            bill,
            official_summary_text,
            official_digest_text,
            current_bill_text,
            settings.ollama_model,
        )
        return interpretation, 0, 0, 1


def _build_wyoming_payload(
    *,
    year: int,
    detail: dict[str, Any],
    status_info: dict[str, str],
    official_summary_text: str,
    official_digest_text: str,
    current_bill_text: str,
    source_hash: str,
    timestamp: str,
) -> dict[str, Any]:
    normalized_detail = dict(detail)
    normalized_detail["currentVersionPath"] = first_non_empty(
        detail.get("enrolledAct"),
        detail.get("engrossedVersion"),
        detail.get("introduced"),
    )
    return _build_state_payload(
        state_code="wy",
        year=year,
        detail=normalized_detail,
        status_info=status_info,
        official_summary_text=official_summary_text,
        official_digest_text=official_digest_text,
        current_bill_text=current_bill_text,
        source_hash=source_hash,
        timestamp=timestamp,
    )


def _build_federal_payload(
    *,
    congress: int,
    bill_num: str,
    bill_type: str,
    catch_title: str,
    sponsor: str,
    bill_title: str,
    last_action: str,
    last_action_date: str,
    signed_date: str,
    law_number: str,
    current_text_version: dict[str, Any] | None,
    introduced_text_version: dict[str, Any] | None,
    official_summary_text: str,
    official_digest_text: str,
    current_bill_text: str,
    status_info: dict[str, str],
    source_hash: str,
    bill_actions: list[dict[str, str]],
    timestamp: str,
) -> dict[str, Any]:
    return {
        "state": "us",
        "year": congress,
        "special_session_value": None,
        "bill_num": bill_num,
        "bill_type": bill_type,
        "catch_title": catch_title,
        "sponsor": sponsor,
        "bill_title": bill_title,
        "bill_status": last_action,
        "status_label": status_info["label"],
        "status_explainer": status_info["explanation"],
        "outcome": status_info["outcome"],
        "last_action": last_action,
        "last_action_date": last_action_date,
        "signed_date": signed_date,
        "effective_date": "",
        "chapter_no": law_number,
        "enrolled_no": current_text_version["text_version_type"] if current_text_version else "",
        "sponsor_string_house": None,
        "sponsor_string_senate": None,
        "introduced_path": introduced_text_version["url"] if introduced_text_version else None,
        "digest_path": None,
        "summary_path": None,
        "current_version_path": current_text_version["url"] if current_text_version else None,
        "official_digest_text": official_digest_text,
        "official_summary_text": official_summary_text,
        "current_bill_text": current_bill_text,
        "bill_actions_json": bill_actions,
        "interpretation_json": None,
        "bill_tags_json": [],
        "search_blob": "",
        "source_hash": source_hash,
        "source_synced_at": timestamp,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def _build_index_payload(
    *,
    bill_status: str | None,
    last_action: str | None,
    last_action_date: str | None,
    signed_date: str | None,
    effective_date: str | None,
    chapter_no: str | None,
    enrolled_no: str | None,
    source_hash: str,
) -> dict[str, Any]:
    return {
        "bill_status": bill_status,
        "last_action": last_action,
        "last_action_date": last_action_date,
        "signed_date": signed_date,
        "effective_date": effective_date,
        "chapter_no": chapter_no,
        "enrolled_no": enrolled_no,
        "source_hash": source_hash,
    }


def _build_state_amendments(
    *,
    api: Any,
    state_code: str,
    settings: Settings,
    year: int,
    bill: dict[str, Any],
    amendments: list[dict[str, Any]],
    skip_interpretation: bool,
    existing_amendments: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    if not amendments:
        return [], 0

    existing_map = {
        str(item.get("amendment_number") or "").strip(): item
        for item in existing_amendments
        if str(item.get("amendment_number") or "").strip()
    }
    payloads: list[dict[str, Any]] = []
    summarized = 0
    ollama = None if skip_interpretation else OllamaClient(settings)
    try:
        for amendment in amendments:
            amendment_number = str(amendment.get("amendmentNumber") or "").strip()
            if not amendment_number:
                continue
            document_url = str(amendment.get("documentUrl") or "").strip()
            if state_code == "wy" and not document_url:
                document_url = api.public_amendment_url(year, amendment_number, extension="pdf")
            amendment_text = ""
            html_url = None
            if not skip_interpretation:
                try:
                    amendment_text = api.fetch_public_document_text(document_url)
                except Exception:  # noqa: BLE001
                    amendment_text = ""

                if state_code == "wy" and not amendment_text:
                    html_url = api.public_amendment_url(year, amendment_number, extension="htm")
                    try:
                        amendment_text = api.fetch_public_document_text(html_url)
                    except Exception:  # noqa: BLE001
                        amendment_text = ""

            normalized_amendment = {
                "amendmentNumber": amendment_number,
                "house": amendment.get("house"),
                "order": amendment.get("order"),
                "sequence": amendment.get("sequence"),
                "status": amendment.get("status"),
                "sponsor": amendment.get("sponsor"),
            }
            source_hash = _compute_amendment_hash(normalized_amendment, amendment_text)
            reusable = _reusable_amendment_interpretation(
                existing_map.get(amendment_number),
                source_hash,
                settings.ollama_model,
                require_model_match=not skip_interpretation,
            )

            if reusable is not None:
                interpretation = reusable
            elif ollama is not None and amendment_text:
                try:
                    interpretation = ollama.summarize_amendment(bill=bill, amendment=amendment, amendment_text=amendment_text)
                    if interpretation.get("one_sentence_summary") or interpretation.get("changes"):
                        summarized += 1
                except Exception:  # noqa: BLE001
                    interpretation = _fallback_amendment_interpretation(
                        amendment=amendment,
                        amendment_text=amendment_text,
                        generator_model=settings.ollama_model,
                        skipped=False,
                    )
            elif amendment_text:
                interpretation = _fallback_amendment_interpretation(
                    amendment=amendment,
                    amendment_text=amendment_text,
                    generator_model=settings.ollama_model,
                    skipped=True,
                )
            elif skip_interpretation:
                interpretation = _fallback_amendment_interpretation(
                    amendment=amendment,
                    amendment_text="",
                    generator_model=settings.ollama_model,
                    skipped=True,
                )
            else:
                interpretation = _unreadable_amendment_interpretation(amendment, settings.ollama_model)

            timestamp = iso_now()
            payloads.append(
                {
                    "state": state_code,
                    "year": year,
                    "special_session_value": bill.get("specialSessionValue"),
                    "bill_num": bill.get("bill"),
                    "amendment_number": amendment_number,
                    "chamber": amendment.get("house"),
                    "reading_order": amendment.get("order"),
                    "sequence": amendment.get("sequence"),
                    "status": amendment.get("status"),
                    "sponsor": amendment.get("sponsor"),
                    "document_url": document_url or html_url,
                    "document_text": amendment_text,
                    "interpretation_json": interpretation,
                    "source_hash": source_hash,
                    "source_synced_at": timestamp,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                }
            )
    finally:
        if ollama is not None:
            ollama.close()

    return payloads, summarized


def _build_search_blob(
    payload: dict[str, Any],
    interpretation: dict[str, Any] | None,
    bill_tags: list[str],
    amendments: list[dict[str, Any]],
) -> str:
    parts: list[str] = [
        str(payload.get("bill_num") or ""),
        str(payload.get("catch_title") or ""),
        str(payload.get("bill_title") or ""),
        str(payload.get("sponsor") or ""),
        str(payload.get("status_label") or ""),
        str(payload.get("status_explainer") or ""),
        str(payload.get("official_summary_text") or ""),
        str(payload.get("official_digest_text") or ""),
        clean_text(str(payload.get("current_bill_text") or ""))[:8000],
        " ".join(bill_tags),
    ]
    if interpretation:
        parts.extend(
            [
                str(interpretation.get("one_sentence_summary") or ""),
                " ".join(str(item) for item in interpretation.get("what_it_does", []) or []),
                " ".join(str(item) for item in interpretation.get("who_it_affects", []) or []),
                " ".join(str(item) for item in interpretation.get("limits_and_unknowns", []) or []),
            ]
        )
    for amendment in amendments:
        parts.append(str(amendment.get("amendment_number") or ""))
        parts.append(str(amendment.get("sponsor") or ""))
        parts.append(str(amendment.get("status") or ""))
        parts.append(str(amendment.get("reading_order") or ""))
        parts.append(clean_text(str(amendment.get("document_text") or ""))[:2500])
        amendment_interpretation = amendment.get("interpretation_json") or {}
        if isinstance(amendment_interpretation, dict):
            parts.append(str(amendment_interpretation.get("one_sentence_summary") or ""))
            parts.append(" ".join(str(item) for item in amendment_interpretation.get("changes", []) or []))
            parts.append(" ".join(str(item) for item in amendment_interpretation.get("limits_and_unknowns", []) or []))
    return clean_text("\n".join(part for part in parts if str(part).strip()))[:20000]


def _amendment_search_snippets(amendments: list[dict[str, Any]]) -> list[str]:
    snippets: list[str] = []
    for amendment in amendments:
        interpretation = amendment.get("interpretation_json") or {}
        if isinstance(interpretation, dict):
            snippets.append(str(interpretation.get("one_sentence_summary") or ""))
            snippets.extend(str(item) for item in interpretation.get("changes", []) or [])
    return [item for item in snippets if str(item).strip()]


def _compute_source_hash(
    detail: dict[str, Any],
    official_summary_text: str,
    official_digest_text: str,
    current_bill_text: str,
) -> str:
    hash_input = {
        "bill": detail.get("bill"),
        "catchTitle": detail.get("catchTitle"),
        "billTitle": detail.get("billTitle"),
        "summary": official_summary_text,
        "digest": official_digest_text,
        "billText": current_bill_text,
        "currentVersionPath": detail.get("currentVersionPath"),
        "currentVersionFingerprint": detail.get("currentVersionFingerprint"),
    }
    encoded = json.dumps(hash_input, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _compute_amendment_hash(amendment: dict[str, Any], amendment_text: str) -> str:
    hash_input = {
        "amendmentNumber": amendment.get("amendmentNumber"),
        "house": amendment.get("house"),
        "order": amendment.get("order"),
        "sequence": amendment.get("sequence"),
        "status": amendment.get("status"),
        "sponsor": amendment.get("sponsor"),
        "text": amendment_text,
    }
    encoded = json.dumps(hash_input, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _fallback_interpretation(
    detail: dict[str, Any],
    official_summary_text: str,
    official_digest_text: str,
    current_bill_text: str,
    generator_model: str,
) -> dict[str, Any]:
    source_text = first_non_empty(official_summary_text, official_digest_text, current_bill_text, detail.get("billTitle"))
    bullets = sentence_list(source_text, max_items=4)
    return {
        "plain_language_title": (detail.get("catchTitle") or detail.get("bill") or "").strip(),
        "one_sentence_summary": bullets[0] if bullets else "Official summary text is still being prepared for this bill.",
        "what_it_does": bullets[:4],
        "who_it_affects": [],
        "terms_to_know": [],
        "limits_and_unknowns": [
            "This entry is temporarily using official source text because the generated explanation could not be confirmed against the official bill text during the last sync."
        ],
        "fact_check_status": "fallback",
        "fact_check_result": "source-only",
        "fact_check_version": FACT_CHECK_VERSION,
        "generator_model": generator_model,
        "fact_check_notes": [
            "Using official source text because the generated explanation was unavailable or could not be confirmed against the official bill text."
        ],
        "removed_claims": [],
        "validator_notes": [],
    }


def _fallback_amendment_interpretation(
    *,
    amendment: dict[str, Any],
    amendment_text: str,
    generator_model: str,
    skipped: bool,
) -> dict[str, Any]:
    bullets = sentence_list(amendment_text, max_items=4)
    limits: list[str] = []
    if skipped:
        limits.append("This amendment summary is using official source text because generated interpretation was skipped for this run.")
    if not bullets:
        limits.append("The official amendment text was available, but an easy plain-English summary could not be produced automatically during the last sync.")
    return {
        "one_sentence_summary": bullets[0] if bullets else _amendment_status_summary(amendment),
        "changes": bullets[:4],
        "limits_and_unknowns": limits,
        "generator_model": generator_model,
    }


def _unreadable_amendment_interpretation(amendment: dict[str, Any], generator_model: str) -> dict[str, Any]:
    return {
        "one_sentence_summary": _amendment_status_summary(amendment),
        "changes": [],
        "limits_and_unknowns": [
            "The official amendment file could not be read automatically during the last sync, so only the official amendment metadata is shown right now."
        ],
        "generator_model": generator_model,
    }


def _amendment_status_summary(amendment: dict[str, Any]) -> str:
    status = str(amendment.get("status") or "Filed").strip()
    stage = str(amendment.get("order") or "").strip()
    sponsor = str(amendment.get("sponsor") or "").strip()
    pieces = [status]
    if stage:
        pieces.append(stage)
    if sponsor:
        pieces.append(f"by {sponsor}")
    return " ".join(pieces).strip() or "Official amendment metadata is available."


def _mark_validated_interpretation(interpretation: dict[str, Any], generator_model: str) -> dict[str, Any]:
    validated = dict(interpretation)
    removed_claims = [str(item).strip() for item in validated.get("removed_claims", []) if str(item).strip()]
    validator_notes = [str(item).strip() for item in validated.get("validator_notes", []) if str(item).strip()]
    validated["removed_claims"] = removed_claims
    validated["validator_notes"] = validator_notes
    validated["fact_check_status"] = "validated"
    validated["fact_check_result"] = "trimmed" if removed_claims else "passed"
    validated["fact_check_version"] = FACT_CHECK_VERSION
    validated["generator_model"] = generator_model
    validated["fact_check_notes"] = validator_notes or ["Checked against official source text during the last sync."]
    return validated


def _has_interpretation_content(interpretation: dict[str, Any]) -> bool:
    if str(interpretation.get("one_sentence_summary", "")).strip():
        return True
    for key in ("what_it_does", "who_it_affects", "terms_to_know"):
        if interpretation.get(key):
            return True
    return False


def _needs_refresh(
    existing: dict[str, Any] | None,
    item: dict[str, Any],
    skip_interpretation: bool,
    current_model: str | None = None,
) -> bool:
    comparisons = {
        "bill_status": item.get("billStatus"),
        "last_action": item.get("lastAction"),
        "last_action_date": item.get("lastActionDate"),
        "signed_date": item.get("SignedDate") or item.get("signedDate"),
        "effective_date": item.get("EffectiveDate") or item.get("effectiveDate"),
        "chapter_no": item.get("ChapterNo") or item.get("chapterNo"),
        "enrolled_no": item.get("EnrolledNo") or item.get("enrolledNo"),
    }
    return _needs_refresh_from_values(existing, comparisons, skip_interpretation, current_model)


def _needs_refresh_from_values(
    existing: dict[str, Any] | None,
    comparisons: dict[str, Any],
    skip_interpretation: bool,
    current_model: str | None = None,
) -> bool:
    if existing is None:
        return True
    if not skip_interpretation:
        if not int(existing.get("has_interpretation", 0)):
            return True
        if int(existing.get("fact_check_version", 0)) < FACT_CHECK_VERSION:
            return True
        if existing.get("fact_check_status") != "validated":
            return True
        if (current_model or "").strip() and str(existing.get("generator_model") or "").strip() != (current_model or "").strip():
            return True
    for key, new_value in comparisons.items():
        if (existing.get(key) or "") != (new_value or ""):
            return True
    return False
