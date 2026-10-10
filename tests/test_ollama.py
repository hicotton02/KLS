import socket
from types import SimpleNamespace

import pytest

import httpx

from app.ollama import GPUQueuePending, OllamaClient


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        return {"response": '{"ok": true}'}


class _FakeClient:
    def __init__(self) -> None:
        self.payload: dict[str, object] | None = None

    def post(self, path: str, *, json: dict[str, object]) -> _FakeResponse:
        assert path == "/api/generate"
        self.payload = json
        return _FakeResponse()


def test_expand_base_urls_uses_all_resolved_addresses(monkeypatch) -> None:
    def fake_getaddrinfo(host: str, port: int, type: int):  # noqa: ARG001
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.11", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.12", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.11", port)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    urls = OllamaClient._expand_base_urls("http://ollama-general-headless.ollama.svc.cluster.local:11434")

    assert urls == ["http://10.0.0.11:11434", "http://10.0.0.12:11434"]


def test_json_prompt_disables_model_thinking() -> None:
    transport = _FakeClient()
    client = object.__new__(OllamaClient)
    client.settings = SimpleNamespace(ollama_model="qwen3.5:27b")
    client.clients = [transport]
    client._client_index = 0

    result = client._run_json_prompt("Return JSON.", temperature=0.0, top_p=0.3, num_predict=100)

    assert result == {"ok": True}
    assert transport.payload is not None
    assert transport.payload["think"] is False
    assert transport.payload["format"] == "json"


def test_vote_explanation_prompt_has_room_for_complete_json() -> None:
    transport = _FakeClient()
    client = object.__new__(OllamaClient)
    client.settings = SimpleNamespace(ollama_model="test-model")
    client.clients = [transport]
    client._client_index = 0

    assert client.extract_vote_explanations(
        bill_num="SF0101",
        bill_title="Test bill",
        lawmakers=["Pat Example"],
        transcript="[100] I will vote no because the wording is unclear.",
    ) == []

    assert transport.payload is not None
    assert transport.payload["options"]["num_predict"] == 10000
    assert "at most one statement per lawmaker" in str(transport.payload["prompt"])
    statements = transport.payload["format"]["properties"]["statements"]
    assert statements["maxItems"] == 1
    fields = statements["items"]["properties"]
    assert fields["lawmaker_name"]["enum"] == ["Pat Example"]
    assert fields["reason_summary"]["maxLength"] == 700
    assert fields["evidence_text"]["maxLength"] == 1200


@pytest.mark.parametrize("content,done_reason", [
    ('{"statements": [{"evidence_text": "repeated', "length"),
    ('{"statements": []}', "length"),
    ('{"statements": [{"evidence_text": "repeated', "stop"),
])
def test_incomplete_explanation_response_is_not_accepted(content, done_reason) -> None:
    client = object.__new__(OllamaClient)
    client.settings = SimpleNamespace(ollama_model="test-model")
    client.clients = [SimpleNamespace(post=lambda *_args, **_kwargs: SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"response": content, "done_reason": done_reason},
    ))]
    client._client_index = 0
    with pytest.raises(ValueError):
        client.extract_vote_explanations(
            bill_num="SF0101", bill_title="Test", lawmakers=["Pat Example"],
            transcript="[100] I vote no because the wording is unclear.",
        )


@pytest.mark.parametrize('state', ['queued', 'running'])
def test_durable_summary_waits_without_blocking_and_reuses_request(state):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(202, json={'job_id': 'durable-test', 'state': state})
    client = object.__new__(OllamaClient)
    client.settings = SimpleNamespace(ollama_model='test-model')
    client.queue_key = 'summary:1:source:3'
    client.clients = [httpx.Client(base_url='http://queue', transport=httpx.MockTransport(handler))]
    client._client_index = 0
    try:
        for _ in range(2):
            with pytest.raises(GPUQueuePending):
                client._run_json_prompt('Draft.', temperature=0.1, top_p=0.9, num_predict=700)
        assert requests[0].headers['idempotency-key'] == requests[1].headers['idempotency-key']
        assert requests[0].headers['prefer'] == 'respond-async'
        assert requests[0].headers['x-customer-ref'] == 'keeping-law-simple'
        assert 'x-gpu-priority' not in requests[0].headers
        with pytest.raises(GPUQueuePending):
            client._run_json_prompt('Check.', temperature=0.0, top_p=0.3, num_predict=900)
        assert requests[2].headers['idempotency-key'] != requests[0].headers['idempotency-key']
        client.queue_key = 'summary:1:source:4'
        with pytest.raises(GPUQueuePending):
            client._run_json_prompt('Draft.', temperature=0.1, top_p=0.9, num_predict=700)
        assert requests[3].headers['idempotency-key'] != requests[0].headers['idempotency-key']
    finally:
        client.close()


def test_completed_queue_request_fetches_original_response():
    requests = []
    def handler(request):
        requests.append(request)
        if 'prefer' in request.headers:
            return httpx.Response(202, json={'job_id': 'durable-test', 'state': 'succeeded'})
        return httpx.Response(200, json={'response': '{"ok":true}'})
    client = object.__new__(OllamaClient)
    client.settings = SimpleNamespace(ollama_model='test-model')
    client.queue_key = 'summary:1:source:1'
    client.clients = [httpx.Client(base_url='http://queue', transport=httpx.MockTransport(handler))]
    client._client_index = 0
    try:
        assert client._run_json_prompt('Draft.', temperature=0.1, top_p=0.9, num_predict=700) == {'ok': True}
        assert len(requests) == 2
        assert requests[0].headers['idempotency-key'] == requests[1].headers['idempotency-key']
    finally:
        client.close()


@pytest.mark.parametrize('payload', [
    {'state': 'queued'},
    {'job_id': 'durable-test', 'state': 'failed'},
    {'job_id': 'durable-test', 'state': 'canceled'},
    {'job_id': 'durable-test', 'state': 'unknown'},
])
def test_invalid_or_terminal_queue_job_is_not_a_summary(payload):
    client = object.__new__(OllamaClient)
    client.settings = SimpleNamespace(ollama_model='test-model')
    client.queue_key = 'summary:1:source:1'
    client.clients = [httpx.Client(base_url='http://queue', transport=httpx.MockTransport(
        lambda _: httpx.Response(202, json=payload)))]
    client._client_index = 0
    try:
        with pytest.raises(RuntimeError) as error:
            client._run_json_prompt('Draft.', temperature=0.1, top_p=0.9, num_predict=700)
        assert not isinstance(error.value, GPUQueuePending)
    finally:
        client.close()
