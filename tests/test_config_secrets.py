from stockbot import config


def test_secret_uses_environment_without_credentials_directory(monkeypatch):
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.setenv("TEST_SECRET", "env-value")

    assert config._secret("TEST_SECRET") == "env-value"


def test_secret_prefers_systemd_credential_over_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("TEST_SECRET", "env-value")
    (tmp_path / "TEST_SECRET").write_text("credential-value\n", encoding="utf-8")

    assert config._secret("TEST_SECRET") == "credential-value"


def test_missing_systemd_credential_falls_back_to_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("TEST_SECRET", "env-value")

    assert config._secret("TEST_SECRET") == "env-value"


def test_credential_read_error_does_not_include_secret_value(monkeypatch, tmp_path):
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("TEST_SECRET", "env-secret-value")
    (tmp_path / "TEST_SECRET").mkdir()

    try:
        config._secret("TEST_SECRET")
    except RuntimeError as exc:
        assert "env-secret-value" not in str(exc)
    else:
        raise AssertionError("Nicht lesbares Credential muss den Start verweigern")
