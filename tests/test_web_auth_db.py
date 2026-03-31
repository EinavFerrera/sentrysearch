"""SQLite auth DB (users, SSO toggle)."""

import pytest


@pytest.fixture
def auth_db_path(tmp_path, monkeypatch):
    monkeypatch.setattr("sentrysearch.web.auth_db.get_data_root", lambda: tmp_path)
    from sentrysearch.web import auth_db

    auth_db.init_auth_db()
    return auth_db


def test_auth_toggle_and_users(auth_db_path):
    adb = auth_db_path
    assert adb.is_auth_enabled() is False
    adb.set_auth_enabled(True)
    assert adb.is_auth_enabled() is True
    adb.set_auth_enabled(False)
    u = adb.add_user("A@Example.com", "viewer")
    assert u["email"] == "a@example.com"
    assert u["role"] == "viewer"
    users = adb.list_users()
    assert len(users) == 1
    adb.update_user_role(u["id"], "admin")
    row = adb.get_user_by_email("a@example.com")
    assert row["role"] == "admin"
    assert adb.delete_user(u["id"]) is True
    assert adb.list_users() == []
