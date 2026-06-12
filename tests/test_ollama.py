import socket

from app.ollama import OllamaClient


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
