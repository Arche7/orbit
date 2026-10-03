"""Проверка, что запрос действительно пришёл из Telegram Mini App.

Когда Telegram открывает Mini App, он передаёт строку initData — в ней данные
пользователя и подпись (hash). Подпись сделана токеном бота, поэтому подделать
её нельзя: если кто-то поменяет хотя бы букву, проверка не пройдёт.

Алгоритм — из официальной документации Telegram:
  secret_key = HMAC_SHA256(key="WebAppData", msg=BOT_TOKEN)
  hash       = HMAC_SHA256(key=secret_key, msg=data_check_string), hex
где data_check_string — все поля, кроме hash, отсортированные по имени
и соединённые как "ключ=значение" через перевод строки.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl


class InitDataError(ValueError):
    """initData не прошла проверку."""


@dataclass(frozen=True)
class TgUser:
    id: int
    first_name: str
    username: str | None = None
    language_code: str | None = None


@dataclass(frozen=True)
class InitData:
    user: TgUser
    auth_date: int
    start_param: str | None


def _secret_key(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def sign(fields: dict[str, str], bot_token: str) -> str:
    """Считает подпись для набора полей (используется в тестах)."""
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    return hmac.new(_secret_key(bot_token), check_string.encode(), hashlib.sha256).hexdigest()


def verify_init_data(
    init_data: str,
    bot_token: str,
    max_age: int = 24 * 60 * 60,
    now: float | None = None,
) -> InitData:
    if not init_data:
        raise InitDataError("пустая initData — Mini App открыта не из Telegram")

    fields = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))
    received_hash = fields.pop("hash", None)
    if not received_hash:
        raise InitDataError("в initData нет подписи")
    # Поле signature (Bot API 8.0+) входит в строку проверки — его НЕ убираем,
    # так же делает aiogram (check_webapp_signature). Вариант без signature
    # оставлен как запасной: подделать его без токена бота всё равно нельзя.
    without_signature = {k: v for k, v in fields.items() if k != "signature"}
    candidates = [fields] if without_signature == fields else [fields, without_signature]
    if not any(hmac.compare_digest(sign(c, bot_token), received_hash) for c in candidates):
        raise InitDataError("подпись initData не совпала")
    fields = without_signature

    try:
        auth_date = int(fields.get("auth_date", "0"))
    except ValueError as exc:
        raise InitDataError("неверный auth_date") from exc
    current = time.time() if now is None else now
    if max_age and current - auth_date > max_age:
        raise InitDataError("initData устарела — перезапусти Mini App")

    try:
        raw_user = json.loads(fields.get("user", ""))
        user = TgUser(
            id=int(raw_user["id"]),
            first_name=str(raw_user.get("first_name", "")),
            username=raw_user.get("username"),
            language_code=raw_user.get("language_code"),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise InitDataError("в initData нет пользователя") from exc

    return InitData(user=user, auth_date=auth_date, start_param=fields.get("start_param"))
