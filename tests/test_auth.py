import pytest

from backend import auth


def test_password_hash_and_verify_round_trip():
    hashed = auth.hash_password("correct horse battery staple")
    assert auth.verify_password("correct horse battery staple", hashed) is True
    assert auth.verify_password("wrong password", hashed) is False


def test_password_hash_is_not_plaintext():
    hashed = auth.hash_password("hunter2")
    assert hashed != "hunter2"


def test_create_and_decode_access_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    token = auth.create_access_token("user-123")
    assert auth.decode_access_token(token) == "user-123"


def test_decode_rejects_garbage_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    assert auth.decode_access_token("not-a-real-token") is None


def test_decode_rejects_token_signed_with_different_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SECRET_KEY", "secret-a")
    token = auth.create_access_token("user-123")

    monkeypatch.setenv("SECRET_KEY", "secret-b")
    assert auth.decode_access_token(token) is None


def test_user_id_from_header_variants(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    token = auth.create_access_token("user-456")

    assert auth.user_id_from_header(f"Bearer {token}") == "user-456"
    assert auth.user_id_from_header(None) is None
    assert auth.user_id_from_header("") is None
    assert auth.user_id_from_header("Bearer ") is None
    assert auth.user_id_from_header("NotBearer sometoken") is None
    assert auth.user_id_from_header("Bearer garbage-token") is None


def test_is_admin_email_matches_on_email_case_insensitively():
    assert auth.is_admin_email(None, "Admin@Example.com", "admin@example.com") is True
    assert auth.is_admin_email(None, "admin@example.com", "admin@example.com") is True


def test_is_admin_email_matches_on_username_case_insensitively():
    # A password-signup account has no required email field, so someone who
    # types their email into the username box instead is still recognized.
    assert auth.is_admin_email("Admin@Example.com", None, "admin@example.com") is True
    assert auth.is_admin_email("admin@example.com", "someone-else@example.com", "admin@example.com") is True


def test_is_admin_email_rejects_when_neither_matches():
    assert auth.is_admin_email("someone-else", "someone-else@example.com", "admin@example.com") is False


def test_is_admin_email_rejects_when_admin_email_unset():
    assert auth.is_admin_email("admin@example.com", "admin@example.com", None) is False
    assert auth.is_admin_email(None, None, None) is False
