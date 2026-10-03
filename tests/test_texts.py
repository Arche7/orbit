"""Тексты бота: укладываются в лимиты Telegram и совпадают с каталогом."""

import unittest
from datetime import datetime, timezone
from pathlib import Path

from hub import texts
from hub.catalog import load_catalog

ROOT = Path(__file__).resolve().parent.parent


class TextsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog()

    def test_welcome_fits_caption_and_mentions_every_app_and_price(self):
        text = texts.welcome_new(self.catalog, "ORBIT", "Сеня")
        self.assertLessEqual(texts.visible_length(text), texts.CAPTION_LIMIT)
        for app in self.catalog.live_apps:
            self.assertIn(app.name, text)
        self.assertIn(f"{self.catalog.bundle.price_stars} ⭐", text)
        self.assertIn(f"{self.catalog.separate_total} ⭐", text)
        self.assertIn("Сеня", text)

    def test_name_is_html_escaped(self):
        text = texts.welcome_new(self.catalog, "ORBIT", "<b>x</b>")
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", text)
        self.assertIn("друг", texts.welcome_new(self.catalog, "ORBIT", "  "))

    def test_welcome_back_lists_active_plans(self):
        exp = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
        text = texts.welcome_back(
            self.catalog, "ORBIT", "Сеня", [texts.ActivePlan("VELORA", exp, True)]
        )
        self.assertIn("С возвращением", text)
        self.assertIn("VELORA", text)
        self.assertIn("3 ноября", text)
        self.assertIn("продлится автоматически", text)
        self.assertLessEqual(texts.visible_length(text), texts.CAPTION_LIMIT)

    def test_welcome_back_without_plans_offers_choice(self):
        text = texts.welcome_back(self.catalog, "ORBIT", "Сеня", [])
        self.assertIn(self.catalog.bundle.name, text)
        admin = texts.welcome_back(self.catalog, "ORBIT", "Сеня", [], is_admin=True)
        self.assertIn("администратора", admin)

    def test_buy_text_shows_plan_price(self):
        text = texts.welcome_buy(self.catalog, "Сеня", "velora")
        self.assertIn("VELORA", text)
        self.assertIn(f"{self.catalog.plan_price('velora')} ⭐", text)

    def test_tour_has_caption_and_slide_file_for_every_step(self):
        captions = texts.tour_captions(self.catalog, "ORBIT")
        self.assertEqual(len(captions), len(texts.TOUR_SLIDES))
        for caption in captions:
            self.assertLessEqual(texts.visible_length(caption), texts.CAPTION_LIMIT)
        for name in texts.TOUR_SLIDES + (texts.WELCOME_VIDEO,):
            self.assertTrue((ROOT / "hub" / "assets" / name).is_file(), name)

    def test_plans(self):
        text = texts.plans_text(self.catalog)
        for app in self.catalog.live_apps:
            self.assertIn(f"{app.name}</b> — {app.price_stars} ⭐", text)
        labels = texts.plan_button_labels(self.catalog)
        self.assertEqual(labels[0][0], self.catalog.bundle.code)
        self.assertEqual(len(labels), 1 + len(self.catalog.live_apps))

    def test_profile_descriptions_fit_limits(self):
        self.assertLessEqual(len(texts.bot_description(self.catalog, "ORBIT")), texts.DESCRIPTION_LIMIT)
        self.assertLessEqual(len(texts.bot_short_description(self.catalog)), texts.SHORT_DESCRIPTION_LIMIT)

    def test_paid_text(self):
        exp = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
        self.assertIn("активна", texts.paid_text("VELORA", exp, renewal=False))
        self.assertIn("продлена", texts.paid_text("VELORA", exp, renewal=True))


if __name__ == "__main__":
    unittest.main()
