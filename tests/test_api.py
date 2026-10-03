"""Тесты сервера целиком: API + база (SQLite) + заглушка Telegram.

Им нужны библиотеки из requirements.txt, поэтому, если они не установлены,
тесты пропускаются. Запуск у себя:

    pip install -r requirements.txt
    python -m unittest discover -s tests -t . -v
"""

import asyncio
import importlib.util
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.test_logic import TOKEN, make_init_data

HAVE_DEPS = all(
    importlib.util.find_spec(name) for name in ("fastapi", "aiogram", "sqlalchemy", "aiosqlite", "httpx")
)

APP_KEY = "velora-secret-key-123456"


class FakeBot:
    def __init__(self):
        self.invoices = []
        self.edits = []

    async def create_invoice_link(self, **kwargs):
        self.invoices.append(kwargs)
        return "https://t.me/$fake_invoice"

    async def edit_user_star_subscription(self, **kwargs):
        self.edits.append(kwargs)
        return True


@unittest.skipUnless(HAVE_DEPS, "нет fastapi/aiogram/sqlalchemy — выполни pip install -r requirements.txt")
class ApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        from hub.config import Settings
        from hub.main import create_app

        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(
            bot_token=TOKEN,
            database_url="sqlite+aiosqlite:///" + str(Path(self.tmp.name) / "test.db"),
            public_url="https://orbit.example",
            app_keys={APP_KEY: "velora"},
            admin_ids=frozenset({777}),
        )
        self.bot = FakeBot()
        self.app = create_app(self.settings, bot=self.bot, run_polling=False)
        self.client = TestClient(self.app)
        self.client.__enter__()  # запускает lifespan: создаются таблицы
        self.headers = {"X-Telegram-Init-Data": make_init_data(user_id=42)}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def pay(self, plan, days=30, charge="ch-1", first=True):
        from hub import db

        async def go():
            # Отдельное подключение к той же базе: у TestClient свой event loop.
            engine = db.make_engine(self.settings.database_url)
            try:
                async with db.make_sessionmaker(engine)() as session:
                    return await db.record_payment(
                        session,
                        user_id=42,
                        plan=plan,
                        charge_id=charge,
                        amount_stars=99,
                        expires_at=datetime.now(timezone.utc) + timedelta(days=days),
                        is_recurring=True,
                        is_first_recurring=first,
                    )
            finally:
                await engine.dispose()

        return asyncio.run(go())

    def access(self, user_id=42, key=APP_KEY, app="velora"):
        return self.client.get(
            "/api/v1/access", params={"user_id": user_id, "app": app}, headers={"Authorization": f"Bearer {key}"}
        )

    def test_catalog_and_webapp(self):
        data = self.client.get("/api/catalog").json()
        self.assertEqual(data["bundle"]["code"], "all")
        self.assertEqual(self.client.get("/app/").status_code, 200)

    def test_me_requires_telegram(self):
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        me = self.client.get("/api/me", headers=self.headers).json()
        self.assertEqual(me["user"]["id"], 42)
        self.assertFalse(me["access"]["velora"]["active"])

    def test_invoice_is_a_stars_subscription(self):
        r = self.client.post("/api/invoice", json={"plan": "velora"}, headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        sent = self.bot.invoices[-1]
        self.assertEqual(sent["currency"], "XTR")
        self.assertEqual(sent["subscription_period"], 2592000)
        self.assertEqual(sent["payload"], "orbit:velora:42")

    def test_payment_opens_access(self):
        self.assertFalse(self.access().json()["active"])
        self.assertTrue(self.pay("all"))
        self.assertFalse(self.pay("all"))  # повтор того же события ничего не ломает
        body = self.access().json()
        self.assertTrue(body["active"])
        self.assertEqual(body["source"], "bundle")
        # пакет уже есть — отдельно velora покупать не нужно
        r = self.client.post("/api/invoice", json={"plan": "velora"}, headers=self.headers)
        self.assertEqual(r.status_code, 409)

    def test_cancel_and_resume(self):
        self.pay("velora", charge="first-charge")
        self.pay("velora", charge="renewal-charge", first=False)
        r = self.client.post("/api/subscriptions/velora/cancel", headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.bot.edits[-1]["telegram_payment_charge_id"], "first-charge")
        self.assertTrue(self.bot.edits[-1]["is_canceled"])
        me = self.client.get("/api/me", headers=self.headers).json()
        self.assertFalse(me["subscriptions"][0]["auto_renew"])
        self.client.post("/api/subscriptions/velora/resume", headers=self.headers)
        self.assertFalse(self.bot.edits[-1]["is_canceled"])

    def test_access_key_rules(self):
        self.assertEqual(self.access(key="wrong-key-000000000").status_code, 401)
        self.assertEqual(self.access(app="mera").status_code, 403)  # ключ VELORA не видит MERA
        self.assertTrue(self.access(user_id=777).json()["active"])  # админ


if __name__ == "__main__":
    unittest.main()
