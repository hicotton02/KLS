import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from app import wyoming_news as news
from app.db import (connect, count_bill_vote_explanations, get_vote_explanation_overview,
                    list_bill_vote_explanations, list_legislator_vote_explanations,
                    list_vote_explanation_bill_keys, replace_bill_roll_calls)
from app.main import app
from app.text_utils import iso_now

URL = "https://cowboystatedaily.com/2026/03/07/a-reported-vote/"
ATTRIBUTION = 'Jane Citizen said she voted no because the bill would raise costs for local towns.'
TEXT = ATTRIBUTION + " " + "An article with sufficient background on the bill and public discussion. " * 12
HTML = '<html><head><meta property="article:published_time" content="2026-03-07T09:00:00Z"></head><body><article><h1>A reported vote</h1><p>' + TEXT + '</p><a href="https://wyoleg.gov/Legislation/2026/SF0101">Senate File 101</a></article></body></html>'


def seed():
    now = iso_now()
    with connect() as c:
        c.execute("""INSERT INTO bills(state,year,special_session_key,bill_num,bill_type,catch_title,
                     bill_title,outcome,source_synced_at,created_at,updated_at)
                     VALUES('wy',2026,-1,'SF0101','SF','Test bill','Test bill','passed',?,?,?)""", (now, now, now))
    replace_bill_roll_calls("wy", 2026, "SF0101", payloads=[{
        "roll_call_key": "third-1", "chamber": "H", "vote_date": "2026-03-05T12:00:00",
        "action": "H 3rd Reading:Passed", "vote_type": "Third Reading", "no_count": 1,
        "vote_id": "1", "amendment_number": None, "yes_count": 0, "absent_count": 0,
        "conflict_count": 0, "excused_count": 0, "source_synced_at": now, "created_at": now, "updated_at": now,
        "members": [{"member_key": "wy-123", "source_legislator_id": "123", "legislator_name": "Jane Citizen",
                     "vote_position": "no", "vote_label": "No", "party": "R", "district": "H01"}]}])
    article_id = news.enqueue_article(URL)
    parsed = news.parse_article(HTML, URL)
    with connect() as c:
        c.execute("""UPDATE wyoming_news_articles SET title=:title,published_date=:published_date,
                  content_text=:content_text,content_hash=:content_hash,bill_links_json=:bill_links_json,
                  fetched_at=:now WHERE id=:id""", {**parsed, "id": article_id, "now": now})
    return {**parsed, "id": article_id}


def candidate(article):
    raw = {"year": 2026, "bill_num": "SF0101", "special_session_value": None, "roll_call_key": "third-1",
           "member_key": "wy-123", "lawmaker_name": "Jane Citizen", "vote_position": "no",
           "reason_summary": "She said the bill would raise costs for local towns.",
           "evidence_text": "the bill would raise costs for local towns.", "attribution_text": ATTRIBUTION,
           "source_kind": "reported_paraphrase"}
    return news.validate_candidate(article, raw, news.bill_context(article))


@pytest.mark.parametrize("url", ["http://wyofile.com/a", "https://127.0.0.1/a", "https://wyofile.com.evil.test/a",
                                  "https://user:pass@wyofile.com/a", "https://wyofile.com:8443/a", "https://evil.test/a"])
def test_url_allowlist(url):
    with pytest.raises(ValueError):
        news.normalize_url(url)


def test_article_parser_requires_actual_body_and_date():
    parsed = news.parse_article(HTML, URL)
    assert parsed["published_date"] == "2026-03-07"
    assert json.loads(parsed["bill_links_json"]) == [[2026, "SF0101"]]
    assert parsed["content_hash"] == hashlib.sha256(parsed["content_text"].encode()).hexdigest()
    with pytest.raises(news.SourceHold):
        news.parse_article('<h1>Subscribe to read</h1><p>Nothing available.</p>', URL)


def test_no_cross_publisher_canonical_or_paywall():
    with pytest.raises(news.SourceHold):
        news.parse_article('<link rel="canonical" href="https://wyofile.com/other">' + HTML, URL)
    with pytest.raises(news.SourceHold, match="paywall"):
        news.parse_article('<script type="application/ld+json">{"@type":"NewsArticle","isAccessibleForFree":false}</script>' + HTML, URL)


def test_publisher_checks_robots_and_rejects_external_redirect():
    requests = []
    def serve(req):
        requests.append(str(req.url))
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private")
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/secret"})
    with httpx.Client(transport=httpx.MockTransport(serve)) as http:
        client = news.PublisherClient(client=http, sleeper=lambda _: None)
        with pytest.raises(news.SourceHold, match="robots"):
            client.fetch("https://wyofile.com/private")
        assert len(requests) == 1
        with pytest.raises(news.SourceHold, match="approved"):
            client.fetch("https://wyofile.com/public")
        assert len(requests) == 2


def test_rate_limit_opens_cooldown_without_retrying(monkeypatch):
    requests = []
    def serve(req):
        requests.append(str(req.url))
        return httpx.Response(429, headers={"Retry-After": "7200"})
    with httpx.Client(transport=httpx.MockTransport(serve)) as http:
        client = news.PublisherClient(client=http, sleeper=lambda _: None)
        with pytest.raises(news.SourceHold, match="rate limit"):
            client.fetch(URL)
        with pytest.raises(news.SourceHold, match="cooldown"):
            client.fetch(URL)
    assert len(requests) == 1
    with connect() as c:
        row = c.execute("SELECT * FROM pipeline_circuit_breakers").fetchone()
    assert row["name"] == "wyoming-news:cowboystatedaily.com"


def test_syndicated_national_articles_are_out_of_scope_not_retry_failures():
    with pytest.raises(news.SourceHold) as error:
        news.parse_article('<link rel="canonical" href="https://www.npr.org/national-news">' + HTML, URL)
    assert error.value.status == "out_of_scope"


def test_discovery_skips_people_named_bill_and_national_election_stories():
    class FakePublisher:
        def fetch(self, url):
            return '''<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
                       xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
             <url><loc>https://cowboystatedaily.com/bill-sniffin-trip/</loc><news:news><news:title>Bill Sniffin visits Iowa</news:title></news:news></url>
             <url><loc>https://cowboystatedaily.com/wyoming-bill/</loc><news:news><news:title>Wyoming lawmakers explain bill votes</news:title></news:news></url>
             <url><loc>https://www.wyomingpublicmedia.org/national-race</loc><news:news><news:title>Michigan Senate election</news:title></news:news></url>
             </urlset>'''
    news.discover(FakePublisher())
    with connect() as c:
        urls = [r["url"] for r in c.execute("SELECT url FROM wyoming_news_articles").fetchall()]
    assert urls == ["https://cowboystatedaily.com/wyoming-bill/"]


def test_news_stays_private_until_review_and_appears_on_both_public_records():
    article = seed()
    news.save_candidate(candidate(article))
    assert list_bill_vote_explanations("wy", 2026, "SF0101") == []
    with connect() as c:
        identifier = c.execute("SELECT id FROM wyoming_news_reasons").fetchone()["id"]
    news.approve_candidate(identifier, reviewer="test-reviewer")
    rows = list_bill_vote_explanations("wy", 2026, "SF0101")
    assert len(rows) == 1 and rows[0]["source_publisher"] == "Cowboy State Daily"
    assert len(list_legislator_vote_explanations("wy", "wy-123")) == 1
    assert count_bill_vote_explanations("wy", 2026, "SF0101") == 1
    assert list_vote_explanation_bill_keys("wy")[0]["explanation_count"] == 1
    assert get_vote_explanation_overview("wy")["explanation_count"] == 1
    with TestClient(app) as client:
        response = client.get("/api/v1/areas/wyoming/bills/2026/SF0101")
        assert response.status_code == 200
        assert response.json()["vote_explanations"][0]["source"]["url"] == URL
        profile = client.get("/api/v1/areas/wyoming/legislators/wy-123?year=2026")
        assert profile.status_code == 200
        assert profile.json()["published_reasons"][0]["explanation"]["source"]["url"] == URL
    # Exercise the serializer separately from bill content-quality gates.
    from app.main import _vote_explanation_json
    public = _vote_explanation_json(rows[0])
    assert public["source"]["url"] == URL
    assert public["source"]["kind"] == "article"
    assert public["source"]["start_seconds"] is None
    assert "evidence_text" not in public and "attribution_text" not in public


@pytest.mark.parametrize("field,value", [("vote_position", "yes"), ("year", 2025), ("member_key", "wy-wrong"),
                                         ("evidence_text", "Invented evidence"), ("attribution_text", "An unnamed person voted no"),
                                         ("special_session_value", 1), ("roll_call_key", "a-different-stage")])
def test_unmatched_or_fabricated_candidates_fail_closed(field, value):
    article = seed()
    raw = candidate(article)
    raw[field] = value
    with pytest.raises(ValueError):
        news.validate_candidate(article, raw, news.bill_context(article))


def test_article_revision_and_changed_votes_invalidate_publication():
    article = seed()
    news.save_candidate(candidate(article))
    with connect() as c:
        identifier = c.execute("SELECT id FROM wyoming_news_reasons").fetchone()["id"]
    news.approve_candidate(identifier, reviewer="reviewer")
    with connect() as c:
        c.execute("UPDATE bill_roll_call_votes SET vote_position='yes'")
    assert list_bill_vote_explanations("wy", 2026, "SF0101") == []
    with pytest.raises(ValueError):
        news.approve_candidate(identifier, reviewer="reviewer")
    with connect() as c:
        c.execute("UPDATE bill_roll_call_votes SET vote_position='no'")
        c.execute("UPDATE wyoming_news_articles SET content_hash='changed'")
    assert list_bill_vote_explanations("wy", 2026, "SF0101") == []
    with pytest.raises(ValueError, match="changed"):
        news.approve_candidate(identifier, reviewer="reviewer")


def test_idempotency_leases_and_waiting_ai_do_not_spend_retry_budget():
    article_id = news.enqueue_article(URL)
    assert news.enqueue_article(URL + "?tracking=1#fragment") == article_id
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: news.claim_article(), range(2)))
    claimed = [c for c in claims if c]
    assert len(claimed) == 1
    news._finish(claimed[0], "waiting_ai", "queue accepted")
    with connect() as c:
        row = dict(c.execute("SELECT * FROM wyoming_news_articles").fetchone())
        assert row["attempts"] == 0 and row["status"] == "waiting_ai"
        c.execute("UPDATE wyoming_news_articles SET status='processing',attempts=4,lease_expires_at='2000-01-01'")
    assert news.claim_article() is None
    with connect() as c:
        assert c.execute("SELECT status FROM wyoming_news_articles").fetchone()["status"] == "quality_hold"


def test_no_amendments_or_future_votes_in_context():
    article = seed()
    with connect() as c:
        c.execute("UPDATE bill_roll_calls SET amendment_number='SF0101H3001'")
    assert news.bill_context(article) == []
    with connect() as c:
        c.execute("UPDATE bill_roll_calls SET amendment_number=NULL,vote_date='2026-03-08'")
    assert news.bill_context(article) == []


def test_article_worker_saves_only_review_held_matches(monkeypatch):
    article = seed()
    raw = candidate(article)
    class FakeAI:
        def __init__(self, settings, *, queue_key):
            assert queue_key.endswith(article["content_hash"])
        def extract_news_vote_reasons(self, **kwargs):
            return [raw, {**raw, "evidence_text": "not actually said"}]
        def close(self):
            pass
    monkeypatch.setattr(news, "OllamaClient", FakeAI)
    claimed = news.claim_article()
    assert news.process_article(claimed, None) == 1
    assert list_bill_vote_explanations("wy", 2026, "SF0101") == []
    with connect() as c:
        assert c.execute("SELECT status FROM wyoming_news_articles").fetchone()["status"] == "review_hold"
        row = c.execute("SELECT * FROM wyoming_news_reasons").fetchone()
    assert row["review_status"] == "review_hold"
    news.approve_candidate(row["id"], reviewer="test-review")
    news.save_candidate(raw)
    assert count_bill_vote_explanations("wy", 2026, "SF0101") == 1


def test_lost_worker_cannot_finish_a_new_workers_claim():
    news.enqueue_article(URL)
    first = news.claim_article()
    with connect() as c:
        c.execute("UPDATE wyoming_news_articles SET lease_expires_at='2000-01-01'")
    second = news.claim_article()
    assert second["lease_token"] != first["lease_token"]
    news._finish(first, "no_reason_found")
    with connect() as c:
        assert c.execute("SELECT lease_token FROM wyoming_news_articles").fetchone()["lease_token"] == second["lease_token"]
