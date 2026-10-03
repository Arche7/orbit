"""Тексты бота ORBIT.

Все тексты собраны здесь и строятся из каталога (hub/catalog.json), чтобы
цены и названия в сообщениях всегда совпадали с витриной. Модуль не зависит
от aiogram — его проверяют обычные тесты.

Лимиты Telegram (из документации Bot API):
  подпись к фото/видео — до 1024 символов видимого текста;
  описание бота — до 512; короткое описание — до 120.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .catalog import Catalog

CAPTION_LIMIT = 1024
DESCRIPTION_LIMIT = 512
SHORT_DESCRIPTION_LIMIT = 120

BULLET = "◆"
MOSCOW = timezone(timedelta(hours=3))
MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]

# Слайды карусели «Как это работает» — файлы лежат в hub/assets.
TOUR_SLIDES = ("slide-1.jpg", "slide-2.jpg", "slide-3.jpg", "slide-4.jpg")
WELCOME_VIDEO = "welcome.mp4"


@dataclass(frozen=True)
class ActivePlan:
    title: str
    expires_at: datetime
    auto_renew: bool


def visible_length(text: str) -> int:
    """Длина текста так, как её считает Telegram: без HTML-тегов."""
    return len(html.unescape(re.sub(r"<[^>]+>", "", text)))


def fmt_date(value: datetime) -> str:
    local = value.astimezone(MOSCOW)
    return f"{local.day} {MONTHS[local.month - 1]}"


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def _name(first_name: str | None) -> str:
    return html.escape(first_name.strip()) if first_name and first_name.strip() else "друг"


def _apps_lines(catalog: Catalog) -> str:
    return "\n".join(
        f"{BULLET} <b>{html.escape(a.name)}</b> — {html.escape(_lower_first(a.tagline))}"
        for a in catalog.live_apps
    )


def _bundle_line(catalog: Catalog) -> str:
    b = catalog.bundle
    line = f"<b>{html.escape(b.name)}</b> — {b.price_stars} ⭐ в месяц"
    if catalog.bundle_savings:
        line += f" вместо {catalog.separate_total} ⭐"
    return line + ". Новые приложения — без доплат."


# --- /start ---------------------------------------------------------------

def welcome_new(catalog: Catalog, hub_name: str, first_name: str | None) -> str:
    return (
        f"<b>{_name(first_name)}, добро пожаловать в {html.escape(hub_name)}</b>\n\n"
        "Я собираю приложения в одну орбиту: один вход через Telegram, "
        "одна подписка, никаких регистраций и паролей.\n\n"
        f"{_apps_lines(catalog)}\n\n"
        f"{_bundle_line(catalog)}\n\n"
        f"Начни с кнопки «Открыть {html.escape(hub_name)}» 👇"
    )


def welcome_back(
    catalog: Catalog,
    hub_name: str,
    first_name: str | None,
    active: list[ActivePlan],
    is_admin: bool = False,
) -> str:
    text = f"<b>С возвращением, {_name(first_name)}</b>\n\n"
    if is_admin:
        text += "Режим администратора: все приложения открыты.\n\n"
    if active:
        text += "Твоя орбита сейчас:\n"
        for p in active:
            tail = "продлится автоматически" if p.auto_renew else "продление отключено"
            text += f"{BULLET} <b>{html.escape(p.title)}</b> — до {fmt_date(p.expires_at)}, {tail}\n"
        text += "\nВсе приложения — в кнопке ниже."
    elif not is_admin:
        text += (
            f"{_apps_lines(catalog)}\n\n"
            f"{_bundle_line(catalog)}\n\n"
            "Загляни в тарифы или открой ORBIT, чтобы выбрать."
        )
    else:
        text += "Все приложения — в кнопке ниже."
    return text


def welcome_buy(catalog: Catalog, first_name: str | None, plan: str) -> str:
    title = html.escape(catalog.plan_title(plan))
    price = catalog.plan_price(plan)
    text = f"<b>{_name(first_name)}, оформим «{title}»?</b>\n\n{price} ⭐ в месяц, доступ откроется сразу."
    if plan == catalog.bundle.code:
        text += " Входят все приложения — и будущие тоже."
    return text + "\nОтменить можно в любой момент — доступ останется до конца оплаченного месяца."


# --- карусель «Как это работает» -------------------------------------------

def tour_captions(catalog: Catalog, hub_name: str) -> list[str]:
    b = catalog.bundle
    names = ", ".join(a.name for a in catalog.live_apps)
    saving = f" Выгода {catalog.bundle_savings} ⭐ по сравнению с подпиской по отдельности." if catalog.bundle_savings else ""
    plans = "\n".join(
        f"{BULLET} <b>{html.escape(a.name)}</b> — {html.escape(_lower_first(a.tagline))} · {a.price_stars} ⭐"
        for a in catalog.live_apps
    )
    return [
        f"<b>{html.escape(hub_name)} — все приложения в одной орбите</b>\n\n"
        f"{html.escape(names)} — в одном месте. Вход через Telegram: "
        "без регистраций, паролей и лишних ботов.",
        f"<b>{_count_title(len(catalog.live_apps))} — одна экосистема</b>\n\n{plans}",
        f"<b>{html.escape(b.name)} — {b.price_stars} ⭐ в месяц</b>\n\n"
        f"Все приложения, что есть сейчас, и все, что выйдут потом.{saving}",
        "<b>Как это работает</b>\n\n"
        f"1. Открой {html.escape(hub_name)}\n"
        f"2. Выбери приложение или «{html.escape(b.name)}»\n"
        "3. Оплати в Telegram Stars — доступ откроется сразу\n\n"
        "Отменить можно в любой момент.",
    ]


def _count_title(n: int) -> str:
    words = {2: "Два приложения", 3: "Три приложения", 4: "Четыре приложения"}
    return words.get(n, "Все приложения")


# --- тарифы -----------------------------------------------------------------

def plans_text(catalog: Catalog) -> str:
    b = catalog.bundle
    text = f"<b>Тарифы</b>\n\n{BULLET} <b>{html.escape(b.name)}</b> — {b.price_stars} ⭐/мес\n"
    text += "Все приложения, включая будущие."
    if catalog.bundle_savings:
        text += f" Выгода {catalog.bundle_savings} ⭐."
    text += "\n\n" + "\n".join(
        f"{BULLET} <b>{html.escape(a.name)}</b> — {a.price_stars} ⭐/мес" for a in catalog.live_apps
    )
    text += (
        "\n\nОплата в Telegram Stars, продление раз в 30 дней. "
        "Отменить можно в любой момент — доступ сохранится до конца периода."
    )
    return text


def plan_button_labels(catalog: Catalog) -> list[tuple[str, str]]:
    """(код плана, подпись кнопки) — сначала пакет, потом приложения."""
    b = catalog.bundle
    items = [(b.code, f"✦ {b.name} · {b.price_stars} ⭐")]
    items += [(a.code, f"{a.name} · {a.price_stars} ⭐") for a in catalog.live_apps]
    return items


# --- профиль бота -------------------------------------------------------------

def bot_description(catalog: Catalog, hub_name: str) -> str:
    """Текст в пустом чате до первого /start (до 512 символов)."""
    lines = "\n".join(f"{BULLET} {a.name} — {_lower_first(a.tagline)}" for a in catalog.live_apps)
    return (
        f"{hub_name} — все приложения в одной орбите.\n\n{lines}\n\n"
        "Один вход через Telegram и одна подписка в Stars — "
        "включая все будущие приложения. Нажми кнопку внизу, чтобы начать."
    )


def bot_short_description(catalog: Catalog) -> str:
    """Текст в профиле бота (до 120 символов)."""
    names = catalog.live_apps
    if len(names) > 1:
        joined = ", ".join(a.name for a in names[:-1]) + " и " + names[-1].name
    else:
        joined = names[0].name if names else "Все приложения"
    return f"{joined} в одном месте. Одна подписка в Telegram Stars."


# --- прочее -------------------------------------------------------------------

def help_text(hub_name: str) -> str:
    return (
        f"<b>{html.escape(hub_name)}</b> — один вход во все приложения и одна подписка.\n\n"
        f"{BULLET} Открыть — кнопкой меню слева от поля ввода или /start\n"
        f"{BULLET} Тарифы и цены — /plans\n"
        f"{BULLET} Вопросы по оплате — /paysupport\n"
        f"{BULLET} Условия подписки — /terms"
    )


def paid_text(title: str, expires: datetime, renewal: bool) -> str:
    title = html.escape(title)
    if renewal:
        return f"🔁 Подписка «{title}» продлена до {fmt_date(expires)}."
    return (
        f"✦ <b>Готово — «{title}» активна</b>\n\n"
        f"До {fmt_date(expires)}, дальше продлится автоматически раз в 30 дней. "
        "Отменить можно в любой момент.\n\nПриложения уже открыты — жми кнопку ниже."
    )
