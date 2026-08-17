import pytest

from backend import google_oauth


@pytest.fixture(autouse=True)
def _client_id(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    google_oauth._pending_states.clear()


def test_authorize_url_contains_client_id_and_state():
    url = google_oauth.build_authorize_url()
    assert "client_id=test-client-id" in url
    assert "state=" in url
    assert "scope=openid" in url


def test_consume_state_accepts_issued_state_once():
    url = google_oauth.build_authorize_url()
    state = url.split("state=")[1].split("&")[0]

    assert google_oauth.consume_state(state) is True
    assert google_oauth.consume_state(state) is False  # single use


def test_consume_state_rejects_unknown_state():
    assert google_oauth.consume_state("never-issued") is False
