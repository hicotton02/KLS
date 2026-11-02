"""Source-reviewed starter examples. Dry run by default; refuses changed source text."""
from __future__ import annotations

import argparse
import json

from app.db import connect
from app.text_utils import iso_now
from app.wyoming_news import (PublisherClient, approve_candidate, bill_context, enqueue_article,
                             parse_article, save_candidate, validate_candidate)


SOURCES = [
    {
        "url": "https://cowboystatedaily.com/2023/10/18/wyoming-lawmaker-accused-of-voting-against-god-avoids-party-censure/",
        "hash": "2f1b6f067e9977452908b44597f6f5f84519c682a487097d5141475e9594fa33",
        "marker": "One of his votes that received some criticism",
        "examples": [
            (2023, "HB0152", "h-5394", "wy-2117", "Forrest Chadwick", "no",
             "He said the House version did not meet constitutional requirements."),
            (2023, "HB0152", "h-5630", "wy-2117", "Forrest Chadwick", "yes",
             "He said the Senate changes addressed his constitutional concerns, so he supported the revised bill."),
        ],
    },
    {
        "url": "https://www.wyomingpublicmedia.org/open-spaces/2024-03-01/lawmakers-and-advocates-grapple-with-early-fallout-of-committee-bills-during-the-budget-session",
        "hash": "3183f715e41bd6c2a9d177bb228ece6e4883bfcbb45b77b16abb6eb2c05ff0d9",
        "marker": "Bear said he voted against the bill because",
        "examples": [
            (2024, "HB0005", "h-5014", "wy-2075", "John Bear", "no",
             "He said mental health needs should be addressed through social solutions rather than government action."),
        ],
    },
    {
        "url": "https://wyofile.com/ppp-loan-records-contradict-gops-federal-funding-stance/",
        "hash": "d63d812b6bd55e43261580dedf582c2a523ad61c55f5d7a649c0e103bfff1752",
        "marker": "Case said he voted against the ARPA bill because",
        "examples": [
            (2022, "SF0066", "s-268", "wy-945", "Cale Case", "no",
             "He said he was concerned about inflation and relying on short-term federal funding."),
        ],
    },
]


def prepare(client: PublisherClient):
    prepared = []
    for source in SOURCES:
        article = parse_article(client.fetch(source["url"]), source["url"])
        if article["url"] != source["url"] or article["content_hash"] != source["hash"]:
            raise ValueError("Source changed since review: " + source["url"])
        article["id"] = 0
        context = bill_context(article)
        evidence = next(p for p in article["content_text"].splitlines() if source["marker"] in p)
        rows = []
        for year, bill, call, member, name, vote, summary in source["examples"]:
            raw = {"year": year, "bill_num": bill, "special_session_value": None, "roll_call_key": call,
                   "member_key": member, "lawmaker_name": name, "vote_position": vote, "reason_summary": summary,
                   "evidence_text": evidence, "attribution_text": evidence, "source_kind": "reported_paraphrase"}
            rows.append(validate_candidate(article, raw, context))
        prepared.append((article, rows))
    return prepared


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    client = PublisherClient()
    try:
        prepared = prepare(client)
    finally:
        client.close()
    print(json.dumps({"sources_verified": len(prepared), "reasons_verified": sum(len(r) for _, r in prepared),
                      "apply": args.apply}), flush=True)
    if not args.apply:
        return
    for article, rows in prepared:
        article_id = enqueue_article(article["url"])
        with connect() as c:
            existing = c.execute("SELECT content_hash FROM wyoming_news_articles WHERE id=?", (article_id,)).fetchone()
            if existing["content_hash"] not in (None, article["content_hash"]):
                raise ValueError("Stored article revision differs; new review required")
            changed = c.execute("""UPDATE wyoming_news_articles SET title=:title,published_date=:published_date,
                content_text=:content_text,content_hash=:content_hash,bill_links_json=:bill_links_json,
                fetched_at=:now,updated_at=:now,status='review_hold'
                WHERE id=:id AND status <> 'processing' RETURNING id""",
                {**article, "id": article_id, "now": iso_now()}).fetchone()
            if not changed:
                raise ValueError("Article has an active worker; wait instead of overriding it")
        for row in rows:
            row["article_id"] = article_id
            save_candidate(row)
            with connect() as c:
                identifier = c.execute("""SELECT id FROM wyoming_news_reasons WHERE article_id=? AND article_hash=?
                    AND year=? AND special_session_key=? AND bill_num=? AND roll_call_key=? AND member_key=?""",
                    (article_id, row["article_hash"], row["year"], row["special_session_key"], row["bill_num"],
                     row["roll_call_key"], row["member_key"])).fetchone()["id"]
            approve_candidate(identifier, reviewer="codex-source-review-20261102")
            print(json.dumps({"published_id": identifier, "year": row["year"], "bill": row["bill_num"],
                              "name": row["lawmaker_name"], "vote": row["vote_position"], "url": article["url"]}), flush=True)


if __name__ == "__main__":
    main()
