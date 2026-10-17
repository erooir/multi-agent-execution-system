import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient

from backend.app import auth
from backend.app.platform import api
from backend.app.storage import Store


@pytest.fixture
def identity_app(tmp_path, monkeypatch):
    database = Store(tmp_path / "identities.sqlite3")
    monkeypatch.setattr(auth, "store", database)
    monkeypatch.setenv("HANGZHI_ADMIN_PASSWORD", "synthetic-admin-password")
    app = FastAPI()
    app.include_router(api.identity)
    return app, database


def test_registration_persists_hashed_password_login_cookie_and_roles(identity_app, monkeypatch):
    app, database = identity_app
    password = "  secret password  "
    with TestClient(app) as client:
        response = client.post(
            "/api/auth/register", json={"username": "Research_1", "password": password, "name": "航空研究员"}
        )
        assert response.status_code == 201, response.text
        expected_user = {
            "id": "research_1",
            "username": "research_1",
            "name": "航空研究员",
            "role": "operator",
        }
        assert response.json() == {"user": expected_user}
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie
        assert client.get("/api/auth/me").json() == expected_user
        assert client.get("/api/users").status_code == 403
        assert client.put("/api/users/admin", json={"role": "reviewer"}).status_code == 403
        token = client.cookies.get("workbench_session")
        digest = hashlib.sha256(token.encode()).hexdigest()
        assert database.get("sessions", digest)
        assert token not in json.dumps(database.list("sessions"))
        assert client.post("/api/auth/logout").status_code == 200
        assert database.get("sessions", digest) is None
        assert client.get("/api/auth/me").status_code == 401

        # A new Store instance simulates re-opening the persisted identity database.
        monkeypatch.setattr(auth, "store", Store(database.path))
        assert (
            client.post(
                "/api/auth/login", json={"username": "research_1", "password": password.strip()}
            ).status_code
            == 401
        )
        assert client.post(
            "/api/auth/login", json={"username": "RESEARCH_1", "password": password}
        ).json() == {"user": expected_user}
        user_record = database.get("users", "research_1")
        assert user_record["password_hash"].startswith("pbkdf2_sha256$600000$")
        assert password not in json.dumps(database.list("users") + database.list("audits"))
        assert set(response.json()["user"]) == set(auth.PUBLIC_USER_FIELDS)


def test_same_password_gets_unique_salts_and_registration_cannot_elevate(identity_app):
    app, database = identity_app
    with TestClient(app) as client:
        for username in ("first_user", "second_user"):
            assert (
                client.post(
                    "/api/auth/register", json={"username": username, "password": "same-password"}
                ).status_code
                == 201
            )
        first, second = (database.get("users", name) for name in ("first_user", "second_user"))
        assert first["password_hash"] != second["password_hash"]
        assert first["name"] == "first_user"
        response = client.post(
            "/api/auth/register", json={"username": "attacker", "password": "same-password", "role": "admin"}
        )
        assert response.status_code == 422
        assert database.get("users", "attacker") is None
        assert (
            client.post(
                "/api/auth/register", json={"username": "ADMIN", "password": "same-password"}
            ).status_code
            == 409
        )
        assert database.get("users", "admin")["role"] == "admin"


@pytest.mark.parametrize(
    "changes",
    [
        {"username": "ab"},
        {"username": "a" * 33},
        {"username": "中文账号"},
        {"username": "a-b"},
        {"password": "short"},
        {"password": "x" * 129},
        {"password": 12345678},
        {"name": "x" * 41},
        {"name": "bad\nname"},
        {"name": ["name"]},
    ],
)
def test_registration_validates_parameters(identity_app, changes):
    app, database = identity_app
    with TestClient(app) as client:
        response = client.post(
            "/api/auth/register", json={"username": "new_user", "password": "password-123", **changes}
        )
        assert response.status_code == 422
        assert isinstance(response.json()["detail"], str)
        assert not database.list("users")


def test_parallel_duplicate_username_is_atomic(identity_app):
    _, database = identity_app
    auth.ensure_users()
    barrier = Barrier(2)

    def attempt(username):
        barrier.wait()
        try:
            return auth.register({"username": username, "password": "atomic-password"}, Response())["user"]
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ("Concurrent_User", "concurrent_user")))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert results.count(409) == 1
    assert len([user for user in database.list("users") if user["username"] == "concurrent_user"]) == 1


def test_legacy_identity_and_existing_session_migrate_once(identity_app, monkeypatch):
    app, database = identity_app
    legacy_token = "existing-local-session-token"
    original_hash = auth._hash_password("existing-owner-password")
    database.save(
        "users",
        {
            "id": "admin",
            "username": "admin",
            "name": "已有管理员",
            "role": "admin",
            "enabled": True,
            "password_hash": original_hash,
        },
    )
    database.save(
        "sessions",
        {
            "id": hashlib.sha256(legacy_token.encode()).hexdigest(),
            "username": "admin",
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    database.save("projects", {"id": "keep-project", "name": "已有工作"})
    with TestClient(app) as client:
        client.cookies.set("workbench_session", legacy_token)
        assert client.get("/api/auth/me").json()["role"] == "admin"
        migrated = database.get("users", "admin")
        assert migrated["password_hash"] == original_hash
        assert len(database.list("users")) == 1
        monkeypatch.setenv("HANGZHI_ADMIN_PASSWORD", "environment-change-does-not-reset-users")
        auth.ensure_users()
        assert database.get("users", "admin")["password_hash"] == migrated["password_hash"]
        assert database.get("projects", "keep-project")["name"] == "已有工作"
        assert (
            client.post(
                "/api/auth/login", json={"username": "admin", "password": "existing-owner-password"}
            ).status_code
            == 200
        )


def test_expired_deleted_disabled_and_modified_identities_are_not_stale(identity_app):
    app, database = identity_app
    with TestClient(app) as client:
        client.post("/api/auth/register", json={"username": "session_user", "password": "session-password"})
        user = database.get("users", "session_user")
        user["role"] = "reviewer"
        database.save("users", user)
        assert client.get("/api/auth/me").json()["role"] == "reviewer"
        assert (
            client.post(
                "/api/users", json={"username": "not_allowed", "password": "password-123"}
            ).status_code
            == 403
        )
        user["enabled"] = False
        database.save("users", user)
        assert client.get("/api/auth/me").status_code == 401
        assert (
            client.post(
                "/api/auth/login", json={"username": "session_user", "password": "session-password"}
            ).status_code
            == 401
        )
        user["enabled"] = True
        database.save("users", user)
        token = client.cookies.get("workbench_session")
        session = database.get("sessions", hashlib.sha256(token.encode()).hexdigest())
        session["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        database.save("sessions", session)
        assert client.get("/api/auth/me").status_code == 401
        client.post("/api/auth/login", json={"username": "session_user", "password": "session-password"})
        database.delete("users", "session_user")
        assert client.get("/api/auth/me").status_code == 401
        assert (
            client.post(
                "/api/auth/login", json={"username": "missing_user", "password": "session-password"}
            ).status_code
            == 401
        )


@pytest.mark.parametrize("endpoint", ["register", "login", "logout"])
def test_auth_endpoints_reject_cross_origin(identity_app, endpoint):
    app, database = identity_app
    with TestClient(app) as client:
        response = client.post(
            f"/api/auth/{endpoint}",
            json={"username": "new_user", "password": "password-123"},
            headers={"Origin": "https://evil.example"},
        )
        assert response.status_code == 403
        assert not database.list("sessions")
        response = client.post(
            f"/api/auth/{endpoint}",
            json={"username": "new_user", "password": "password-123"},
            headers={"Origin": "http://localhost:9999"},
        )
        assert response.status_code == 403


def test_login_and_registration_rate_limits(identity_app):
    app, database = identity_app
    with TestClient(app) as client:
        for _ in range(10):
            assert (
                client.post("/api/auth/register", json={"username": "bad", "password": "short"}).status_code
                == 422
            )
        blocked = client.post(
            "/api/auth/register", json={"username": "real_user", "password": "password-123"}
        )
        assert blocked.status_code == 429
        assert int(blocked.headers["retry-after"]) > 0
        for _ in range(10):
            assert (
                client.post("/api/auth/login", json={"username": "admin", "password": ""}).status_code == 401
            )
        assert (
            client.post(
                "/api/auth/login", json={"username": "admin", "password": "synthetic-admin-password"}
            ).status_code
            == 429
        )
        assert not database.list("sessions")


def _login(client, username="admin", password="synthetic-admin-password"):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response


def test_admin_manages_users(identity_app):
    app, database = identity_app
    with TestClient(app) as client:
        _login(client)
        users = client.get("/api/users").json()
        assert {user["username"] for user in users} == {"admin"}
        assert all("password_hash" not in user for user in users)

        created = client.post(
            "/api/users",
            json={
                "username": "Reviewer_2",
                "password": "review-password",
                "name": "审核专家乙",
                "role": "reviewer",
            },
        )
        assert created.status_code == 200, created.text
        assert created.json()["username"] == "reviewer_2"
        assert "password_hash" not in created.json()
        assert not database.list("sessions") or all(
            session.get("username") != "reviewer_2" for session in database.list("sessions")
        )

        client.post("/api/auth/logout")
        assert (
            client.post(
                "/api/auth/login", json={"username": "REVIEWER_2", "password": "review-password"}
            ).json()["user"]["role"]
            == "reviewer"
        )

        _login(client)
        assert (
            client.post(
                "/api/users", json={"username": "reviewer_2", "password": "review-password"}
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/api/users", json={"username": "root2", "password": "admin-password", "role": "admin"}
            ).status_code
            == 422
        )
        assert database.get("users", "root2") is None

        updated = client.put("/api/users/reviewer_2", json={"name": "资深审核", "role": "operator"})
        assert updated.status_code == 200
        assert updated.json()["name"] == "资深审核" and updated.json()["role"] == "operator"

        assert client.put("/api/users/admin", json={"enabled": False}).status_code == 400
        assert client.put("/api/users/admin", json={"role": "reviewer"}).status_code == 400
        assert database.get("users", "admin")["enabled"] is True

        reset = client.put("/api/users/reviewer_2", json={"password": "new-password-1"})
        assert reset.status_code == 200
        record = database.get("users", "reviewer_2")
        assert auth._verify_password("new-password-1", record["password_hash"])

        disabled = client.put("/api/users/reviewer_2", json={"enabled": False})
        assert disabled.status_code == 200 and disabled.json()["enabled"] is False
        assert (
            client.post(
                "/api/auth/login", json={"username": "reviewer_2", "password": "new-password-1"}
            ).status_code
            == 401
        )
        client.put("/api/users/reviewer_2", json={"enabled": True})
        assert (
            client.post(
                "/api/auth/login", json={"username": "reviewer_2", "password": "new-password-1"}
            ).status_code
            == 200
        )


def test_user_management_requires_admin(identity_app):
    app, database = identity_app
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/auth/register", json={"username": "ordinary", "password": "operator-password"}
            ).status_code
            == 201
        )
        assert client.get("/api/users").status_code == 403
        assert (
            client.post("/api/users", json={"username": "sneaky", "password": "sneaky-password"}).status_code
            == 403
        )
        assert client.put("/api/users/reviewer", json={"enabled": False}).status_code == 403
        assert database.get("users", "sneaky") is None


def test_disabling_user_revokes_sessions(identity_app):
    app, database = identity_app
    with TestClient(app) as client:
        _login(client)
        client.post("/api/users", json={"username": "temp_user", "password": "temp-password"})
        client.post("/api/auth/logout")
        _login(client, "temp_user", "temp-password")
        assert client.get("/api/auth/me").status_code == 200
    with TestClient(app) as admin_client:
        _login(admin_client)
        admin_client.put("/api/users/temp_user", json={"enabled": False})
        assert not [
            session for session in database.list("sessions") if session.get("username") == "temp_user"
        ]


def test_fresh_bootstrap_random_secret_is_bound_to_identity_store(identity_app, monkeypatch):
    _, database = identity_app
    monkeypatch.delenv("HANGZHI_ADMIN_PASSWORD")
    credential_file = database.path.parent / "bootstrap-admin.json"
    auth.ensure_users()
    credentials = json.loads(credential_file.read_text(encoding="utf-8"))
    account = database.get("users", "admin")
    assert auth._verify_password(credentials["password"], account["password_hash"])
    assert len(credentials["password"]) >= 24
    assert len(database.list("users")) == 1
    assert not auth._verify_password("demo12345", account["password_hash"])
    auth.ensure_users()
    assert database.get("users", "admin")["password_hash"] == account["password_hash"]
    if os.name != "nt":
        assert credential_file.stat().st_mode & 0o777 == 0o600


def test_interrupted_bootstrap_reuses_saved_credentials_and_revokes_orphans(identity_app, monkeypatch):
    _, database = identity_app
    monkeypatch.delenv("HANGZHI_ADMIN_PASSWORD")
    credential_file = database.path.parent / "bootstrap-admin.json"
    credential_file.write_text(
        json.dumps({"username": "admin", "password": "interrupted-setup-password"}), encoding="utf-8"
    )
    database.save(
        "sessions",
        {
            "id": "orphan",
            "username": "admin",
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    auth.ensure_users()
    assert auth._verify_password(
        "interrupted-setup-password", database.get("users", "admin")["password_hash"]
    )
    assert database.get("sessions", "orphan") is None


def test_invalid_bootstrap_file_fails_without_creating_accounts(identity_app, monkeypatch):
    _, database = identity_app
    monkeypatch.delenv("HANGZHI_ADMIN_PASSWORD")
    (database.path.parent / "bootstrap-admin.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(RuntimeError, match="bootstrap_credentials_invalid"):
        auth.ensure_users()
    assert not database.list("users")
    assert not database.get("auth_meta", "persisted_users_v1")


def test_bootstrap_is_atomic_across_processes(tmp_path):
    directory = tmp_path / "private-runtime"
    environment = dict(os.environ)
    environment.pop("HANGZHI_ADMIN_PASSWORD", None)
    environment["WORKBENCH_DATA_DIR"] = str(directory)
    command = [sys.executable, "-c", "from backend.app.auth import ensure_users; ensure_users()"]
    processes = [
        subprocess.Popen(command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(3)
    ]
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr.decode(errors="replace")
        assert not stdout
    database = Store(directory / "workbench.sqlite3")
    credentials = json.loads((directory / "bootstrap-admin.json").read_text(encoding="utf-8"))
    assert len(database.list("users")) == 1
    assert auth._verify_password(credentials["password"], database.get("users", "admin")["password_hash"])


def test_password_change_prevents_stale_login_session(identity_app):
    _, database = identity_app
    auth.ensure_users()
    actor = database.get("users", "admin")
    auth.create_user({"username": "concurrent", "password": "before-password"}, actor)
    stale = database.get("users", "concurrent")
    auth.update_user("concurrent", {"password": "after-password"}, actor)
    with pytest.raises(HTTPException) as error:
        auth._start_session(stale, Response())
    assert error.value.status_code == 401
    assert not database.list("sessions")


def test_concurrent_administrators_cannot_disable_every_admin(identity_app):
    _, database = identity_app
    auth.ensure_users()
    first = database.get("users", "admin")
    auth.create_user({"username": "second_admin", "password": "second-admin-password"}, first)
    second = database.get("users", "second_admin")
    second["role"] = "admin"
    database.save("users", second)
    barrier = Barrier(2)

    def disable(pair):
        actor, target = pair
        barrier.wait()
        try:
            return auth.update_user(target["id"], {"enabled": False}, actor)
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(disable, [(first, second), (second, first)]))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert len([user for user in database.list("users") if user["role"] == "admin" and user["enabled"]]) == 1
