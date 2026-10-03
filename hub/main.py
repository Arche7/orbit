"""Точка входа ORBIT: один процесс = веб-сервер + Telegram-бот.

Запуск локально:   uvicorn hub.main:create_app --factory --reload
На Railway:        команда из railway.json (то же самое, на порту $PORT)

Что происходит при старте:
  1. читаем настройки и каталог (если в catalog.json ошибка — сразу видно);
  2. создаём таблицы в базе, если их ещё нет;
  3. запускаем бота (long polling) в фоне;
  4. ставим кнопку меню бота, открывающую Mini App;
  5. отдаём Mini App по адресу /app/ и API по адресам /api/... .
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .api import Deps, router as api_router
from .catalog import load_catalog
from .config import BASE_DIR, Settings, load_settings

log = logging.getLogger("orbit")


def create_app(settings: Settings | None = None, bot=None, run_polling: bool = True) -> FastAPI:
    """Собирает приложение. В тестах сюда передают свои settings и заглушку бота."""
    settings = settings or load_settings()
    catalog = load_catalog()
    engine = db.make_engine(settings.database_url)
    sessionmaker = db.make_sessionmaker(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await db.init_db(engine)

        tg_bot = bot
        polling_task: asyncio.Task | None = None
        dispatcher = None
        if tg_bot is None:
            from aiogram import Bot, Dispatcher
            from aiogram.client.default import DefaultBotProperties
            from aiogram.enums import ParseMode

            from .bot import build_router

            tg_bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
            dispatcher = Dispatcher()
            dispatcher.include_router(build_router(catalog, sessionmaker, settings))

        app.state.deps = Deps(catalog=catalog, settings=settings, sessionmaker=sessionmaker, bot=tg_bot)

        if dispatcher is not None:
            await _setup_menu(tg_bot, settings)
            if run_polling:
                polling_task = asyncio.create_task(
                    dispatcher.start_polling(tg_bot, handle_signals=False)
                )
                log.info("Бот запущен, Mini App: %s", settings.webapp_url or "PUBLIC_URL не задан")

        try:
            yield
        finally:
            if polling_task is not None:
                with contextlib.suppress(Exception):
                    await dispatcher.stop_polling()
                polling_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await polling_task
            if dispatcher is not None:
                await tg_bot.session.close()
            await engine.dispose()

    app = FastAPI(title=settings.hub_name, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.include_router(api_router)
    app.mount("/app", StaticFiles(directory=BASE_DIR / "webapp", html=True), name="webapp")

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/app/")

    return app


async def _setup_menu(bot, settings: Settings) -> None:
    """Кнопка меню слева от поля ввода в чате с ботом — открывает Mini App."""
    if not settings.webapp_url:
        log.warning("PUBLIC_URL не задан — кнопка меню бота не настроена")
        return
    from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text=settings.hub_name, web_app=WebAppInfo(url=settings.webapp_url))
        )
        await bot.set_my_commands(
            [
                BotCommand(command="start", description=f"Открыть {settings.hub_name}"),
                BotCommand(command="help", description="Как это работает"),
                BotCommand(command="paysupport", description="Вопросы по оплате"),
                BotCommand(command="terms", description="Условия подписки"),
            ]
        )
    except Exception as exc:  # не валим сервер, если Telegram временно недоступен
        log.warning("Не удалось настроить меню бота: %s", exc)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
