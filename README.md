# Cargo Bots

Backend для двух Telegram-ботов cargo:

- `admin_bot` принимает Excel-файлы от администратора, сохраняет оригинал, создаёт import job и запускает обработку.
- `client_bot` регистрирует клиентов, показывает профиль, персональный адрес в Китае и статусы товаров.

## Стек

- `FastAPI` + webhook endpoints
- `aiogram 3` для обоих Telegram-ботов
- `PostgreSQL` + `SQLAlchemy 2 async`
- `Redis` для Celery и FSM storage
- `Celery` для фонового импорта и отправки уведомлений
- `pandas` + `calamine` для чтения `.xls/.xlsx`
- `S3-compatible storage` или локальное storage для оригиналов Excel

## Структура

- `src/cargo_bots/main.py` — FastAPI app factory
- `src/cargo_bots/bots/` — aiogram handlers, menus, runtime
- `src/cargo_bots/services/` — бизнес-логика регистрации, импорта, парсинга и уведомлений
- `src/cargo_bots/tasks/` — Celery app и worker jobs
- `src/cargo_bots/tools/import_legacy.py` — загрузка старых клиентов из CSV

## Быстрый старт

1. Создайте и активируйте виртуальное окружение.
2. Установите зависимости:

```bash
pip install -e .
```

3. Скопируйте `.env.example` в `.env` и заполните:

- `APP_ROLE`
- `ADMIN_BOT_TOKEN`
- `CLIENT_BOT_TOKEN`
- `DATABASE_URL`
- `REDIS_URL`
- `WEBHOOK_BASE_URL`

`ADMIN_IDS` можно оставить пустым, если админ-бот должен быть доступен всем, кто напишет ему в Telegram. Если нужен whitelist, укажите Telegram user id через запятую.

4. Поднимите PostgreSQL и Redis.
5. Запустите API:

```bash
uvicorn cargo_bots.main:app --host 0.0.0.0 --port 8000
```

6. Запустите Celery worker:

```bash
celery -A cargo_bots.tasks.celery_app:celery_app worker -l info
```

Для единой стартовой команды можно использовать launcher:

```bash
APP_ROLE=combined_web python -m cargo_bots.run
```

## Импорт старых клиентов

CSV должен содержать колонки:

```text
client_code,full_name,phone,notes
```

Запуск:

```bash
python -m cargo_bots.tools.import_legacy path/to/legacy_clients.csv
```

## Webhooks

Приложение ожидает два webhook endpoint:

- `/webhook/admin`
- `/webhook/client`

Если `WEBHOOK_BASE_URL` задан, на старте приложение само регистрирует webhook для обоих ботов.

`ADMIN_SECRET_TOKEN` и `CLIENT_SECRET_TOKEN` не связаны с ролями пользователей. Это отдельные секреты для проверки входящих webhook-запросов Telegram к `/webhook/admin` и `/webhook/client`.

## Railway

Если хотите запускать всё раздельно, создайте три Railway service из одного репозитория:

- `admin-web`
- `client-web`
- `worker`

Во всех трёх:

- `Root Directory` -> `/`
- `Start Command` можно не задавать вручную, потому что он уже есть в `railway.toml`
- если Railway не подхватил его автоматически, укажите вручную: `PYTHONPATH=src python -m cargo_bots.run`

Задайте только разный `APP_ROLE`:

- `admin-web` -> `APP_ROLE=admin_web`
- `client-web` -> `APP_ROLE=client_web`
- `worker` -> `APP_ROLE=worker`

Для `admin-web` задайте `WEBHOOK_BASE_URL` на публичный домен именно этого сервиса. Тогда бот зарегистрирует webhook `https://.../webhook/admin`.

Для `client-web` задайте `WEBHOOK_BASE_URL` на публичный домен именно этого сервиса. Тогда бот зарегистрирует webhook `https://.../webhook/client`.

Для `worker` `WEBHOOK_BASE_URL` не нужен.

## Админ-бот

Поддерживаемые команды:

- `/start`
- `/upload`
- `/imports`
- `/unmatched`
- `/stats`

Также админ может просто отправить `.xls` или `.xlsx` документ.

## Клиент-бот

Основные сценарии:

- привязка существующего клиента по коду `J-1234` и ФИО
- регистрация нового клиента с автоматической выдачей следующего кода
- просмотр профиля
- просмотр товаров
- просмотр адреса в Китае

## Примечания по эксплуатации

- Для production лучше использовать `Uvicorn` workers за reverse proxy.
- Для локальной разработки можно оставить `STORAGE_BACKEND=local`.
- Нераспознанные строки сохраняются в `unmatched_import_rows`.
- Повторный импорт того же трека не создаёт дубликат посылки.
