"""Unit and integration tests for StoryClip Studio Phase 1:
- Argon2id password hashing and verification
- SQLite database initialization & WAL mode
- Startup reconciliation of interrupted jobs
- HttpOnly cookie authentication and double-submit CSRF enforcement
- Single-use WebSocket ticket issuance and consumption
"""
import os
import sqlite3
import time
import unittest
from fastapi.testclient import TestClient

from services.studio_api.auth import (
    hash_password,
    verify_password,
    create_session_token,
    decode_session_token,
    issue_ws_ticket,
    consume_ws_ticket,
)
from services.studio_api.db import (
    get_db,
    init_db,
    reconcile_interrupted_jobs,
    DB_PATH,
)
from services.studio_api.server import app


class StudioSecurityTests(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_db() as conn:
            conn.execute("UPDATE render_jobs SET status = 'failed' WHERE status = 'running'")
            conn.commit()
        self.client = TestClient(app)

    def tearDown(self):
        with get_db() as conn:
            conn.execute("DELETE FROM users WHERE username = 'admin_test'")
            conn.commit()

    def test_argon2id_hashing_and_verification(self):
        password = "SuperSecretSecurePassword123!"
        hashed = hash_password(password)
        self.assertTrue(hashed.startswith("$argon2id$"))
        self.assertTrue(verify_password(password, hashed))
        self.assertFalse(verify_password("WrongPassword!", hashed))

    def test_sqlite_wal_mode_and_tables(self):
        with get_db() as conn:
            mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            self.assertEqual(mode.lower(), "wal")
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
            self.assertIn("users", tables)
            self.assertIn("render_jobs", tables)
            self.assertIn("ws_tickets", tables)

    def test_startup_reconciliation_of_interrupted_jobs(self):
        job_id = "test-orphaned-job-1"
        with get_db() as conn:
            conn.execute("DELETE FROM render_jobs WHERE id = ?", (job_id,))
            conn.execute("""
                INSERT INTO render_jobs (id, user_id, project_slug, status, progress, stage, pid, command_args)
                VALUES (?, 'test-user', 'slug', 'running', 45.0, 'Rendering', 99999999, '[]')
            """, (job_id,))
            conn.commit()

        # Run reconciler
        reconciled = reconcile_interrupted_jobs()
        self.assertGreaterEqual(reconciled, 1)

        with get_db() as conn:
            row = conn.execute("SELECT status, stage FROM render_jobs WHERE id = ?", (job_id,)).fetchone()
            self.assertEqual(row["status"], "interrupted")
            self.assertIn("Interrupted", row["stage"])

    def test_unauthenticated_request_rejected(self):
        res = self.client.post("/api/render", json={"idea": "test idea"})
        self.assertEqual(res.status_code, 401)
        self.assertIn("Authentication required", res.json().get("detail", ""))

    def test_csrf_protection_on_authenticated_post(self):
        # Ensure test operator exists in DB
        with get_db() as conn:
            user = conn.execute("SELECT id FROM users WHERE username = ?", ("admin_test",)).fetchone()
            if not user:
                conn.execute(
                    "INSERT INTO users (id, username, password_hash, role, created_at) VALUES (?, ?, ?, 'admin', ?)",
                    ("test-admin-uuid", "admin_test", hash_password("AdminPassword123!"), time.time()),
                )

        login_res = self.client.post("/api/auth/login", json={"username": "admin_test", "password": "AdminPassword123!"})
        self.assertEqual(login_res.status_code, 200)
        self.client.cookies.update(login_res.cookies)

        # Missing CSRF header on POST should return 403 Forbidden
        post_res = self.client.post("/api/render", json={"idea": "test"})
        self.assertEqual(post_res.status_code, 403)
        self.assertIn("CSRF token validation failed", post_res.json().get("detail", ""))

        # Supplying matching X-CSRF-Token allows request through
        csrf_token = login_res.cookies.get("csrf_token")
        self.assertIsNotNone(csrf_token)
        ticket_res = self.client.post("/api/auth/ws-ticket", headers={"X-CSRF-Token": csrf_token})
        self.assertEqual(ticket_res.status_code, 200)
        self.assertIn("ticket", ticket_res.json())

        # Testing POST /api/render with valid CSRF token
        from unittest.mock import patch
        with patch("services.studio_api.server.run_render_process") as mock_run:
            render_res = self.client.post(
                "/api/render",
                headers={"X-CSRF-Token": csrf_token},
                json={"idea": "test idea", "minutes": 1.0},
            )
            self.assertEqual(render_res.status_code, 200)
            self.assertEqual(render_res.json().get("status"), "started")
            self.assertIn("job_id", render_res.json())

    def test_bearer_token_authentication_supported(self):
        with get_db() as conn:
            user = conn.execute("SELECT id FROM users WHERE username = ?", ("admin_test",)).fetchone()
            if not user:
                conn.execute(
                    "INSERT INTO users (id, username, password_hash, role, created_at) VALUES (?, ?, ?, 'admin', ?)",
                    ("test-admin-uuid", "admin_test", hash_password("AdminPassword123!"), time.time()),
                )

        login_res = self.client.post("/api/auth/login", json={"username": "admin_test", "password": "AdminPassword123!"})
        token = login_res.json().get("token")
        self.assertIsNotNone(token)

        # Clear cookies on test client to ensure purely header-based auth
        self.client.cookies.clear()
        me_res = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me_res.status_code, 200)
        self.assertEqual(me_res.json()["user"]["username"], "admin_test")

        # Query token authentication for media tags
        query_res = self.client.get(f"/api/auth/me?token={token}")
        self.assertEqual(query_res.status_code, 200)
        self.assertEqual(query_res.json()["user"]["username"], "admin_test")

    def test_websocket_ticket_is_single_use(self):
        ticket = issue_ws_ticket("test-user-42")
        self.assertIsNotNone(ticket)

        user_id = consume_ws_ticket(ticket)
        self.assertEqual(user_id, "test-user-42")

        # Second consumption fails
        second_attempt = consume_ws_ticket(ticket)
        self.assertIsNone(second_attempt)


if __name__ == "__main__":
    unittest.main()
