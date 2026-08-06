from app.settings import get_settings


def test_settings_support_qa_environment_flags(monkeypatch) -> None:
    monkeypatch.setenv("KLS_ENVIRONMENT_NAME", "qa")
    monkeypatch.setenv("KLS_ENVIRONMENT_LABEL", "QA")
    monkeypatch.setenv("KLS_ALLOW_INDEXING", "0")
    monkeypatch.setenv("KLS_GOOGLE_ANALYTICS_ID", "")
    get_settings.cache_clear()

    try:
        settings = get_settings()
        assert settings.environment_name == "qa"
        assert settings.environment_label == "QA"
        assert settings.allow_indexing is False
        assert settings.google_analytics_id == ""
    finally:
        get_settings.cache_clear()


def test_transcription_throughput_settings_are_bounded(monkeypatch) -> None:
    monkeypatch.setenv("KLS_TRANSCRIPTION_CHUNK_CONCURRENCY", "99")
    monkeypatch.setenv("KLS_YOUTUBE_CAPTION_COOLDOWN_SECONDS", "1")
    get_settings.cache_clear()

    try:
        settings = get_settings()
        assert settings.transcription_chunk_concurrency == 16
        assert settings.youtube_caption_cooldown_seconds == 60
    finally:
        get_settings.cache_clear()
