# Подключение MERA, VELORA и DELTA к ORBIT

Каждое приложение остаётся отдельным ботом со своей базой. Нужно только
научить его спрашивать у ORBIT: «есть ли у этого человека подписка?».
Для этого — один файл `hub_access.py`, без новых библиотек.

## Шаг 1. Придумай ключ для приложения

В терминале:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
```

Получится строка вроде `p9Qx...`. Сделай так три раза — по ключу на приложение.

## Шаг 2. Пропиши ключи в ORBIT

На Railway → сервис ORBIT → **Variables**:

```
HUB_APP_KEYS=mera:КЛЮЧ_MERA,velora:КЛЮЧ_VELORA,delta:КЛЮЧ_DELTA
```

Ключ VELORA может спрашивать только про VELORA — даже если он утечёт,
по нему не узнать ничего про MERA или DELTA.

## Шаг 3. Скопируй файл в приложение

Положи `hub_access.py` рядом с главным файлом бота (там же, где `bot.py`
или `main.py`) в репозитории приложения.

## Шаг 4. Переменные приложения

В Variables самого приложения (VELORA — на Railway, MERA — на Amvera):

| Переменная     | VELORA                         | DELTA                          | MERA                           |
|----------------|--------------------------------|--------------------------------|--------------------------------|
| `HUB_URL`      | адрес ORBIT на Railway         | так же                         | так же                         |
| `HUB_APP_CODE` | `velora`                       | `delta`                        | `mera`                         |
| `HUB_API_KEY`  | КЛЮЧ_VELORA                    | КЛЮЧ_DELTA                     | КЛЮЧ_MERA                      |
| `HUB_BOT_LINK` | `https://t.me/имя_бота_orbit`  | так же                         | так же                         |

## Шаг 5. Проверка в коде

В боте (aiogram):

```python
from hub_access import hub

@router.message(Command("coach"))
async def coach(message: Message):
    access = await hub.acheck(message.from_user.id)
    if not access.active:
        await message.answer(
            "ИИ-коуч доступен по подписке.",
            reply_markup=hub.paywall_keyboard(),
        )
        return
    ...  # подписка есть — работаем
```

В API Mini App (FastAPI):

```python
from hub_access import hub

@app.get("/api/premium-report")
async def premium_report(user_id: int = Depends(current_user_id)):
    access = await hub.acheck(user_id)
    if not access.active:
        return {"paywall": hub.paywall_link()}
    ...
```

`hub.paywall_link()` открывает бота ORBIT сразу на оплате этого приложения.

## Что закрывать подпиской — решаешь ты

ORBIT только отвечает «да/нет». Что именно платное, выбираешь в каждом
приложении сам. Хорошее правило: базовое пользование бесплатно,
подписка открывает то, что даёт заметную пользу. Например:

- **VELORA** — бесплатно до 3 привычек; по подписке ИИ-коуч, голос и недельные отчёты;
- **DELTA** — бесплатно учёт в рублях; по подписке валюты и крипта, ярлык iPhone, общий бюджет;
- **MERA** — бесплатно для клиентов всегда; для мастеров по подписке автопилот и статистика.

## Если ORBIT недоступен

Ответы кешируются: «есть доступ» — на 2 минуты, «нет доступа» — на 10 секунд
(чтобы после оплаты доступ появился почти сразу). Если ORBIT не отвечает,
используется последний известный ответ. Если его нет — по умолчанию доступ
закрыт; чтобы в такой ситуации пускать людей, задай `HUB_FAIL_OPEN=1`.
