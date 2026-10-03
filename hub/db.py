"""База данных ORBIT (SQLAlchemy 2.0, асинхронно).

Таблицы:
  users          — кто хоть раз открывал ORBIT;
  subscriptions  — одна строка на пару (пользователь, план): до какого числа
                   оплачено и включено ли автопродление;
  payments       — журнал всех оплат (и первых, и продлений).

Локально всё хранится в файле orbit.db (SQLite), на Railway — в PostgreSQL.
Таблицы создаются сами при первом запуске.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
    select,
)
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .access import SubRecord


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    """SQLite возвращает время без часового пояса — считаем его UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    first_name: Mapped[str | None] = mapped_column(String(128))
    username: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("user_id", "plan", name="uq_subscription_user_plan"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    plan: Mapped[str] = mapped_column(String(32))
    # charge_id первой оплаты — нужен Telegram для отмены/возобновления продления.
    charge_id: Mapped[str] = mapped_column(String(255))
    last_charge_id: Mapped[str] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    def to_record(self) -> SubRecord:
        return SubRecord(
            plan=self.plan,
            expires_at=_aware(self.expires_at),
            auto_renew=self.auto_renew,
            charge_id=self.charge_id,
        )


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    charge_id: Mapped[str] = mapped_column(String(255), unique=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    plan: Mapped[str] = mapped_column(String(32))
    amount_stars: Mapped[int] = mapped_column(Integer)
    is_recurring: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# --- подключение ----------------------------------------------------------------


def make_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# --- операции -------------------------------------------------------------------


async def upsert_user(
    session: AsyncSession, user_id: int, first_name: str | None, username: str | None
) -> None:
    user = await session.get(User, user_id)
    if user is None:
        session.add(User(id=user_id, first_name=first_name, username=username))
    else:
        user.first_name = first_name
        user.username = username
        user.last_seen_at = _utcnow()
    await session.commit()


async def get_subscriptions(session: AsyncSession, user_id: int) -> list[SubRecord]:
    rows = await session.scalars(select(Subscription).where(Subscription.user_id == user_id))
    return [row.to_record() for row in rows]


async def record_payment(
    session: AsyncSession,
    *,
    user_id: int,
    plan: str,
    charge_id: str,
    amount_stars: int,
    expires_at: datetime,
    is_recurring: bool,
    is_first_recurring: bool,
) -> bool:
    """Записывает оплату и продлевает подписку.

    Возвращает False, если эта оплата уже была записана (Telegram иногда
    присылает одно и то же событие повторно — второй раз ничего не делаем).
    """
    exists = await session.scalar(select(Payment.id).where(Payment.charge_id == charge_id))
    if exists is not None:
        return False

    expires_at = _aware(expires_at)
    session.add(
        Payment(
            charge_id=charge_id,
            user_id=user_id,
            plan=plan,
            amount_stars=amount_stars,
            is_recurring=is_recurring,
            expires_at=expires_at,
        )
    )

    sub = await session.scalar(
        select(Subscription).where(Subscription.user_id == user_id, Subscription.plan == plan)
    )
    if sub is None:
        session.add(
            Subscription(
                user_id=user_id,
                plan=plan,
                charge_id=charge_id,
                last_charge_id=charge_id,
                expires_at=expires_at,
                auto_renew=True,
            )
        )
    else:
        if is_first_recurring or not is_recurring:
            # Новая цепочка подписки (например, оформил заново после окончания).
            sub.charge_id = charge_id
        sub.last_charge_id = charge_id
        sub.expires_at = max(_aware(sub.expires_at), expires_at)
        sub.auto_renew = True
    await session.commit()
    return True


async def set_auto_renew(session: AsyncSession, user_id: int, plan: str, value: bool) -> str | None:
    """Меняет флаг автопродления. Возвращает charge_id или None, если подписки нет."""
    sub = await session.scalar(
        select(Subscription).where(Subscription.user_id == user_id, Subscription.plan == plan)
    )
    if sub is None:
        return None
    sub.auto_renew = value
    await session.commit()
    return sub.charge_id


async def find_charge(session: AsyncSession, user_id: int, plan: str) -> str | None:
    sub = await session.scalar(
        select(Subscription).where(Subscription.user_id == user_id, Subscription.plan == plan)
    )
    return sub.charge_id if sub else None
