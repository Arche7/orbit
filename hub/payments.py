"""Мелкие правила оплаты: payload счёта и проверка перед списанием."""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import Catalog

# Telegram требует для подписок период ровно 30 дней (в секундах).
SUBSCRIPTION_PERIOD = 30 * 24 * 60 * 60  # 2592000
CURRENCY = "XTR"  # Telegram Stars
_PREFIX = "orbit"


@dataclass(frozen=True)
class Payload:
    plan: str
    user_id: int


def make_payload(plan: str, user_id: int) -> str:
    """Payload зашивается в счёт и возвращается к нам после оплаты."""
    return f"{_PREFIX}:{plan}:{user_id}"


def parse_payload(raw: str) -> Payload | None:
    parts = (raw or "").split(":")
    if len(parts) != 3 or parts[0] != _PREFIX:
        return None
    try:
        return Payload(plan=parts[1], user_id=int(parts[2]))
    except ValueError:
        return None


def check_pre_checkout(
    catalog: Catalog, payload_raw: str, payer_id: int, currency: str, amount: int
) -> str | None:
    """Проверка перед списанием Stars. Возвращает текст ошибки или None."""
    payload = parse_payload(payload_raw)
    if payload is None:
        return "Счёт устарел. Открой ORBIT и оформи подписку заново."
    if payload.user_id != payer_id:
        return "Этот счёт выставлен другому пользователю."
    if currency != CURRENCY:
        return "Оплата принимается только в Telegram Stars."
    if not catalog.is_purchasable(payload.plan):
        return "Этот план сейчас недоступен."
    if amount != catalog.plan_price(payload.plan):
        return "Цена изменилась. Открой ORBIT и оформи подписку заново."
    return None
