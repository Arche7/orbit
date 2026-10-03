"""Telegram-бот ORBIT (aiogram 3).

Что делает бот:
  * /start — премиальное приветствие: короткое видео-заставка, кто мы и что
    внутри, кнопки «Открыть ORBIT», «Как это работает», «Тарифы».
    Новому человеку — знакомство, вернувшемуся — его активные подписки;
  * «Как это работает» (/help) — карусель из 4 слайдов, листается стрелками
    в одном сообщении;
  * «Тарифы» (/plans) — цены и кнопки оформления каждой подписки;
  * принимает оплату Stars: проверяет счёт перед списанием и записывает
    подписку после успешной оплаты (и каждое автопродление тоже);
  * /paysupport и /terms — Telegram требует их у ботов, принимающих Stars;
  * /refund — возврат Stars (только для администраторов из ADMIN_IDS).

Картинки и видео лежат в hub/assets. Первый раз бот загружает файл в Telegram,
а потом отправляет его по file_id — мгновенно и без повторной загрузки.
"""

from __future__ import annotations

import contextlib
import html
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
    PreCheckoutQuery,
    WebAppInfo,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from . import db, texts
from .access import is_sub_active
from .catalog import Catalog
from .config import Settings
from .payments import SUBSCRIPTION_PERIOD, check_pre_checkout, parse_payload

log = logging.getLogger("orbit.bot")

ASSETS_DIR = Path(__file__).resolve().parent / "assets"


class MediaCache:
    """Помнит file_id уже загруженных в Telegram файлов."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._ids: dict[str, str] = {}

    def exists(self, name: str) -> bool:
        return name in self._ids or (self.folder / name).is_file()

    def get(self, name: str) -> str | FSInputFile:
        return self._ids.get(name) or FSInputFile(self.folder / name)

    def remember(self, name: str, message: Message | bool | None) -> None:
        if not isinstance(message, Message):
            return
        if message.photo:
            self._ids[name] = message.photo[-1].file_id
        elif message.animation:
            self._ids[name] = message.animation.file_id
        elif message.video:
            self._ids[name] = message.video.file_id


def build_router(
    catalog: Catalog,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    assets_dir: Path = ASSETS_DIR,
) -> Router:
    router = Router(name="orbit")
    media = MediaCache(assets_dir)
    hub = settings.hub_name
    tour = texts.tour_captions(catalog, hub)
    slides = [s for s in texts.TOUR_SLIDES if media.exists(s)]
    tour_ready = len(slides) == len(tour)

    # --- кнопки ------------------------------------------------------------

    def open_button(text: str | None = None, plan: str | None = None) -> InlineKeyboardButton | None:
        if not settings.webapp_url:
            return None
        url = settings.webapp_url + (f"?plan={plan}" if plan else "")
        return InlineKeyboardButton(text=text or f"🪐 Открыть {hub}", web_app=WebAppInfo(url=url))

    def keyboard(*rows: list[InlineKeyboardButton | None]) -> InlineKeyboardMarkup:
        clean = [[b for b in row if b is not None] for row in rows]
        return InlineKeyboardMarkup(inline_keyboard=[row for row in clean if row])

    def main_keyboard() -> InlineKeyboardMarkup:
        return keyboard(
            [open_button()],
            [
                InlineKeyboardButton(text="✦ Как это работает", callback_data="tour:0"),
                InlineKeyboardButton(text="⭐ Тарифы", callback_data="plans"),
            ],
        )

    def buy_keyboard(plan: str) -> InlineKeyboardMarkup:
        title = catalog.plan_title(plan)
        return keyboard(
            [open_button(f"Оформить «{title}» · {catalog.plan_price(plan)} ⭐", plan)],
            [
                InlineKeyboardButton(text="✦ Как это работает", callback_data="tour:0"),
                InlineKeyboardButton(text="⭐ Все тарифы", callback_data="plans"),
            ],
        )

    def tour_keyboard(i: int) -> InlineKeyboardMarkup:
        n = len(tour)
        return keyboard(
            [
                InlineKeyboardButton(text="‹", callback_data=f"tour:{(i - 1) % n}"),
                InlineKeyboardButton(text=f"{i + 1} / {n}", callback_data="noop"),
                InlineKeyboardButton(text="›", callback_data=f"tour:{(i + 1) % n}"),
            ],
            [open_button()],
            [
                InlineKeyboardButton(text="⭐ Тарифы", callback_data="plans"),
                InlineKeyboardButton(text="✕ Закрыть", callback_data="tour:close"),
            ],
        )

    def plans_keyboard() -> InlineKeyboardMarkup:
        rows = [[open_button(label, code)] for code, label in texts.plan_button_labels(catalog)]
        return keyboard(*rows)

    # --- отправка с запасным вариантом -----------------------------------

    async def send_welcome(message: Message, caption: str, markup: InlineKeyboardMarkup) -> None:
        """Видео-заставка с подписью; если видео нет или не ушло — просто текст."""
        if media.exists(texts.WELCOME_VIDEO):
            try:
                sent = await message.answer_animation(
                    animation=media.get(texts.WELCOME_VIDEO),
                    caption=caption,
                    reply_markup=markup,
                    width=1280,
                    height=720,
                )
                media.remember(texts.WELCOME_VIDEO, sent)
                return
            except Exception:  # не валим /start из-за картинки
                log.exception("Не удалось отправить видео-приветствие, шлю текст")
        await message.answer(caption, reply_markup=markup)

    async def send_tour(message: Message, i: int) -> None:
        if tour_ready:
            try:
                sent = await message.answer_photo(
                    photo=media.get(slides[i]), caption=tour[i], reply_markup=tour_keyboard(i)
                )
                media.remember(slides[i], sent)
                return
            except Exception:  # слайд не ушёл — показываем тот же текст без картинки
                log.exception("Не удалось отправить слайд, шлю текст")
        await message.answer(tour[i], reply_markup=tour_keyboard(i))

    async def active_plans(user_id: int) -> list[texts.ActivePlan]:
        async with sessionmaker() as session:
            subs = await db.get_subscriptions(session, user_id)
        now = datetime.now(timezone.utc)
        return [
            texts.ActivePlan(catalog.plan_title(s.plan), s.expires_at, s.auto_renew)
            for s in sorted(subs, key=lambda s: s.expires_at)
            if is_sub_active(s, now)
        ]

    # --- команды -----------------------------------------------------------

    @router.message(CommandStart())
    async def start(message: Message, command: CommandObject) -> None:
        user = message.from_user
        is_new = True
        if user is not None:
            async with sessionmaker() as session:
                is_new = await db.upsert_user(session, user.id, user.first_name, user.username)
        first_name = user.first_name if user else None

        # /start buy_velora — пришли из приложения за подпиской.
        if command.args and command.args.startswith("buy_") and catalog.is_purchasable(command.args[4:]):
            plan = command.args[4:]
            await send_welcome(message, texts.welcome_buy(catalog, first_name, plan), buy_keyboard(plan))
            return

        if is_new or user is None:
            caption = texts.welcome_new(catalog, hub, first_name)
        else:
            caption = texts.welcome_back(
                catalog, hub, first_name, await active_plans(user.id),
                is_admin=user.id in settings.admin_ids,
            )
        if not settings.webapp_url:
            caption += "\n\n⚠️ Mini App ещё не настроена: задай PUBLIC_URL в настройках сервера."
        await send_welcome(message, caption, main_keyboard())

    @router.message(Command("help"))
    async def help_cmd(message: Message) -> None:
        await send_tour(message, 0)
        await message.answer(texts.help_text(hub))

    @router.message(Command("plans"))
    async def plans_cmd(message: Message) -> None:
        await message.answer(texts.plans_text(catalog), reply_markup=plans_keyboard())

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
            f"• Отменить продление можно в {html.escape(hub)} («Мои подписки») или в настройках Telegram.\n"
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

    # --- кнопки под сообщениями --------------------------------------------

    @router.callback_query(F.data == "noop")
    async def noop(query: CallbackQuery) -> None:
        await query.answer()

    @router.callback_query(F.data == "plans")
    async def plans_cb(query: CallbackQuery) -> None:
        await query.answer()
        if isinstance(query.message, Message):
            await query.message.answer(texts.plans_text(catalog), reply_markup=plans_keyboard())

    @router.callback_query(F.data.startswith("tour:"))
    async def tour_cb(query: CallbackQuery) -> None:
        msg = query.message
        arg = (query.data or "").split(":", 1)[1]
        if not isinstance(msg, Message):
            await query.answer()
            return
        if arg == "close":
            await query.answer()
            try:
                await msg.delete()
            except Exception:  # старое сообщение уже не удалить — просто убираем кнопки
                with contextlib.suppress(Exception):
                    await msg.edit_reply_markup(reply_markup=None)
            return
        if not arg.isdigit() or int(arg) >= len(tour):
            await query.answer()
            return
        i = int(arg)
        await query.answer()

        # Нажали «Как это работает» под приветствием — присылаем карусель отдельным сообщением.
        # Нажали стрелку внутри карусели — листаем это же сообщение.
        if msg.reply_markup and any(b.callback_data == "tour:close" for row in msg.reply_markup.inline_keyboard for b in row):
            try:
                if tour_ready and msg.photo:
                    edited = await msg.edit_media(
                        media=InputMediaPhoto(media=media.get(slides[i]), caption=tour[i]),
                        reply_markup=tour_keyboard(i),
                    )
                    media.remember(slides[i], edited)
                else:
                    await msg.edit_text(tour[i], reply_markup=tour_keyboard(i))
                return
            except Exception as exc:
                # Двойное нажатие: второй раз Telegram отвечает «message is not modified» —
                # это не ошибка, новое сообщение не нужно.
                if "not modified" in str(exc).lower():
                    return
                log.exception("Не удалось перелистнуть слайд, отправляю новым сообщением")
        await send_tour(msg, i)

    # --- оплата --------------------------------------------------------------

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

        renewal = bool(sp.is_recurring and not sp.is_first_recurring)
        text = texts.paid_text(catalog.plan_title(payload.plan), expires, renewal)
        await message.answer(text, reply_markup=keyboard([open_button("🪐 Открыть приложения")]))

    return router
