"""HTTP API ORBIT (FastAPI).

Два вида клиентов:

1. Mini App ORBIT — передаёт заголовок X-Telegram-Init-Data, по нему мы
   понимаем, кто пользователь (см. initdata.py).
     GET  /api/catalog                       — каталог (без входа)
     GET  /api/me                            — я, мои подписки и доступы
     POST /api/invoice        {"plan": ...}  — ссылка на оплату в Stars
     POST /api/subscriptions/{plan}/cancel   — отключить автопродление
     POST /api/subscriptions/{plan}/resume   — включить обратно

2. Твои приложения (MERA, VELORA, DELTA) — спрашивают, есть ли доступ:
     GET  /api/v1/access?user_id=123&app=velora
     Authorization: Bearer <ключ приложения из HUB_APP_KEYS>
   Ключ каждого приложения даёт право спрашивать только про своё приложение.
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass

from aiogram.types import LabeledPrice
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from . import db
from .access import access_map, app_access, is_sub_active, purchase_blocker, utcnow
from .catalog import Catalog
from .config import Settings
from .initdata import InitDataError, TgUser, verify_init_data
from .payments import CURRENCY, SUBSCRIPTION_PERIOD, make_payload

log = logging.getLogger("orbit.api")
router = APIRouter()


# --- зависимости ------------------------------------------------------------------


@dataclass
class Deps:
    catalog: Catalog
    settings: Settings
    sessionmaker: async_sessionmaker[AsyncSession]
    bot: object  # aiogram.Bot (в тестах — заглушка)


def get_deps(request: Request) -> Deps:
    return request.app.state.deps


def get_tg_user(
    deps: Deps = Depends(get_deps),
    x_telegram_init_data: str = Header(default=""),
) -> TgUser:
    try:
        data = verify_init_data(
            x_telegram_init_data, deps.settings.bot_token, deps.settings.init_data_max_age
        )
    except InitDataError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return data.user


class InvoiceIn(BaseModel):
    plan: str


# --- для Mini App -----------------------------------------------------------------


@router.get("/api/catalog")
async def catalog(deps: Deps = Depends(get_deps)) -> dict:
    return deps.catalog.to_public_dict() | {"hub_name": deps.settings.hub_name}


@router.get("/api/me")
async def me(user: TgUser = Depends(get_tg_user), deps: Deps = Depends(get_deps)) -> dict:
    async with deps.sessionmaker() as session:
        await db.upsert_user(session, user.id, user.first_name, user.username)
        subs = await db.get_subscriptions(session, user.id)

    is_admin = user.id in deps.settings.admin_ids
    now = utcnow()
    access = access_map(deps.catalog, subs, now=now, is_admin=is_admin)
    return {
        "user": {"id": user.id, "first_name": user.first_name, "username": user.username},
        "is_admin": is_admin,
        "access": {code: item.to_dict() for code, item in access.items()},
        "subscriptions": [
            {
                "plan": s.plan,
                "title": deps.catalog.plan_title(s.plan) if _known(deps.catalog, s.plan) else s.plan,
                "expires_at": s.expires_at.isoformat(),
                "auto_renew": s.auto_renew,
                "active": is_sub_active(s, now),
            }
            for s in sorted(subs, key=lambda s: s.expires_at, reverse=True)
        ],
    }


def _known(catalog: Catalog, plan: str) -> bool:
    return plan == catalog.bundle.code or catalog.get_app(plan) is not None


@router.post("/api/invoice")
async def invoice(
    body: InvoiceIn, user: TgUser = Depends(get_tg_user), deps: Deps = Depends(get_deps)
) -> dict:
    plan = body.plan.strip().lower()
    async with deps.sessionmaker() as session:
        subs = await db.get_subscriptions(session, user.id)
    blocker = purchase_blocker(deps.catalog, plan, subs)
    if blocker:
        raise HTTPException(status_code=409, detail=blocker)

    title = deps.catalog.plan_title(plan)
    price = deps.catalog.plan_price(plan)
    if plan == deps.catalog.bundle.code:
        description = deps.catalog.bundle.tagline
    else:
        description = deps.catalog.get_app(plan).tagline

    link = await deps.bot.create_invoice_link(
        title=f"{deps.settings.hub_name} · {title}"[:32],
        description=description[:255],
        payload=make_payload(plan, user.id),
        currency=CURRENCY,
        prices=[LabeledPrice(label=title[:32], amount=price)],
        subscription_period=SUBSCRIPTION_PERIOD,
    )
    return {"link": link, "plan": plan, "price_stars": price}


async def _toggle(plan: str, user: TgUser, deps: Deps, cancel: bool) -> dict:
    async with deps.sessionmaker() as session:
        charge_id = await db.find_charge(session, user.id, plan)
    if charge_id is None:
        raise HTTPException(status_code=404, detail="Такой подписки нет.")
    try:
        await deps.bot.edit_user_star_subscription(
            user_id=user.id, telegram_payment_charge_id=charge_id, is_canceled=cancel
        )
    except Exception as exc:
        log.warning("editUserStarSubscription не сработал: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="Telegram не принял изменение. Попробуй ещё раз или измени подписку в настройках Telegram.",
        ) from exc
    async with deps.sessionmaker() as session:
        await db.set_auto_renew(session, user.id, plan, not cancel)
    return {"plan": plan, "auto_renew": not cancel}


@router.post("/api/subscriptions/{plan}/cancel")
async def cancel(plan: str, user: TgUser = Depends(get_tg_user), deps: Deps = Depends(get_deps)) -> dict:
    return await _toggle(plan, user, deps, cancel=True)


@router.post("/api/subscriptions/{plan}/resume")
async def resume(plan: str, user: TgUser = Depends(get_tg_user), deps: Deps = Depends(get_deps)) -> dict:
    return await _toggle(plan, user, deps, cancel=False)


# --- для твоих приложений ---------------------------------------------------------


def _app_for_key(deps: Deps, authorization: str) -> str:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Нужен заголовок Authorization: Bearer <ключ>")
    for key, app_code in deps.settings.app_keys.items():
        if hmac.compare_digest(key, token):
            return app_code
    raise HTTPException(status_code=401, detail="Неизвестный ключ приложения")


@router.get("/api/v1/access")
async def access(
    user_id: int,
    app: str,
    authorization: str = Header(default=""),
    deps: Deps = Depends(get_deps),
) -> dict:
    caller = _app_for_key(deps, authorization)
    app = app.strip().lower()
    if caller != app:
        raise HTTPException(status_code=403, detail="Этот ключ выдан другому приложению")
    if deps.catalog.get_app(app) is None:
        raise HTTPException(status_code=404, detail="Приложения нет в каталоге")

    async with deps.sessionmaker() as session:
        subs = await db.get_subscriptions(session, user_id)
    result = app_access(deps.catalog, app, subs, is_admin=user_id in deps.settings.admin_ids)
    return {"user_id": user_id} | result.to_dict()


@router.get("/health")
async def health() -> dict:
    return {"ok": True}
