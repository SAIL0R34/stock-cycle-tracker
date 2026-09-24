"""Tests for the Settings-UI secrets store and credential resolution."""

from unittest.mock import patch

from stock_cycle_tracker.data.alpaca_client import AlpacaHTTPClient
from stock_cycle_tracker.web.secrets_store import SecretsStore


def _store(tmp_path):
    return SecretsStore(tmp_path / "app_secrets.json")


def test_store_roundtrip_and_blank_never_wipes(tmp_path):
    store = _store(tmp_path)
    store.update({"alpaca_api_key_id": "PKABC1234", "alpaca_api_secret_key": "SECRETXYZ"})
    assert store.get("alpaca_api_key_id") == "PKABC1234"

    # A blank field (the masked UI input) must not erase the saved value.
    store.update({"alpaca_api_key_id": "", "alpaca_api_secret_key": ""})
    assert store.get("alpaca_api_key_id") == "PKABC1234"

    # Re-entry overwrites.
    store.update({"alpaca_api_key_id": "PKNEW9999"})
    assert store.get("alpaca_api_key_id") == "PKNEW9999"


def test_store_rejects_unknown_fields(tmp_path):
    store = _store(tmp_path)
    store.update({"evil_field": "x", "llm_base_url": "http://x/v1"})
    assert store.get("evil_field") == ""
    assert store.get("llm_base_url") == "http://x/v1"


def test_status_masks_secrets_but_never_returns_them(tmp_path):
    store = _store(tmp_path)
    store.update({"alpaca_api_key_id": "PKABCDWXYZ12", "alpaca_api_secret_key": "TOPSECRET9999"})
    status = store.status()
    assert status["alpaca"]["configured"] is True
    assert status["alpaca"]["key_id_hint"].endswith("Z12")
    import json
    blob = json.dumps(status)
    assert "TOPSECRET9999" not in blob
    assert "PKABCDWXYZ12" not in blob


def test_store_corrupt_file_starts_clean(tmp_path):
    path = tmp_path / "app_secrets.json"
    path.write_text("{nope")
    store = SecretsStore(path)
    assert store.status()["alpaca"]["configured"] is False


def test_client_resolves_credentials_from_store(tmp_path, monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    store = _store(tmp_path)
    store.update({"alpaca_api_key_id": "STOREKEY1", "alpaca_api_secret_key": "STORESECRET1"})

    with patch("stock_cycle_tracker.data.alpaca_client.secrets_store", store) if False else patch(
        "stock_cycle_tracker.web.secrets_store.secrets_store", store
    ):
        client = AlpacaHTTPClient()
        assert client.has_credentials
        assert client.api_key_id == "STOREKEY1"


def test_client_env_beats_store(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "ENVKEY")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "ENVSECRET")
    store = _store(tmp_path)
    store.update({"alpaca_api_key_id": "STOREKEY2", "alpaca_api_secret_key": "S2"})

    with patch("stock_cycle_tracker.web.secrets_store.secrets_store", store):
        client = AlpacaHTTPClient()
        assert client.api_key_id == "ENVKEY"


def test_client_explicit_args_beat_everything(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "ENVKEY")
    store = _store(tmp_path)
    store.update({"alpaca_api_key_id": "STOREKEY3", "alpaca_api_secret_key": "S3"})

    with patch("stock_cycle_tracker.web.secrets_store.secrets_store", store):
        client = AlpacaHTTPClient(api_key_id="EXPLICIT", api_secret_key="EXPLICITSECRET")
        assert client.api_key_id == "EXPLICIT"
        # re-resolution keeps explicit args on top
        client._resolve_credentials()
        assert client.api_key_id == "EXPLICIT"


def test_new_keys_apply_to_existing_client_without_restart(tmp_path, monkeypatch):
    """The whole point of the overlay: an already-built client picks up
    newly stored credentials on its next request."""
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    store = _store(tmp_path)

    with patch("stock_cycle_tracker.web.secrets_store.secrets_store", store):
        client = AlpacaHTTPClient()
        assert not client.has_credentials
        store.update({"alpaca_api_key_id": "LATEKEY", "alpaca_api_secret_key": "LATESECRET"})
        client._resolve_credentials()  # called at the top of every _request
        assert client.has_credentials
        assert client.api_key_id == "LATEKEY"


def test_llm_store_override_beats_import_default(tmp_path):
    from stock_cycle_tracker.web.llm import LLMClient

    store = _store(tmp_path)
    store.update({"llm_base_url": "http://10.0.0.9:9999/v1", "llm_model": "my-model"})
    with patch("stock_cycle_tracker.web.secrets_store.secrets_store", store):
        client = LLMClient()
        base, model = client._effective()
        assert base == "http://10.0.0.9:9999/v1"
        assert model == "my-model"
