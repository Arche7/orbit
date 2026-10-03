"""Тесты логики ORBIT, которым не нужны aiogram/FastAPI/база.

Запуск:  python -m unittest discover -s tests -v
"""

import json
import time
import unittest
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from hub.access import GRACE_PERIOD, SubRecord, access_map, app_access, purchase_blocker
from hub.catalog import CATALOG_PATH, CatalogError, load_catalog, parse_catalog
from hub.config import normalize_database_url, parse_app_keys, parse_ids, resolve_public_url
from hub.initdata import InitDataError, sign, verify_init_data
from hub.payments import check_pre_checkout, make_payload, parse_payload

TOKEN = "123456:TEST-token-for-unit-tests"
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def make_init_data(user_id=42, first_name="Арсений", auth_date=None, token=TOKEN, **extra):
    fields = {
        "auth_date": str(int(auth_date if auth_date is not None else time.time())),
        "query_id": "AAE",
        "user": json.dumps({"id": user_id, "first_name": first_name, "username": "arche7"}, ensure_ascii=False),
        **extra,
    }
    fields["hash"] = sign(fields, token)
    return urlencode(fields)


class InitDataTests(unittest.TestCase):
    def test_signature_field_is_part_of_hash(self):
        # Так присылает настоящий Telegram: signature подписан вместе с остальными полями.
        data = verify_init_data(make_init_data(signature="c2lnbmF0dXJl"), TOKEN)
        self.assertEqual(data.user.id, 42)

    def test_valid(self):
        data = verify_init_data(make_init_data(start_param="buy_velora"), TOKEN)
        self.assertEqual(data.user.id, 42)
        self.assertEqual(data.user.first_name, "Арсений")
        self.assertEqual(data.start_param, "buy_velora")

    def test_wrong_token(self):
        with self.assertRaises(InitDataError):
            verify_init_data(make_init_data(token="999:other"), TOKEN)

    def test_tampered_user(self):
        raw = make_init_data().replace("42", "43")
        with self.assertRaises(InitDataError):
            verify_init_data(raw, TOKEN)

    def test_expired(self):
        old = time.time() - 2 * 24 * 3600
        with self.assertRaises(InitDataError):
            verify_init_data(make_init_data(auth_date=old), TOKEN)

    def test_empty(self):
        with self.assertRaises(InitDataError):
            verify_init_data("", TOKEN)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog()

    def test_real_catalog_is_valid(self):
        codes = [a.code for a in self.catalog.live_apps]
        self.assertEqual(codes, ["mera", "velora", "delta"])
        self.assertFalse(self.catalog.is_purchasable("next"))  # «Скоро» купить нельзя
        self.assertTrue(self.catalog.is_purchasable("all"))

    def test_bundle_is_cheaper(self):
        self.assertGreater(self.catalog.bundle_savings, 0)
        self.assertLess(self.catalog.bundle.price_stars, self.catalog.separate_total)

    def test_bundle_covers_future_apps(self):
        self.assertTrue(self.catalog.plan_covers("all", "next"))
        self.assertFalse(self.catalog.plan_covers("velora", "delta"))

    def test_placeholder_links_hidden(self):
        public = {a["code"]: a for a in self.catalog.to_public_dict()["apps"]}
        self.assertTrue(public["mera"]["link"].startswith("https://t.me/"))
        self.assertEqual(public["velora"]["link"], "")  # YOUR_VELORA_BOT ещё не заменён

    def test_errors_are_explained(self):
        good = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        bad = json.loads(json.dumps(good))
        bad["apps"][1]["code"] = "mera"
        with self.assertRaisesRegex(CatalogError, "повторяется"):
            parse_catalog(bad)
        bad = json.loads(json.dumps(good))
        bad["apps"][0]["price_stars"] = 0
        with self.assertRaisesRegex(CatalogError, "цена"):
            parse_catalog(bad)
        bad = json.loads(json.dumps(good))
        bad["apps"][0]["colors"]["accent"] = "gold"
        with self.assertRaisesRegex(CatalogError, "accent"):
            parse_catalog(bad)


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog()

    def sub(self, plan, days, auto_renew=True):
        return SubRecord(plan=plan, expires_at=NOW + timedelta(days=days), auto_renew=auto_renew, charge_id="c")

    def test_no_subs(self):
        result = access_map(self.catalog, [], NOW)
        self.assertFalse(any(a.active for a in result.values()))

    def test_single_app(self):
        result = access_map(self.catalog, [self.sub("velora", 10)], NOW)
        self.assertTrue(result["velora"].active)
        self.assertEqual(result["velora"].source, "app")
        self.assertFalse(result["delta"].active)

    def test_bundle_opens_everything(self):
        result = access_map(self.catalog, [self.sub("all", 5)], NOW)
        self.assertTrue(all(a.active for a in result.values()))
        self.assertEqual(result["mera"].source, "bundle")

    def test_latest_expiry_wins(self):
        result = app_access(self.catalog, "delta", [self.sub("delta", 3), self.sub("all", 20)], NOW)
        self.assertEqual(result.plan, "all")
        self.assertEqual(result.expires_at, NOW + timedelta(days=20))

    def test_grace_only_with_auto_renew(self):
        just_expired = NOW - timedelta(hours=1)
        renewing = SubRecord("velora", just_expired, auto_renew=True)
        canceled = SubRecord("velora", just_expired, auto_renew=False)
        self.assertTrue(app_access(self.catalog, "velora", [renewing], NOW).active)
        self.assertFalse(app_access(self.catalog, "velora", [canceled], NOW).active)
        long_ago = SubRecord("velora", NOW - GRACE_PERIOD - timedelta(minutes=1), auto_renew=True)
        self.assertFalse(app_access(self.catalog, "velora", [long_ago], NOW).active)

    def test_admin_has_everything(self):
        self.assertTrue(app_access(self.catalog, "mera", [], NOW, is_admin=True).active)

    def test_unknown_app(self):
        self.assertFalse(app_access(self.catalog, "nope", [self.sub("all", 5)], NOW).active)

    def test_purchase_rules(self):
        self.assertIsNone(purchase_blocker(self.catalog, "velora", [], NOW))
        self.assertIsNotNone(purchase_blocker(self.catalog, "next", [], NOW))
        self.assertIn("уже активна", purchase_blocker(self.catalog, "velora", [self.sub("velora", 5)], NOW))
        self.assertIn("Возобновить", purchase_blocker(self.catalog, "velora", [self.sub("velora", 5, False)], NOW))
        self.assertIn("Всё включено", purchase_blocker(self.catalog, "delta", [self.sub("all", 5)], NOW))
        # Апгрейд с одного приложения до пакета разрешён.
        self.assertIsNone(purchase_blocker(self.catalog, "all", [self.sub("delta", 5)], NOW))


class PaymentTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog()

    def test_payload_roundtrip(self):
        payload = parse_payload(make_payload("velora", 42))
        self.assertEqual((payload.plan, payload.user_id), ("velora", 42))
        self.assertIsNone(parse_payload("garbage"))
        self.assertLessEqual(len(make_payload("all", 9_999_999_999).encode()), 128)

    def test_pre_checkout(self):
        payload = make_payload("velora", 42)
        price = self.catalog.plan_price("velora")
        self.assertIsNone(check_pre_checkout(self.catalog, payload, 42, "XTR", price))
        self.assertIsNotNone(check_pre_checkout(self.catalog, payload, 43, "XTR", price))
        self.assertIsNotNone(check_pre_checkout(self.catalog, payload, 42, "RUB", price))
        self.assertIsNotNone(check_pre_checkout(self.catalog, payload, 42, "XTR", price + 1))
        self.assertIsNotNone(check_pre_checkout(self.catalog, make_payload("next", 42), 42, "XTR", 99))


class ConfigTests(unittest.TestCase):
    def test_database_url(self):
        self.assertTrue(normalize_database_url(None).startswith("sqlite+aiosqlite:///"))
        self.assertEqual(normalize_database_url("postgresql://u:p@h:5432/db"), "postgresql+asyncpg://u:p@h:5432/db")
        self.assertEqual(normalize_database_url("postgres://u:p@h/db"), "postgresql+asyncpg://u:p@h/db")

    def test_app_keys(self):
        keys = parse_app_keys("mera:aaaaaaaaaaaaaaaa1, VELORA:bbbbbbbbbbbbbbbb2")
        self.assertEqual(keys, {"aaaaaaaaaaaaaaaa1": "mera", "bbbbbbbbbbbbbbbb2": "velora"})
        with self.assertRaises(ValueError):
            parse_app_keys("mera:short")
        with self.assertRaises(ValueError):
            parse_app_keys("no-colon-here")

    def test_ids_and_url(self):
        self.assertEqual(parse_ids("1, 2,3"), frozenset({1, 2, 3}))
        self.assertEqual(resolve_public_url(None, "orbit.up.railway.app"), "https://orbit.up.railway.app")
        self.assertEqual(resolve_public_url("https://x.ru/", "ignored"), "https://x.ru")


if __name__ == "__main__":
    unittest.main()
