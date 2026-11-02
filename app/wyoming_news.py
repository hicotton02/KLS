"""Bounded publisher discovery and a review-only queue for reported vote reasons."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup

from app.db import (connect, init_db, list_bill_roll_calls, open_pipeline_circuit_breaker,
                    pipeline_circuit_breaker_is_open)
from app.http_retry import retry_after_seconds
from app.ollama import GPUQueuePending, OllamaClient
from app.settings import get_settings
from app.text_utils import iso_now

USER_AGENT = "KeepingLawSimple/1.0 (+https://www.keepinglawsimple.org/about)"
PUBLISHERS = {
    "cowboystatedaily.com": ("Cowboy State Daily", "/news-sitemap.xml"),
    "wyofile.com": ("WyoFile", "/news-sitemap.xml"),
    "www.wyomingpublicmedia.org": ("Wyoming Public Media", "/news-sitemap-content.xml"),
}
MAX_ATTEMPTS = 4
MAX_BYTES = 3_000_000
VOTE_BILL = re.compile(r"\b(HB|SF|HJ|SJ|House\s+Bill|Senate\s+File)\s*0*(\d{1,4})\b", re.I)
OFFICIAL_BILL = re.compile(r"/Legislation/(\d{4})/((?:HB|SF|HJ|SJ)\d{1,4})(?:[/?#]|$)", re.I)


def normalize_url(value: str) -> str:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    host = {"www.cowboystatedaily.com": "cowboystatedaily.com", "www.wyofile.com": "wyofile.com",
            "wyomingpublicmedia.org": "www.wyomingpublicmedia.org"}.get(host, host)
    if (parsed.scheme != "https" or host not in PUBLISHERS or parsed.username or parsed.password
            or parsed.port not in (None, 443) or "\\" in parsed.path):
        raise ValueError("Only HTTPS articles from the three approved Wyoming publishers are allowed")
    return urlunparse(("https", host, parsed.path or "/", "", "", ""))


def _host(url: str) -> str:
    return str(urlparse(normalize_url(url)).hostname)


def _future(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


class SourceHold(ValueError):
    def __init__(self, message: str, status: str = "source_hold"):
        super().__init__(message)
        self.status = status


class PublisherClient:
    def __init__(self, *, client: httpx.Client | None = None, sleeper=time.sleep):
        self.client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=25, follow_redirects=False)
        self.robots: dict[str, RobotFileParser] = {}
        self.last_request: dict[str, float] = {}
        self.sleeper = sleeper

    def close(self) -> None:
        self.client.close()

    def _request(self, url: str) -> str:
        host = _host(url)
        circuit = "wyoming-news:" + host
        if pipeline_circuit_breaker_is_open(circuit):
            raise SourceHold("Publisher cooldown is active", "retry")
        elapsed = time.monotonic() - self.last_request.get(host, 0)
        self.sleeper(max(0, 2 - elapsed))
        self.last_request[host] = time.monotonic()
        with self.client.stream("GET", url, headers={"User-Agent": USER_AGENT}) as response:
            if response.status_code == 429:
                delay = retry_after_seconds(response.headers.get("retry-after"))
                open_pipeline_circuit_breaker(circuit, cooldown_seconds=max(3600, int(delay or 0)),
                                             reason="Publisher HTTP 429; honoring Retry-After")
                raise SourceHold("Publisher rate limit; wait for its cooldown", "retry")
            if response.status_code in (401, 403, 451):
                raise SourceHold(f"Publisher access restriction: HTTP {response.status_code}", "access_hold")
            if response.status_code in (404, 410):
                raise SourceHold(f"Publisher article unavailable: HTTP {response.status_code}")
            if response.is_redirect:
                return "REDIRECT:" + urljoin(url, response.headers.get("location", ""))
            response.raise_for_status()
            size = 0
            pieces = []
            for piece in response.iter_bytes():
                size += len(piece)
                if size > MAX_BYTES:
                    raise SourceHold("Publisher response exceeds the bounded download limit")
                pieces.append(piece)
            return b"".join(pieces).decode(response.encoding or "utf-8", errors="replace")

    def fetch(self, value: str) -> str:
        url = normalize_url(value)
        host = _host(url)
        if host not in self.robots:
            robots = self._request("https://" + host + "/robots.txt")
            if robots.startswith("REDIRECT:"):
                raise SourceHold("Cannot verify publisher robots rules after a redirect")
            parser = RobotFileParser()
            parser.parse(robots.splitlines())
            self.robots[host] = parser
        for _ in range(4):
            if _host(url) != host:
                raise SourceHold("Cross-publisher redirect requires review", "access_hold")
            rules = self.robots[host]
            if not rules.can_fetch(USER_AGENT, url):
                raise SourceHold("Publisher robots rules prohibit this URL", "access_hold")
            crawl_delay = rules.crawl_delay(USER_AGENT) or rules.crawl_delay("*") or 2
            elapsed = time.monotonic() - self.last_request.get(host, 0)
            self.sleeper(max(0, crawl_delay - elapsed))
            result = self._request(url)
            if not result.startswith("REDIRECT:"):
                return result
            try:
                url = normalize_url(result.removeprefix("REDIRECT:"))
            except ValueError as exc:
                raise SourceHold("Redirect leaves the approved publisher list", "out_of_scope") from exc
        raise SourceHold("Too many publisher redirects")


def _date(value: Any) -> str | None:
    try:
        return date.fromisoformat(str(value or "")[:10]).isoformat()
    except ValueError:
        return None


def parse_article(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    canonical = soup.find("link", rel="canonical")
    try:
        canonical_url = normalize_url(urljoin(url, str(canonical.get("href")))) if canonical else normalize_url(url)
    except ValueError as exc:
        raise SourceHold("Syndicated original is outside the approved publisher list", "out_of_scope") from exc
    if _host(canonical_url) != _host(url):
        raise SourceHold("Article canonical URL points to another publisher", "out_of_scope")
    published = soup.find("meta", property="article:published_time")
    published_date = _date(published.get("content")) if published else None
    article_data: dict[str, Any] = {}
    for node in soup.find_all("script", type="application/ld+json"):
        try:
            raw = json.loads(node.string or node.get_text())
        except (ValueError, TypeError):
            continue
        stack = raw if isinstance(raw, list) else [raw]
        while stack:
            item = stack.pop()
            if not isinstance(item, dict):
                continue
            kind = item.get("@type") or ""
            if any(t in (kind if isinstance(kind, list) else [kind]) for t in ("NewsArticle", "Article", "ReportageNewsArticle")):
                article_data = item
                published_date = published_date or _date(item.get("datePublished"))
            stack.extend(item.get("@graph") or [])
    if article_data.get("isAccessibleForFree") in (False, "false"):
        raise SourceHold("Article is marked paywalled; no paywall bypass", "access_hold")
    heading = soup.find("h1")
    title = str(article_data.get("headline") or (heading.get_text(" ", strip=True) if heading else "")).strip()
    body = next((found for selector in (".RichTextArticleBody", ".RichTextBody", ".entry-content",
                 "[itemprop='articleBody']", "article") if (found := soup.select_one(selector))), None)
    if body:
        for extra in body.select("script, style, nav, footer, aside, form, .related-posts, .sharedaddy"):
            extra.decompose()
        text = "\n".join(p.get_text(" ", strip=True) for p in body.select("p, h2, h3, blockquote"))
    else:
        text = ""
    if not text and article_data.get("articleBody"):
        text = BeautifulSoup(str(article_data["articleBody"]), "html.parser").get_text(" ", strip=True)
    if not title or not published_date or len(text.split()) < 60:
        raise SourceHold("Missing article title, publication date, or usable article body")
    if published_date > date.today().isoformat():
        raise SourceHold("Article publication date is in the future")
    links = []
    for anchor in (body or soup).find_all("a", href=True):
        href = str(anchor["href"])
        if (urlparse(href).hostname or "").lower() in ("wyoleg.gov", "www.wyoleg.gov"):
            match = OFFICIAL_BILL.search(urlparse(href).path + "/")
            if match:
                links.append([int(match[1]), normalize_bill(match[2])])
    text = text[:100_000]
    return {"url": canonical_url, "title": title[:500], "published_date": published_date,
            "content_text": text, "content_hash": hashlib.sha256(text.encode()).hexdigest(),
            "bill_links_json": json.dumps(links)}


def normalize_bill(value: str) -> str:
    match = VOTE_BILL.fullmatch(value.strip())
    if not match:
        raise ValueError("Invalid Wyoming bill number")
    prefix = match[1].upper().replace(" ", "")
    prefix = {"HOUSEBILL": "HB", "SENATEFILE": "SF"}.get(prefix, prefix)
    return f"{prefix}{int(match[2]):04d}"


def enqueue_article(url: str) -> int:
    url = normalize_url(url)
    now = iso_now()
    with connect() as c:
        c.execute("""INSERT INTO wyoming_news_articles(url,publisher,discovered_at,updated_at)
                     VALUES(?,?,?,?) ON CONFLICT(url) DO NOTHING""",
                  (url, PUBLISHERS[_host(url)][0], now, now))
        row = c.execute("SELECT id FROM wyoming_news_articles WHERE url=?", (url,)).fetchone()
    return int(row["id"])


def discover(client: PublisherClient, *, limit: int = 20) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for host, (_, sitemap) in PUBLISHERS.items():
        try:
            root = ElementTree.fromstring(client.fetch("https://" + host + sitemap))
            found = 0
            for entry in root:
                fields = {node.tag.rsplit("}", 1)[-1]: node.text or "" for node in entry.iter()}
                url = fields.get("loc", "")
                title = fields.get("title", "")
                if not url or not re.search(r"bill|senat|legislat|lawmaker|vot|\bhouse\b|tax|gun|abortion", title, re.I):
                    continue
                if not re.search(r"wyoming|cheyenne|wyoleg|lawmaker|legislat|house\s+bill|senate\s+file|\b[HS][BF]\s*\d", title, re.I):
                    continue
                url = normalize_url(url)
                if _host(url) != host:
                    continue
                with connect() as c:
                    exists = c.execute("SELECT id FROM wyoming_news_articles WHERE url=?", (url,)).fetchone()
                if exists:
                    continue
                enqueue_article(url)
                found += 1
                if found >= max(1, min(limit, 40)):
                    break
            report[host] = {"discovered": found}
        except (httpx.HTTPError, ValueError, ElementTree.ParseError) as exc:
            report[host] = {"error": str(exc)[:250]}
    return report


def claim_article() -> dict[str, Any] | None:
    now = iso_now()
    eligible = """attempts < ? AND
        ((status IN ('pending','retry','waiting_ai') AND (next_attempt_at IS NULL OR next_attempt_at <= ?))
         OR (status='processing' AND lease_expires_at <= ?))"""
    with connect() as c:
        c.execute("""UPDATE wyoming_news_articles SET status='quality_hold',lease_token=NULL,
                     lease_expires_at=NULL,last_error='Worker lease expired at the retry limit',updated_at=?
                     WHERE status='processing' AND lease_expires_at <= ? AND attempts >= ?""",
                  (now, now, MAX_ATTEMPTS))
        row = c.execute(f"""UPDATE wyoming_news_articles SET status='processing', attempts=attempts+1,
                      lease_token=?, lease_expires_at=?, updated_at=?
                      WHERE id=(SELECT id FROM wyoming_news_articles WHERE {eligible}
                                ORDER BY discovered_at,id LIMIT 1) AND {eligible} RETURNING *""",
                        (uuid.uuid4().hex, _future(1800), now, MAX_ATTEMPTS, now, now,
                         MAX_ATTEMPTS, now, now)).fetchone()
    return dict(row) if row else None


def _finish(article: dict[str, Any], status: str, error: str | None = None) -> None:
    pending = status == "waiting_ai"
    with connect() as c:
        c.execute("""UPDATE wyoming_news_articles SET status=?,last_error=?,next_attempt_at=?,
                     attempts=attempts-?,lease_token=NULL,lease_expires_at=NULL,updated_at=?
                     WHERE id=? AND lease_token=?""",
                  (status, error, _future(300 if pending else 21600) if status in ("retry", "waiting_ai") else None,
                   int(pending), iso_now(), article["id"], article["lease_token"]))


def bill_context(article: dict[str, Any]) -> list[dict[str, Any]]:
    keys = {(int(y), str(b)) for y, b in json.loads(article.get("bill_links_json") or "[]")}
    for match in VOTE_BILL.finditer(article["content_text"]):
        keys.add((int(article["published_date"][:4]), normalize_bill(match[0])))
    result = []
    with connect() as c:
        # A literal, substantial official title can identify a bill when reporting omits its number.
        titles = c.execute("SELECT year,bill_num,bill_title,catch_title FROM bills WHERE state='wy' AND year=?",
                           (int(article["published_date"][:4]),)).fetchall()
        article_text = _normalized(article["content_text"])
        for bill in titles:
            if any(len(title := _normalized(str(bill[field] or "")).rstrip(".")) >= 18 and title in article_text
                   for field in ("bill_title", "catch_title")):
                keys.add((int(bill["year"]), str(bill["bill_num"])))
        for year, number in sorted(keys)[:8]:
            bills = c.execute("""SELECT year,bill_num,special_session_value,bill_title,catch_title
                               FROM bills WHERE state='wy' AND year=? AND bill_num=?""", (year, number)).fetchall()
            for bill in bills:
                calls = list_bill_roll_calls("wy", year, number, special_session_value=bill["special_session_value"])
                for call in calls:
                    # Keep bill votes and their exact stages; never attach amendment-only statements.
                    action = str(call.get("action") or "")
                    if call.get("amendment_number") or not re.search(r"3rd|third|concur|override|do pass|passage|introduction", action, re.I):
                        continue
                    if not call.get("vote_date") or str(call["vote_date"])[:10] > article["published_date"]:
                        continue
                    members = [{"name": m["legislator_name"], "member_key": m["member_key"], "vote": m["vote_position"]}
                               for m in call.get("members", []) if m.get("vote_position") in ("yes", "no")]
                    result.append({"year": year, "bill_num": number, "title": bill["bill_title"] or bill["catch_title"],
                                   "special_session_value": bill["special_session_value"],
                                   "roll_call_key": call["roll_call_key"], "action": action,
                                   "vote_date": call["vote_date"], "members": members})
    return result


def _normalized(value: str) -> str:
    return " ".join(value.replace("\u2019", "'").replace("\u2018", "'")
                    .replace("\u201c", '"').replace("\u201d", '"').split()).casefold()


def validate_candidate(article: dict[str, Any], raw: dict[str, Any], context: list[dict[str, Any]]) -> dict[str, Any]:
    call_matches = [c for c in context if c["year"] == raw.get("year") and c["bill_num"] == raw.get("bill_num")
                    and c["roll_call_key"] == raw.get("roll_call_key")
                    and c["special_session_value"] == raw.get("special_session_value")]
    if len(call_matches) != 1:
        raise ValueError("No unique official bill, session and roll call match")
    call = call_matches[0]
    members = [m for m in call["members"] if m["member_key"] == raw.get("member_key")
               and _normalized(m["name"]) == _normalized(str(raw.get("lawmaker_name") or ""))
               and m["vote"] == raw.get("vote_position")]
    if len(members) != 1:
        raise ValueError("Lawmaker or vote does not match the official roll call")
    text = _normalized(article["content_text"])
    evidence = str(raw.get("evidence_text") or "").strip()
    attribution = str(raw.get("attribution_text") or "").strip()
    reason = str(raw.get("reason_summary") or "").strip()
    name = members[0]["name"]
    if (not evidence or not attribution or _normalized(evidence) not in text or _normalized(attribution) not in text
            or _normalized(evidence) not in _normalized(attribution)
            or not re.search(r"\b" + re.escape(name.split()[-1]) + r"\b", attribution, re.I)):
        raise ValueError("Exact evidence and named attribution must both occur in the article")
    if not re.search(r"\b(said|told|explained|wrote|because|voted|vote)\b", attribution, re.I):
        raise ValueError("Evidence lacks a statement attributed to the lawmaker")
    if not reason or len(reason) > 500 or len(evidence.split()) > 100 or len(attribution) > 3000:
        raise ValueError("Reason or internal evidence exceeds its limit")
    if raw.get("source_kind") not in ("reported_direct_quote", "reported_paraphrase"):
        raise ValueError("Unknown reported evidence class")
    return {**raw, "lawmaker_name": name, "article_id": article["id"], "article_hash": article["content_hash"],
            "state": "wy", "special_session_key": call["special_session_value"] if call["special_session_value"] is not None else -1,
            "review_status": "review_hold", "created_at": iso_now(), "updated_at": iso_now()}


def save_candidate(payload: dict[str, Any], *, connection=None) -> None:
    columns = ("article_id", "article_hash", "state", "year", "special_session_key", "special_session_value",
               "bill_num", "roll_call_key", "member_key", "lawmaker_name", "vote_position", "reason_summary",
               "evidence_text", "attribution_text", "source_kind", "review_status", "created_at", "updated_at")
    def write(c):
        c.execute(f"""INSERT INTO wyoming_news_reasons ({','.join(columns)})
                      VALUES ({','.join(':'+k for k in columns)})
                      ON CONFLICT(article_id,article_hash,year,special_session_key,bill_num,roll_call_key,member_key)
                      DO NOTHING""", {key: payload[key] for key in columns})
    if connection is not None:
        write(connection)
    else:
        with connect() as c:
            write(c)


def process_article(article: dict[str, Any], client: PublisherClient) -> int:
    if not article.get("content_hash"):
        parsed = parse_article(client.fetch(article["url"]), article["url"])
        if parsed["url"] != article["url"]:
            enqueue_article(parsed["url"])
            raise SourceHold("Canonical article queued separately", "duplicate")
        with connect() as c:
            saved = c.execute("""UPDATE wyoming_news_articles SET title=:title,published_date=:published_date,
                       content_text=:content_text,content_hash=:content_hash,bill_links_json=:bill_links_json,
                       fetched_at=:now,updated_at=:now WHERE id=:id AND lease_token=:token RETURNING id""",
                      {**parsed, "now": iso_now(), "id": article["id"], "token": article["lease_token"]}).fetchone()
            if not saved:
                raise SourceHold("Worker lease was lost", "retry")
        article.update(parsed)
    context = bill_context(article)
    if not context:
        _finish(article, "no_bill_match", "No explicit, stored bill with a prior passage vote; no inference made")
        return 0
    ollama = OllamaClient(get_settings(), queue_key=f"kls-wyoming-news-v1:{article['id']}:{article['content_hash']}")
    try:
        candidates = ollama.extract_news_vote_reasons(article=article, context=context)
    finally:
        ollama.close()
    valid = []
    rejected = 0
    for raw in candidates[:12]:
        try:
            valid.append(validate_candidate(article, raw, context))
        except (ValueError, TypeError, KeyError):
            rejected += 1
    with connect() as c:
        # Persist only while holding the same lease; all candidates start unpublished.
        claimed = c.execute("""UPDATE wyoming_news_articles SET status=?,last_error=?,lease_token=NULL,
                          lease_expires_at=NULL,updated_at=? WHERE id=? AND lease_token=? RETURNING id""",
                         ("review_hold" if valid else "quality_hold" if rejected else "no_reason_found",
                          f"{rejected} candidate(s) failed evidence validation" if rejected else None,
                          iso_now(), article["id"], article["lease_token"])).fetchone()
        if not claimed:
            raise SourceHold("Worker lease was lost", "retry")
        for item in valid:
            save_candidate(item, connection=c)
    return len(valid)


def approve_candidate(candidate_id: int, *, reviewer: str) -> None:
    if not reviewer.strip():
        raise ValueError("A reviewer identifier is required")
    with connect() as c:
        raw = c.execute("SELECT * FROM wyoming_news_reasons WHERE id=?", (candidate_id,)).fetchone()
        if not raw or raw["review_status"] not in ("review_hold", "curated"):
            raise ValueError("Candidate is missing or has already been rejected")
        article = c.execute("SELECT * FROM wyoming_news_articles WHERE id=?", (raw["article_id"],)).fetchone()
    if not article or article["content_hash"] != raw["article_hash"]:
        raise ValueError("Article changed; a new source review is required")
    normalize_url(article["url"])
    validate_candidate(dict(article), dict(raw), bill_context(dict(article)))
    with connect() as c:
        changed = c.execute("""UPDATE wyoming_news_reasons SET review_status='curated',reviewed_by=?,reviewed_at=?,updated_at=?
                         WHERE id=? AND article_hash=? AND review_status IN ('review_hold','curated')
                         AND EXISTS (SELECT 1 FROM wyoming_news_articles a WHERE a.id=article_id AND a.content_hash=article_hash)
                         AND EXISTS (SELECT 1 FROM bill_roll_call_votes v WHERE v.state=wyoming_news_reasons.state
                           AND v.year=wyoming_news_reasons.year AND v.bill_num=wyoming_news_reasons.bill_num
                           AND v.special_session_key=wyoming_news_reasons.special_session_key
                           AND v.roll_call_key=wyoming_news_reasons.roll_call_key
                           AND v.member_key=wyoming_news_reasons.member_key AND v.vote_position=wyoming_news_reasons.vote_position)""",
                         (reviewer.strip()[:150], iso_now(), iso_now(), candidate_id, raw["article_hash"])).rowcount
        if changed != 1:
            raise ValueError("Candidate changed during review; nothing published")


def status() -> dict[str, Any]:
    with connect() as c:
        return {table: [dict(r) for r in c.execute("SELECT " + column + ",count(*) AS total FROM " + table + " GROUP BY " + column).fetchall()]
                for table, column in (("wyoming_news_articles", "status"), ("wyoming_news_reasons", "review_status"))}


def main() -> None:
    parser = argparse.ArgumentParser(description="Wyoming published vote statements; all new reasons require review")
    parser.add_argument("--stage", choices=("all", "discover", "process", "status", "review"), default="all")
    parser.add_argument("--url", action="append", default=[])
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--approve", type=int)
    parser.add_argument("--reviewer")
    args = parser.parse_args()
    init_db()
    for url in args.url:
        enqueue_article(url)
    if args.approve:
        approve_candidate(args.approve, reviewer=args.reviewer or "")
    client = PublisherClient()
    try:
        if args.stage in ("all", "discover"):
            print(json.dumps({"discovery": discover(client)}), flush=True)
        if args.stage in ("all", "process"):
            for _ in range(max(0, min(args.limit, 12))):
                article = claim_article()
                if not article:
                    break
                try:
                    count = process_article(article, client)
                    print(json.dumps({"article_id": article["id"], "candidates": count}), flush=True)
                except GPUQueuePending as exc:
                    _finish(article, "waiting_ai", str(exc))
                except SourceHold as exc:
                    _finish(article, exc.status, str(exc)[:500])
                except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                    _finish(article, "retry" if article["attempts"] < MAX_ATTEMPTS else "quality_hold", str(exc)[:500])
        if args.stage == "review":
            with connect() as c:
                rows = c.execute("""SELECT r.*,a.url,a.publisher,a.title,a.published_date FROM wyoming_news_reasons r
                                    JOIN wyoming_news_articles a ON a.id=r.article_id
                                    WHERE r.review_status='review_hold' ORDER BY r.id LIMIT 50""").fetchall()
                print(json.dumps([dict(r) for r in rows]), flush=True)
        print(json.dumps(status()), flush=True)
    finally:
        client.close()


if __name__ == "__main__":
    main()
