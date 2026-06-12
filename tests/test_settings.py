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
