"""Расчёт доступа: у кого к какому приложению сейчас есть доступ.

Здесь нет базы данных и Telegram — только правила. Поэтому их легко проверять
тестами и легко менять: вся «бизнес-логика» подписок живёт в этом файле.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .catalog import Catalog

# Telegram продлевает подписку примерно в момент окончания, и сообщение об
# оплате может прийти с задержкой. Чтобы не отключать человека на эти минуты,
# даём запас — но только тем, у кого продление включено.
GRACE_PERIOD = timedelta(hours=12)


@dataclass(frozen=True)
class SubRecord:
    """Одна подписка пользователя (как она лежит в базе)."""

    plan: str
    expires_at: datetime
    auto_renew: bool = True
    charge_id: str | None = None


@dataclass(frozen=True)
class AppAccess:
    app: str
    active: bool
    plan: str | None = None
    expires_at: datetime | None = None
    source: str | None = None  # "admin" | "app" | "bundle"

    def to_dict(self) -> dict:
        return {
            "app": self.app,
            "active": self.active,
            "plan": self.plan,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "source": self.source,
        }


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_sub_active(sub: SubRecord, now: datetime) -> bool:
    deadline = sub.expires_at + (GRACE_PERIOD if sub.auto_renew else timedelta(0))
    return deadline > now


def app_access(
    catalog: Catalog,
    app_code: str,
    subs: list[SubRecord],
    now: datetime | None = None,
    is_admin: bool = False,
) -> AppAccess:
    now = now or utcnow()
    if catalog.get_app(app_code) is None:
        return AppAccess(app=app_code, active=False)
    if is_admin:
        return AppAccess(app=app_code, active=True, source="admin")

    covering = [
        s for s in subs if catalog.plan_covers(s.plan, app_code) and is_sub_active(s, now)
    ]
    if not covering:
        return AppAccess(app=app_code, active=False)

    best = max(covering, key=lambda s: s.expires_at)
    source = "bundle" if best.plan == catalog.bundle.code else "app"
    return AppAccess(
        app=app_code, active=True, plan=best.plan, expires_at=best.expires_at, source=source
    )


def access_map(
    catalog: Catalog,
    subs: list[SubRecord],
    now: datetime | None = None,
    is_admin: bool = False,
) -> dict[str, AppAccess]:
    now = now or utcnow()
    return {a.code: app_access(catalog, a.code, subs, now, is_admin) for a in catalog.apps}


def purchase_blocker(
    catalog: Catalog,
    plan: str,
    subs: list[SubRecord],
    now: datetime | None = None,
) -> str | None:
    """Объясняет, почему план сейчас покупать не нужно. None — можно покупать."""
    now = now or utcnow()
    if not catalog.is_purchasable(plan):
        return "Этот план пока нельзя оформить."

    by_plan = {s.plan: s for s in subs if is_sub_active(s, now)}
    same = by_plan.get(plan)
    if same is not None:
        if same.auto_renew:
            return "Эта подписка уже активна."
        return "Подписка активна, но продление отключено — включи его кнопкой «Возобновить»."

    bundle = by_plan.get(catalog.bundle.code)
    if bundle is not None and plan != catalog.bundle.code and bundle.auto_renew:
        return "Это приложение уже входит в твою подписку «Всё включено»."
    return None
