# Образ с предустановленными браузерами Playwright и системными зависимостями.
# Версию тега держите в синхроне с версией пакета playwright в requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app

# Сначала зависимости — кешируется отдельно от кода.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# В контейнере браузер всегда невидимый.
ENV HH_HEADLESS=1
ENV BIND=0.0.0.0:8000
EXPOSE 8000

# При старте: применить миграции, затем поднять ASGI-приложение через gunicorn.
# (gunicorn импортирует web.server:app напрямую — main() с автооткрытием вкладки
#  не вызывается, что и нужно в проде.)
CMD ["sh", "-c", "alembic upgrade head && gunicorn web.server:app -c gunicorn_conf.py"]
