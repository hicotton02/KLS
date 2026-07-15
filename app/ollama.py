from __future__ import annotations

import json
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

from app.settings import Settings
from app.text_utils import truncate_for_prompt


class OllamaClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        base_urls = self._expand_base_urls(self.settings.ollama_base_url)
        self.clients = [
            httpx.Client(
                base_url=base_url,
                headers={"Content-Type": "application/json"},
                timeout=self.settings.ollama_timeout_seconds,
            )
            for base_url in base_urls
        ]
        self._client_index = 0

    def close(self) -> None:
        for client in self.clients:
            client.close()

    def generate_interpretation(
        self,
        bill: dict[str, Any],
        status_info: dict[str, str],
        official_summary_text: str,
        official_digest_text: str,
        current_bill_text: str,
    ) -> dict[str, Any]:
        prompt = self._build_prompt(
            bill=bill,
            status_info=status_info,
            official_summary_text=official_summary_text,
            official_digest_text=official_digest_text,
            current_bill_text=current_bill_text,
        )
        parsed = self._run_json_prompt(prompt, temperature=0.1, top_p=0.9, num_predict=700)
        return self._normalize(parsed)

    def fact_check_interpretation(
        self,
        bill: dict[str, Any],
        status_info: dict[str, str],
        official_summary_text: str,
        official_digest_text: str,
        current_bill_text: str,
        candidate_interpretation: dict[str, Any],
    ) -> dict[str, Any]:
        prompt = self._build_fact_check_prompt(
            bill=bill,
            status_info=status_info,
            official_summary_text=official_summary_text,
            official_digest_text=official_digest_text,
            current_bill_text=current_bill_text,
            candidate_interpretation=candidate_interpretation,
        )
        parsed = self._run_json_prompt(prompt, temperature=0.0, top_p=0.3, num_predict=900)
        return self._normalize_fact_check(parsed)

    def analyze_bill_relationship(
        self,
        bill_a: dict[str, Any],
        bill_b: dict[str, Any],
        heuristic_reasons: tuple[str, ...],
    ) -> dict[str, Any]:
        prompt = self._build_relationship_prompt(
            bill_a=bill_a,
            bill_b=bill_b,
            heuristic_reasons=heuristic_reasons,
        )
        parsed = self._run_json_prompt(prompt, temperature=0.0, top_p=0.4, num_predict=900)
        return self._normalize_relationship(parsed)

    def summarize_amendment(
        self,
        bill: dict[str, Any],
        amendment: dict[str, Any],
        amendment_text: str,
    ) -> dict[str, Any]:
        prompt = self._build_amendment_prompt(
            bill=bill,
            amendment=amendment,
            amendment_text=amendment_text,
        )
        parsed = self._run_json_prompt(prompt, temperature=0.0, top_p=0.3, num_predict=500)
        return self._normalize_amendment(parsed)

    def _run_json_prompt(
        self,
        prompt: str,
        *,
        temperature: float,
        top_p: float,
        num_predict: int,
    ) -> dict[str, Any]:
        response = self._next_client().post(
            "/api/generate",
            json={
                "model": self.settings.ollama_model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "think": False,
                "options": {
                    "temperature": temperature,
                    "top_p": top_p,
                    "num_predict": num_predict,
                },
            },
        )
        response.raise_for_status()
        payload = response.json()
        content = payload.get("response", "").strip()
        return json.loads(content)

    def _next_client(self) -> httpx.Client:
        client = self.clients[self._client_index % len(self.clients)]
        self._client_index += 1
        return client

    @staticmethod
    def _expand_base_urls(base_url: str) -> list[str]:
        parsed = urlparse(base_url)
        if not parsed.scheme or not parsed.hostname or not parsed.port:
            return [base_url]

        try:
            candidates: list[str] = []
            seen: set[str] = set()
            for _, _, _, _, sockaddr in socket.getaddrinfo(parsed.hostname, parsed.port, type=socket.SOCK_STREAM):
                host = sockaddr[0]
                if ":" in host and not host.startswith("["):
                    host = f"[{host}]"
                expanded = f"{parsed.scheme}://{host}:{parsed.port}"
                if parsed.path:
                    expanded = f"{expanded}{parsed.path}"
                if expanded not in seen:
                    seen.add(expanded)
                    candidates.append(expanded)
            return candidates or [base_url]
        except socket.gaierror:
            return [base_url]

    def _build_prompt(
        self,
        bill: dict[str, Any],
        status_info: dict[str, str],
        official_summary_text: str,
        official_digest_text: str,
        current_bill_text: str,
    ) -> str:
        current_excerpt = truncate_for_prompt(current_bill_text, 9000)
        summary_excerpt = truncate_for_prompt(official_summary_text, 6000)
        digest_excerpt = truncate_for_prompt(official_digest_text, 6000)
        return f"""
You rewrite legislation into plain English for U.S. grade 6 to 8 readers.

Rules:
- Use only the official source material provided below.
- Stay neutral. Do not persuade, praise, criticize, predict motives, or suggest how anyone should vote.
- If the source does not clearly say something, do not add it.
- Use short sentences and concrete words.
- Prefer neutral verbs like says, requires, allows, creates, changes, removes, funds, or limits.
- Do not use partisan labels or emotional wording.
- Keep every bullet factual and readable.

Return strict JSON with this exact shape:
{{
  "plain_language_title": "string",
  "one_sentence_summary": "string",
  "what_it_does": ["string"],
  "who_it_affects": ["string"],
  "terms_to_know": [
    {{
      "term": "string",
      "meaning": "string"
    }}
  ],
  "limits_and_unknowns": ["string"]
}}

Output rules:
- "what_it_does": 3 to 6 bullets.
- "who_it_affects": 1 to 4 bullets.
- "terms_to_know": 0 to 4 items.
- "limits_and_unknowns": 1 to 3 bullets.
- If the bill text does not clearly name who is affected, describe the people or agencies named in the source rather than guessing.

Official bill metadata:
- Bill number: {bill.get("bill")}
- Catch title: {bill.get("catchTitle")}
- Bill title: {bill.get("billTitle")}
- Sponsor: {bill.get("sponsor")}
- Official status label: {status_info.get("label")}
- Official status explanation: {status_info.get("explanation")}
- Last action: {bill.get("lastAction")}
- Last action date: {bill.get("lastActionDate")}
- Effective date: {bill.get("effectiveDate")}

Official bill summary text:
{summary_excerpt or "[not provided]"}

Official digest text:
{digest_excerpt or "[not provided]"}

Official bill text excerpt:
{current_excerpt or "[not provided]"}
""".strip()

    def _build_fact_check_prompt(
        self,
        bill: dict[str, Any],
        status_info: dict[str, str],
        official_summary_text: str,
        official_digest_text: str,
        current_bill_text: str,
        candidate_interpretation: dict[str, Any],
    ) -> str:
        current_excerpt = truncate_for_prompt(current_bill_text, 9000)
        summary_excerpt = truncate_for_prompt(official_summary_text, 6000)
        digest_excerpt = truncate_for_prompt(official_digest_text, 6000)
        candidate_excerpt = truncate_for_prompt(json.dumps(candidate_interpretation, indent=2, ensure_ascii=True), 5000)
        return f"""
You fact-check a plain-English legislation explanation against official source material.

Rules:
- Use only the official source material provided below.
- Keep only statements that are supported by the official material.
- If a candidate statement is partly supported, rewrite it to match only the supported part.
- Remove any unsupported, speculative, exaggerated, causal, or motive-based claim.
- Stay neutral and readable for U.S. grade 6 to 8 readers.
- If the official material does not support a section, return an empty string or empty list for that section.
- Prefer removing content over guessing.

Return strict JSON with this exact shape:
{{
  "plain_language_title": "string",
  "one_sentence_summary": "string",
  "what_it_does": ["string"],
  "who_it_affects": ["string"],
  "terms_to_know": [
    {{
      "term": "string",
      "meaning": "string"
    }}
  ],
  "limits_and_unknowns": ["string"],
  "removed_claims": ["string"],
  "validator_notes": ["string"]
}}

Output rules:
- Keep the same overall subject as the candidate explanation.
- "removed_claims" should briefly list items you removed or materially narrowed.
- "validator_notes" should note any remaining uncertainty that the source itself leaves open.
- Do not include text outside JSON.

Official bill metadata:
- Bill number: {bill.get("bill")}
- Catch title: {bill.get("catchTitle")}
- Bill title: {bill.get("billTitle")}
- Sponsor: {bill.get("sponsor")}
- Official status label: {status_info.get("label")}
- Official status explanation: {status_info.get("explanation")}
- Last action: {bill.get("lastAction")}
- Last action date: {bill.get("lastActionDate")}
- Effective date: {bill.get("effectiveDate")}

Candidate explanation JSON:
{candidate_excerpt}

Official bill summary text:
{summary_excerpt or "[not provided]"}

Official digest text:
{digest_excerpt or "[not provided]"}

Official bill text excerpt:
{current_excerpt or "[not provided]"}
""".strip()

    def _build_relationship_prompt(
        self,
        bill_a: dict[str, Any],
        bill_b: dict[str, Any],
        heuristic_reasons: tuple[str, ...],
    ) -> str:
        return f"""
You review two bills from the same legislative session and decide whether regular readers should look at them together.

Rules:
- Use only the official bill material shown below.
- Be cautious. Two bills with the same broad topic are not enough by themselves.
- Mark a relationship as material only when the bills appear to interact, reinforce each other, or create a bigger practical effect when read together.
- Do not use partisan labels or advocacy language.
- Do not say a bill is "anti" or "pro" anything unless the official text itself says that plainly.
- Describe concrete combined effects, such as one bill creating rights while another adds enforcement, one bill changing definitions while another adds penalties, or two bills changing the same policy area in ways that stack together.
- If the relationship is weak, speculative, or only topical, set is_material_relationship to false.

Return strict JSON with this exact shape:
{{
  "is_material_relationship": true,
  "relationship_type": "shared-topic|definition-plus-enforcement|funding-plus-policy|procedure-plus-penalty|rights-plus-enforcement|other|none",
  "relationship_strength": "low|medium|high",
  "needs_human_review": true,
  "pair_summary": "string",
  "combined_effect": "string",
  "bill_a_evidence": ["string"],
  "bill_b_evidence": ["string"],
  "why_review": "string",
  "limits_and_unknowns": ["string"]
}}

Output rules:
- If there is no meaningful relationship, return:
  {{
    "is_material_relationship": false,
    "relationship_type": "none",
    "relationship_strength": "low",
    "needs_human_review": false,
    "pair_summary": "",
    "combined_effect": "",
    "bill_a_evidence": [],
    "bill_b_evidence": [],
    "why_review": "",
    "limits_and_unknowns": []
  }}
- If there is a material relationship, include at least one concrete evidence bullet from each bill.
- Keep the wording short, factual, and plain English.
- "why_review" should explain why a reader should look at both bills together.

Heuristic overlap hints:
{chr(10).join(f"- {item}" for item in heuristic_reasons) or "- None"}

Bill A:
Bill number: {bill_a.get("bill_num")}
Catch title: {bill_a.get("catch_title")}
Bill title: {bill_a.get("bill_title")}
Sponsor: {bill_a.get("sponsor")}
Outcome: {bill_a.get("status_label") or bill_a.get("outcome")}
Issue tags: {", ".join(sorted(bill_a.get("topic_tags", []))) or "[none found]"}
Rule tags: {", ".join(sorted(bill_a.get("action_tags", []))) or "[none found]"}
Official summary excerpt:
{truncate_for_prompt(self._relationship_source_text(bill_a), 2200)}

Bill B:
Bill number: {bill_b.get("bill_num")}
Catch title: {bill_b.get("catch_title")}
Bill title: {bill_b.get("bill_title")}
Sponsor: {bill_b.get("sponsor")}
Outcome: {bill_b.get("status_label") or bill_b.get("outcome")}
Issue tags: {", ".join(sorted(bill_b.get("topic_tags", []))) or "[none found]"}
Rule tags: {", ".join(sorted(bill_b.get("action_tags", []))) or "[none found]"}
Official summary excerpt:
{truncate_for_prompt(self._relationship_source_text(bill_b), 2200)}
""".strip()

    def _build_amendment_prompt(
        self,
        bill: dict[str, Any],
        amendment: dict[str, Any],
        amendment_text: str,
    ) -> str:
        return f"""
You explain an official bill amendment in plain English.

Rules:
- Use only the official amendment material provided below.
- Stay neutral and factual.
- Do not guess what the amendment does if the text is unclear.
- Keep the wording easy to read for U.S. grade 6 to 8 readers.
- Focus on the concrete change the amendment would make.

Return strict JSON with this exact shape:
{{
  "one_sentence_summary": "string",
  "changes": ["string"],
  "limits_and_unknowns": ["string"]
}}

Output rules:
- "changes": 1 to 4 bullets.
- If the amendment text is too technical or incomplete to explain fully, say that in "limits_and_unknowns".
- Do not include text outside JSON.

Bill metadata:
- Bill number: {bill.get("bill")}
- Catch title: {bill.get("catchTitle")}
- Bill title: {bill.get("billTitle")}

Amendment metadata:
- Amendment number: {amendment.get("amendmentNumber")}
- Chamber: {amendment.get("house")}
- Stage: {amendment.get("order")}
- Status: {amendment.get("status")}
- Sponsor: {amendment.get("sponsor")}

Official amendment text:
{truncate_for_prompt(amendment_text, 7000) or "[not provided]"}
""".strip()

    @staticmethod
    def _relationship_source_text(bill: dict[str, Any]) -> str:
        for key in ("summary_text", "digest_text", "current_text", "bill_title"):
            value = str(bill.get(key) or "").strip()
            if value:
                return value
        return "[not provided]"

    @staticmethod
    def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "plain_language_title": str(payload.get("plain_language_title", "")).strip(),
            "one_sentence_summary": str(payload.get("one_sentence_summary", "")).strip(),
            "what_it_does": [str(item).strip() for item in payload.get("what_it_does", []) if str(item).strip()],
            "who_it_affects": [str(item).strip() for item in payload.get("who_it_affects", []) if str(item).strip()],
            "terms_to_know": [
                {
                    "term": str(item.get("term", "")).strip(),
                    "meaning": str(item.get("meaning", "")).strip(),
                }
                for item in payload.get("terms_to_know", [])
                if str(item.get("term", "")).strip() and str(item.get("meaning", "")).strip()
            ],
            "limits_and_unknowns": [
                str(item).strip() for item in payload.get("limits_and_unknowns", []) if str(item).strip()
            ],
        }

    @staticmethod
    def _normalize_fact_check(payload: dict[str, Any]) -> dict[str, Any]:
        normalized = OllamaClient._normalize(payload)
        normalized["removed_claims"] = [
            str(item).strip() for item in payload.get("removed_claims", []) if str(item).strip()
        ]
        normalized["validator_notes"] = [
            str(item).strip() for item in payload.get("validator_notes", []) if str(item).strip()
        ]
        return normalized

    @staticmethod
    def _normalize_relationship(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "is_material_relationship": bool(payload.get("is_material_relationship")),
            "relationship_type": str(payload.get("relationship_type", "none")).strip().lower(),
            "relationship_strength": str(payload.get("relationship_strength", "low")).strip().lower(),
            "needs_human_review": bool(payload.get("needs_human_review", True)),
            "pair_summary": str(payload.get("pair_summary", "")).strip(),
            "combined_effect": str(payload.get("combined_effect", "")).strip(),
            "bill_a_evidence": [str(item).strip() for item in payload.get("bill_a_evidence", []) if str(item).strip()],
            "bill_b_evidence": [str(item).strip() for item in payload.get("bill_b_evidence", []) if str(item).strip()],
            "why_review": str(payload.get("why_review", "")).strip(),
            "limits_and_unknowns": [
                str(item).strip() for item in payload.get("limits_and_unknowns", []) if str(item).strip()
            ],
        }

    @staticmethod
    def _normalize_amendment(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "one_sentence_summary": str(payload.get("one_sentence_summary", "")).strip(),
            "changes": [str(item).strip() for item in payload.get("changes", []) if str(item).strip()],
            "limits_and_unknowns": [
                str(item).strip() for item in payload.get("limits_and_unknowns", []) if str(item).strip()
            ],
        }
