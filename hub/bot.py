"""Telegram-бот ORBIT (aiogram 3).

Бот делает немного:
  * /start — приветствие и кнопка, открывающая Mini App;
  * принимает оплату Stars: проверяет счёт перед списанием и записывает
    подписку после успешной оплаты (и каждое автопродление тоже);
  * /paysupport и /terms — Telegram требует их у ботов, принимающих Stars;
  * /refund — возврат Stars (только для администраторов из ADMIN_IDS).
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    PreCheckoutQuery,
    WebAppInfo,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from . import db
from .catalog import Catalog
from .config import Settings
from .payments import SUBSCRIPTION_PERIOD, check_pre_checkout, parse_payload

log = logging.getLogger("orbit.bot")


def _open_button(settings: Settings, text: str, plan: str | None = None) -> InlineKeyboardMarkup | None:
    if not settings.webapp_url:
        return None
    url = settings.webapp_url + (f"?plan={plan}" if plan else "")
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url))]]
    )


def _fmt_date(value: datetime) -> str:
    months = [
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    ]
    local = value.astimezone(timezone(timedelta(hours=3)))  # Москва
    return f"{local.day} {months[local.month - 1]}"


def build_router(
    catalog: Catalog,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> Router:
    router = Router(name="orbit")

    @router.message(CommandStart())
    async def start(message: Message, command: CommandObject) -> None:
        user = message.from_user
        if user is not None:
            async with sessionmaker() as session:
                await db.upsert_user(session, user.id, user.first_name, user.username)

        # /start buy_velora — пришли из приложения за подпиской.
        plan = None
        if command.args and command.args.startswith("buy_"):
            candidate = command.args[4:]
            if catalog.is_purchasable(candidate):
                plan = candidate

        name = html.escape(user.first_name) if user else "друг"
        apps = ", ".join(a.name for a in catalog.live_apps)
        text = (
            f"Привет, {name}! Это <b>{settings.hub_name}</b> — все мои приложения в одном месте.\n\n"
            f"Внутри: {apps}. Подписка на каждое отдельно или на всё сразу — "
            f"«{catalog.bundle.name}» за {catalog.bundle.price_stars} ⭐ в месяц."
        )
        markup = _open_button(settings, f"Открыть {settings.hub_name}", plan)
        if markup is None:
            text += "\n\n⚠️ Mini App ещё не настроена: задай PUBLIC_URL в настройках сервера."
        await message.answer(text, reply_markup=markup)

    @router.message(Command("help"))
    async def help_cmd(message: Message) -> None:
        await message.answer(
            f"<b>{settings.hub_name}</b> — один вход во все приложения и одна подписка.\n\n"
            "• Открой Mini App кнопкой меню внизу слева.\n"
            "• Нажми на приложение, чтобы перейти в него.\n"
            "• Подписку можно отменить в любой момент — доступ сохранится до конца оплаченного месяца.\n\n"
            "/paysupport — вопросы по оплате\n/terms — условия"
        )

    @router.message(Command("paysupport"))
    async def paysupport(message: Message) -> None:
        contact = settings.support_contact or "этот чат — просто напиши сообщение"
        await message.answer(
            "Вопросы по оплате и возвратам: " + html.escape(contact) + ".\n"
            "Укажи, какую подписку оформлял и когда — разберёмся в течение суток."
        )

    @router.message(Command("terms"))
    async def terms(message: Message) -> None:
        await message.answer(
            "<b>Условия подписки</b>\n\n"
            "• Подписка оплачивается в Telegram Stars и продлевается каждые 30 дней.\n"
            "• Отменить продление можно в ORBIT («Мои подписки») или в настройках Telegram.\n"
            "• После отмены доступ сохраняется до конца оплаченного периода.\n"
            "• Возврат за текущий период — по запросу через /paysupport."
        )

    @router.message(Command("refund"))
    async def refund(message: Message, command: CommandObject, bot: Bot) -> None:
        if message.from_user is None or message.from_user.id not in settings.admin_ids:
            return
        parts = (command.args or "").split()
        if len(parts) != 2 or not parts[0].isdigit():
            await message.answer("Формат: /refund <user_id> <charge_id>")
            return
        try:
            await bot.refund_star_payment(user_id=int(parts[0]), telegram_payment_charge_id=parts[1])
        except Exception as exc:  # показываем админу причину как есть
            await message.answer(f"Не получилось: {html.escape(str(exc))}")
            return
        await message.answer("Готово, Stars возвращены.")

    @router.pre_checkout_query()
    async def pre_checkout(query: PreCheckoutQuery) -> None:
        error = check_pre_checkout(
            catalog, query.invoice_payload, query.from_user.id, query.currency, query.total_amount
        )
        if error:
            await query.answer(ok=False, error_message=error)
        else:
            await query.answer(ok=True)

    @router.message(F.successful_payment)
    async def paid(message: Message) -> None:
        sp = message.successful_payment
        payload = parse_payload(sp.invoice_payload)
        if payload is None or message.from_user is None:
            log.warning("Оплата с неизвестным payload: %s", sp.invoice_payload)
            return

        if sp.subscription_expiration_date:
            expires = datetime.fromtimestamp(sp.subscription_expiration_date, tz=timezone.utc)
        else:
            expires = datetime.now(timezone.utc) + timedelta(seconds=SUBSCRIPTION_PERIOD)

        async with sessionmaker() as session:
            created = await db.record_payment(
                session,
                user_id=message.from_user.id,
                plan=payload.plan,
                charge_id=sp.telegram_payment_charge_id,
                amount_stars=sp.total_amount,
                expires_at=expires,
                is_recurring=bool(sp.is_recurring),
                is_first_recurring=bool(sp.is_first_recurring),
            )
        if not created:
            return

        title = catalog.plan_title(payload.plan)
        if sp.is_recurring and not sp.is_first_recurring:
            text = f"🔁 Подписка «{html.escape(title)}» продлена до {_fmt_date(expires)}."
        else:
            text = (
                f"✅ Готово! «{html.escape(title)}» активна до {_fmt_date(expires)}.\n"
                "Продление — автоматически раз в 30 дней, отменить можно в любой момент."
            )
        await message.answer(text, reply_markup=_open_button(settings, "Открыть приложения"))

    return router
