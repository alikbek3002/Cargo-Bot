FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

WORKDIR /app

# Копируем и устанавливаем зависимости отдельным слоем для кэша
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Копируем исходный код и ресурсы
COPY src ./src
COPY example_adress.txt ./example_adress.txt

CMD ["sh", "-c", "celery -A cargo_bots.tasks.celery_app:celery_app worker -l ${CELERY_LOG_LEVEL:-info}"]
