"""Каталог приложений ORBIT.

Каталог хранится в hub/catalog.json — чтобы добавить новое приложение,
достаточно дописать туда ещё один блок (см. README). Этот модуль читает файл,
проверяет, что в нём нет ошибок, и отвечает на вопросы вида
«сколько стоит план velora?» или «покрывает ли план all приложение delta?».

«План» — это то, на что можно подписаться:
  * код приложения (например "velora") — подписка на одно приложение;
  * код пакета (по умолчанию "all") — подписка на всё сразу.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parent / "catalog.json"

# Ограничения Telegram: цена подписки в Stars — целое число больше нуля.
# Верхний предел Telegram может менять, поэтому держим разумную планку.
MAX_PRICE_STARS = 10_000
_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,23}$")
_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
STATUSES = ("live", "soon")


class CatalogError(ValueError):
    """Ошибка в catalog.json — сообщение объясняет, что именно не так."""


@dataclass(frozen=True)
class App:
    code: str
    name: str
    tagline: str
    status: str
    price_stars: int
    link: str
    monogram: str
    colors: dict[str, str]
    features: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_live(self) -> bool:
        return self.status == "live"

    @property
    def link_configured(self) -> bool:
        return bool(self.link) and "YOUR_" not in self.link


@dataclass(frozen=True)
class Bundle:
    code: str
    name: str
    tagline: str
    price_stars: int


@dataclass(frozen=True)
class Catalog:
    bundle: Bundle
    apps: tuple[App, ...]

    # --- поиск -----------------------------------------------------------
    def get_app(self, code: str) -> App | None:
        return next((a for a in self.apps if a.code == code), None)

    @property
    def live_apps(self) -> tuple[App, ...]:
        return tuple(a for a in self.apps if a.is_live)

    # --- планы -----------------------------------------------------------
    def is_purchasable(self, plan: str) -> bool:
        """Можно ли сейчас купить этот план."""
        if plan == self.bundle.code:
            return bool(self.live_apps)
        app = self.get_app(plan)
        return app is not None and app.is_live

    def plan_price(self, plan: str) -> int:
        if plan == self.bundle.code:
            return self.bundle.price_stars
        app = self.get_app(plan)
        if app is None:
            raise KeyError(plan)
        return app.price_stars

    def plan_title(self, plan: str) -> str:
        if plan == self.bundle.code:
            return self.bundle.name
        app = self.get_app(plan)
        if app is None:
            raise KeyError(plan)
        return app.name

    def plan_covers(self, plan: str, app_code: str) -> bool:
        """Даёт ли план доступ к приложению.

        Пакет покрывает любое приложение каталога — в том числе то, что
        появится позже. Это и есть главный аргумент купить пакет.
        """
        if plan == self.bundle.code:
            return self.get_app(app_code) is not None
        return plan == app_code

    @property
    def separate_total(self) -> int:
        """Сколько стоили бы все живые приложения по отдельности."""
        return sum(a.price_stars for a in self.live_apps)

    @property
    def bundle_savings(self) -> int:
        return max(0, self.separate_total - self.bundle.price_stars)

    # --- для Mini App ------------------------------------------------------
    def to_public_dict(self) -> dict:
        return {
            "bundle": {
                "code": self.bundle.code,
                "name": self.bundle.name,
                "tagline": self.bundle.tagline,
                "price_stars": self.bundle.price_stars,
                "separate_total": self.separate_total,
                "savings": self.bundle_savings,
            },
            "apps": [
                {
                    "code": a.code,
                    "name": a.name,
                    "tagline": a.tagline,
                    "status": a.status,
                    "price_stars": a.price_stars,
                    "link": a.link if a.link_configured else "",
                    "monogram": a.monogram,
                    "colors": a.colors,
                    "features": list(a.features),
                }
                for a in self.apps
            ],
        }


# --- загрузка и проверка ------------------------------------------------------


def _require(data: dict, key: str, kind: type, where: str):
    if key not in data:
        raise CatalogError(f"{where}: нет поля {key!r}")
    value = data[key]
    if not isinstance(value, kind) or isinstance(value, bool) and kind is int:
        raise CatalogError(f"{where}: поле {key!r} должно быть {kind.__name__}")
    return value


def _check_price(value: int, where: str) -> int:
    if not 1 <= value <= MAX_PRICE_STARS:
        raise CatalogError(f"{where}: цена должна быть от 1 до {MAX_PRICE_STARS} Stars")
    return value


def parse_catalog(data: dict) -> Catalog:
    if not isinstance(data, dict):
        raise CatalogError("catalog.json: ожидался объект { ... }")

    b = _require(data, "bundle", dict, "catalog")
    bundle = Bundle(
        code=_require(b, "code", str, "bundle"),
        name=_require(b, "name", str, "bundle"),
        tagline=_require(b, "tagline", str, "bundle"),
        price_stars=_check_price(_require(b, "price_stars", int, "bundle"), "bundle"),
    )
    if not _CODE_RE.match(bundle.code):
        raise CatalogError("bundle: code — только латиница в нижнем регистре, цифры и _")

    raw_apps = _require(data, "apps", list, "catalog")
    apps: list[App] = []
    seen = {bundle.code}
    for i, item in enumerate(raw_apps):
        where = f"apps[{i}]"
        if not isinstance(item, dict):
            raise CatalogError(f"{where}: ожидался объект")
        code = _require(item, "code", str, where)
        where = f"приложение {code!r}"
        if not _CODE_RE.match(code):
            raise CatalogError(f"{where}: code — только латиница в нижнем регистре, цифры и _")
        if code in seen:
            raise CatalogError(f"{where}: код повторяется (или совпадает с кодом пакета)")
        seen.add(code)

        status = _require(item, "status", str, where)
        if status not in STATUSES:
            raise CatalogError(f"{where}: status должен быть одним из {STATUSES}")

        link = item.get("link", "") or ""
        if not isinstance(link, str):
            raise CatalogError(f"{where}: link должен быть строкой")
        if status == "live" and not link.startswith("https://t.me/"):
            raise CatalogError(f"{where}: у живого приложения link должен начинаться с https://t.me/")

        colors = _require(item, "colors", dict, where)
        for key in ("from", "to", "accent"):
            if not _COLOR_RE.match(str(colors.get(key, ""))):
                raise CatalogError(f"{where}: colors.{key} должен быть цветом вида #A1B2C3")

        features = item.get("features", [])
        if not isinstance(features, list) or not all(isinstance(f, str) for f in features):
            raise CatalogError(f"{where}: features — список строк")

        apps.append(
            App(
                code=code,
                name=_require(item, "name", str, where),
                tagline=_require(item, "tagline", str, where),
                status=status,
                price_stars=_check_price(_require(item, "price_stars", int, where), where),
                link=link,
                monogram=_require(item, "monogram", str, where)[:2],
                colors={k: colors[k] for k in ("from", "to", "accent")},
                features=tuple(features),
            )
        )

    if not apps:
        raise CatalogError("catalog: список apps пуст")
    return Catalog(bundle=bundle, apps=tuple(apps))


def load_catalog(path: Path = CATALOG_PATH) -> Catalog:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CatalogError(f"{path.name}: ошибка JSON в строке {exc.lineno}: {exc.msg}") from exc
    return parse_catalog(data)
