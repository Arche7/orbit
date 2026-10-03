"""Настройки ORBIT.

Все настройки берутся из переменных окружения (на Railway — вкладка Variables,
локально — файл .env). Этот файл ничего не импортирует, кроме стандартной
библиотеки Python, поэтому его можно проверять тестами без интернета.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Корень проекта (папка, где лежат hub/, webapp/, integrations/).
BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Простейшая загрузка .env: строки вида KEY=VALUE.

    Уже заданные переменные окружения не перезаписываются — так на Railway
    всегда побеждают значения из вкладки Variables.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def normalize_database_url(url: str | None) -> str:
    """Приводит адрес базы к виду, который понимает SQLAlchemy (async).

    Railway выдаёт адрес вида postgresql://..., а асинхронному драйверу нужен
    postgresql+asyncpg://... . Если адрес не задан — используем локальный
    файл SQLite (удобно для запуска на своём компьютере).
    """
    if not url:
        return "sqlite+aiosqlite:///" + str(BASE_DIR / "orbit.db")
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://"):]
    return url


def parse_app_keys(raw: str | None) -> dict[str, str]:
    """Разбирает HUB_APP_KEYS вида "mera:ключ1,velora:ключ2".

    Возвращает словарь {ключ: код_приложения}. Ключом словаря сделан сам
    секрет, чтобы по пришедшему ключу сразу понять, какое приложение спрашивает.
    """
    result: dict[str, str] = {}
    if not raw:
        return result
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise ValueError(f"HUB_APP_KEYS: ожидался формат app:ключ, получено {chunk!r}")
        app_code, key = chunk.split(":", 1)
        app_code, key = app_code.strip().lower(), key.strip()
        if len(key) < 16:
            raise ValueError(f"HUB_APP_KEYS: ключ для {app_code!r} короче 16 символов")
        result[key] = app_code
    return result


def parse_ids(raw: str | None) -> frozenset[int]:
    """Разбирает список Telegram ID через запятую: "123,456"."""
    if not raw:
        return frozenset()
    return frozenset(int(part) for part in raw.replace(" ", "").split(",") if part)


def resolve_public_url(explicit: str | None, railway_domain: str | None) -> str:
    """Публичный адрес сервера. На Railway домен подставляется сам."""
    if explicit:
        return explicit.rstrip("/")
    if railway_domain:
        return "https://" + railway_domain.strip("/")
    return ""


@dataclass(frozen=True)
class Settings:
    bot_token: str
    hub_name: str = "ORBIT"
    database_url: str = field(default_factory=lambda: normalize_database_url(None))
    public_url: str = ""
    app_keys: dict[str, str] = field(default_factory=dict)
    admin_ids: frozenset[int] = frozenset()
    support_contact: str = ""
    init_data_max_age: int = 24 * 60 * 60  # сколько секунд «живёт» вход из Mini App

    @property
    def webapp_url(self) -> str:
        return f"{self.public_url}/app/" if self.public_url else ""


def load_settings() -> Settings:
    _load_dotenv(BASE_DIR / ".env")
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "Не задан BOT_TOKEN. Возьми токен у @BotFather и добавь его "
            "в .env (локально) или во вкладку Variables на Railway."
        )
    return Settings(
        bot_token=token,
        hub_name=os.environ.get("HUB_NAME", "ORBIT").strip() or "ORBIT",
        database_url=normalize_database_url(os.environ.get("DATABASE_URL")),
        public_url=resolve_public_url(
            os.environ.get("PUBLIC_URL"), os.environ.get("RAILWAY_PUBLIC_DOMAIN")
        ),
        app_keys=parse_app_keys(os.environ.get("HUB_APP_KEYS")),
        admin_ids=parse_ids(os.environ.get("ADMIN_IDS")),
        support_contact=os.environ.get("SUPPORT_CONTACT", "").strip(),
    )
