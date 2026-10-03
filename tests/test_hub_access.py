"""Тесты модуля integrations/hub_access.py против маленького фейкового ORBIT.

Запуск:  python -m unittest discover -s tests -t . -v
"""

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "integrations"))
from hub_access import HubAccess  # noqa: E402

KEY = "velora-secret-key-123456"
ACTIVE_USERS = {42}


class FakeOrbit(BaseHTTPRequestHandler):
    calls = 0

    def do_GET(self):  # noqa: N802
        FakeOrbit.calls += 1
        if self.headers.get("Authorization") != f"Bearer {KEY}":
            self.send_response(401)
            self.end_headers()
            return
        q = parse_qs(urlparse(self.path).query)
        user_id = int(q["user_id"][0])
        body = {
            "user_id": user_id,
            "app": q["app"][0],
            "active": user_id in ACTIVE_USERS,
            "plan": "all" if user_id in ACTIVE_USERS else None,
            "expires_at": "2026-11-02T12:00:00+00:00" if user_id in ACTIVE_USERS else None,
            "source": "bundle" if user_id in ACTIVE_USERS else None,
        }
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class HubAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOrbit)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def client(self, **kw):
        return HubAccess(self.url, "velora", kw.pop("key", KEY), bot_link="https://t.me/orbit_hub_bot", **kw)

    def test_active_and_inactive(self):
        hub = self.client()
        ok = hub.check(42)
        self.assertTrue(ok.active)
        self.assertEqual(ok.source, "bundle")
        self.assertFalse(hub.check(7).active)

    def test_positive_answer_is_cached(self):
        hub = self.client()
        hub.check(42)
        before = FakeOrbit.calls
        hub.check(42)
        self.assertEqual(FakeOrbit.calls, before)

    def test_wrong_key_explains(self):
        result = self.client(key="wrong-key-0000000000").check(42)
        self.assertFalse(result.active)
        self.assertIn("HUB_API_KEY", result.error)

    def test_hub_down(self):
        down = HubAccess("http://127.0.0.1:1", "velora", KEY, timeout=0.5)
        self.assertFalse(down.check(42).active)
        self.assertTrue(HubAccess("http://127.0.0.1:1", "velora", KEY, timeout=0.5, fail_open=True).check(42).active)

    def test_not_configured(self):
        self.assertFalse(HubAccess("", "velora", "").check(42).active)

    def test_async(self):
        import asyncio

        self.assertTrue(asyncio.run(self.client().acheck(42)).active)

    def test_paywall_link(self):
        self.assertEqual(self.client().paywall_link(), "https://t.me/orbit_hub_bot?start=buy_velora")


if __name__ == "__main__":
    unittest.main()
