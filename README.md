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

## Структура проекта

```
├── src/cargo_bots/          # Основной Python-пакет
│   ├── main.py              # Combined app (оба бота)
│   ├── admin_app.py         # Только admin бот
│   ├── client_app.py        # Только client бот
│   ├── app_factory.py       # FastAPI app factory
│   ├── bots/                # aiogram handlers, menus, runtime
│   ├── services/            # Бизнес-логика
│   ├── tasks/               # Celery app и worker jobs
│   ├── core/                # Конфиг, логирование
│   └── db/                  # SQLAlchemy модели и сессии
│
├── admin-web/               # Railway сервис: admin бот
│   ├── Dockerfile
│   └── railway.toml
│
├── client-web/              # Railway сервис: client бот
│   ├── Dockerfile
│   └── railway.toml
│
├── worker/                  # Railway сервис: Celery worker
│   ├── Dockerfile
│   └── railway.toml
│
├── requirements.txt         # Python зависимости (общие)
├── pyproject.toml           # Настройки проекта
├── .env.example             # Шаблон переменных окружения
├── .env.production.example  # Шаблон для продакшна (Railway)
└── example_adress.txt       # Шаблон адреса в Китае
```

## Быстрый старт (локальная разработка)

1. Создайте и активируйте виртуальное окружение.
2. Установите зависимости:

```bash
pip install -e .
```

3. Скопируйте `.env.example` в `.env.development` и заполните:

```bash
cp .env.example .env.development
```

Обязательные переменные:
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

## ENV файлы

| Файл | Назначение | Коммитить? |
|------|-----------|------------|
| `.env.example` | Шаблон со всеми переменными и комментариями | ✅ Да |
| `.env.production.example` | Шаблон для Railway (все секреты пустые) | ✅ Да |
| `.env.development` | Локальные dev-значения (ваши токены, БД) | ❌ Нет |

На Railway переменные задаются **не через файлы**, а через **Dashboard → Variables**.

## Railway деплой

Проект разбит на три Railway-сервиса из одного репозитория. Каждый сервис использует свой Dockerfile.

### Настройка каждого сервиса

| Сервис | Root Directory | Dockerfile Path | WEBHOOK_BASE_URL |
|--------|---------------|-----------------|------------------|
| admin-web | `/` (корень) | `admin-web/Dockerfile` | Домен этого сервиса |
| client-web | `/` (корень) | `client-web/Dockerfile` | Домен этого сервиса |
| worker | `/` (корень) | `worker/Dockerfile` | Не нужен |

### Шаги для каждого сервиса:

1. Создайте сервис в Railway из репозитория
2. **Settings → Source → Root Directory**: оставьте `/` (корень)
3. **Settings → Build → Dockerfile Path**: укажите путь (см. таблицу)
4. **Variables**: задайте все переменные из `.env.production.example`
5. Для `admin-web` и `client-web`: установите `WEBHOOK_BASE_URL` = сгенерированный Railway домен

### Общие переменные (задаются через Shared Variables или для каждого сервиса):

- `DATABASE_URL` — из Railway PostgreSQL плагина
- `REDIS_URL` — из Railway Redis плагина
- `ADMIN_BOT_TOKEN`, `CLIENT_BOT_TOKEN` — токены ботов
- `STORAGE_BACKEND=s3` и S3-ключи
- `ENV=production`

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
