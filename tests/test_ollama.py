import socket
from types import SimpleNamespace

from app.ollama import OllamaClient


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
