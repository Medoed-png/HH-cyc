"""Конфиг gunicorn для прод-запуска (ASGI через uvicorn worker).

⚠️ workers=1 НЕ случайно: SessionManager и реестр SSE-подписчиков сейчас
in-process (M4, без Redis). Несколько воркеров = разные процессы с разными
пулами сессий, и события/сессии перестанут совпадать между запросами. Масштаб
вширь (несколько воркеров/машин) требует Redis pub/sub + распределённой очереди
(см. DEPLOY.md, отложено). До этого держим один воркер и масштабируем вертикально.
"""
import os

bind = os.environ.get("BIND", "0.0.0.0:8000")
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))  # см. предупреждение выше
worker_class = "uvicorn.workers.UvicornWorker"
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
graceful_timeout = 30
keepalive = 65  # держим SSE-соединения живыми
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")
