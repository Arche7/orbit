"""hub_access.py — проверка подписки ORBIT из любого твоего приложения.

Этот файл копируется как есть в MERA, VELORA и DELTA. Он не требует
никаких новых библиотек — только стандартный Python 3.10+.

Настройки (переменные окружения приложения):
    HUB_URL       адрес ORBIT, например https://orbit-production.up.railway.app
    HUB_APP_CODE  код приложения в каталоге ORBIT: mera / velora / delta
    HUB_API_KEY   ключ этого приложения (тот же, что прописан в HUB_APP_KEYS у ORBIT)
    HUB_BOT_LINK  ссылка на бота ORBIT, например https://t.me/orbit_hub_bot

Пример в обработчике aiogram:

    from hub_access import hub

    @router.message(Command("pro"))
    async def pro_feature(message: Message):
        access = await hub.acheck(message.from_user.id)
        if not access.active:
            await message.answer("Это функция подписки.", reply_markup=hub.paywall_keyboard())
            return
        ...  # доступ есть — делаем дело

Пример в FastAPI (у Mini App приложения):

    access = await hub.acheck(user_id)
    if not access.active:
        return {"paywall": hub.paywall_link()}
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class AccessResult:
    active: bool
    plan: str | None = None
    expires_at: str | None = None  # ISO-дата окончания или None
    source: str | None = None  # "app" | "bundle" | "admin"
    error: str | None = None  # если ORBIT не ответил — здесь причина


class HubAccess:
    # Положительный ответ кешируем подольше, отрицательный — коротко, чтобы
    # человек, только что оплативший подписку, получил доступ почти сразу.
    POSITIVE_TTL = 120
    NEGATIVE_TTL = 10

    def __init__(
        self,
        base_url: str,
        app_code: str,
        api_key: str,
        bot_link: str = "",
        timeout: float = 3.0,
        fail_open: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.app_code = app_code
        self.api_key = api_key
        self.bot_link = bot_link.rstrip("/")
        self.timeout = timeout
        # fail_open=True — если ORBIT недоступен и в кеше ничего нет, пускаем
        # пользователя (лучше для лояльности). False — не пускаем (строже).
        self.fail_open = fail_open
        self._cache: dict[int, tuple[float, AccessResult]] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "HubAccess":
        return cls(
            base_url=os.environ.get("HUB_URL", ""),
            app_code=os.environ.get("HUB_APP_CODE", ""),
            api_key=os.environ.get("HUB_API_KEY", ""),
            bot_link=os.environ.get("HUB_BOT_LINK", ""),
            fail_open=os.environ.get("HUB_FAIL_OPEN", "0") == "1",
        )

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.app_code and self.api_key)

    # --- проверка ----------------------------------------------------------

    def check(self, user_id: int) -> AccessResult:
        """Есть ли у пользователя доступ к этому приложению (синхронно)."""
        if not self.configured:
            return AccessResult(active=self.fail_open, error="hub_access не настроен (HUB_URL/HUB_APP_CODE/HUB_API_KEY)")

        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(user_id)
        if cached:
            saved_at, result = cached
            ttl = self.POSITIVE_TTL if result.active else self.NEGATIVE_TTL
            if now - saved_at < ttl:
                return result

        try:
            result = self._fetch(user_id)
        except Exception as exc:  # сеть, таймаут, 5xx — не роняем приложение
            if cached:
                return cached[1]  # лучше устаревший ответ, чем никакого
            return AccessResult(active=self.fail_open, error=f"ORBIT недоступен: {exc}")

        with self._lock:
            self._cache[user_id] = (now, result)
        return result

    async def acheck(self, user_id: int) -> AccessResult:
        """То же самое для async-кода (aiogram, FastAPI)."""
        return await asyncio.to_thread(self.check, user_id)

    def forget(self, user_id: int) -> None:
        """Сбросить кеш пользователя (например, после возврата из оплаты)."""
        with self._lock:
            self._cache.pop(user_id, None)

    def _fetch(self, user_id: int) -> AccessResult:
        query = urllib.parse.urlencode({"user_id": user_id, "app": self.app_code})
        request = urllib.request.Request(
            f"{self.base_url}/api/v1/access?{query}",
            headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 404):
                # Ошибка настройки: неверный ключ или код. Это не «ORBIT упал»,
                # поэтому не пускаем и явно пишем причину.
                return AccessResult(active=False, error=f"ORBIT ответил {exc.code}: проверь HUB_API_KEY и HUB_APP_CODE")
            raise
        return AccessResult(
            active=bool(data.get("active")),
            plan=data.get("plan"),
            expires_at=data.get("expires_at"),
            source=data.get("source"),
        )

    # --- переход к оплате --------------------------------------------------

    def paywall_link(self) -> str:
        """Ссылка, которая откроет ORBIT сразу на оплате этого приложения."""
        if not self.bot_link:
            return ""
        return f"{self.bot_link}?start=buy_{self.app_code}"

    def paywall_keyboard(self, text: str = "Оформить подписку ⭐"):
        """Готовая кнопка для aiogram. Импорт внутри — чтобы файл работал и без aiogram."""
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, url=self.paywall_link())]])


# Готовый объект: from hub_access import hub
hub = HubAccess.from_env()
