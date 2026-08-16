import os
import logging
from celery import Celery

logger = logging.getLogger("crack_api.celery")

# Read Redis URL from environment variables, fallback to local Redis
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "crack_tasks",
    broker=REDIS_URL,
    backend=REDIS_URL
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Ho_Chi_Minh",
    enable_utc=True,
    worker_prefetch_multiplier=1,  # Read one task at a time (important for GPU memory)
    task_acks_late=True,           # Acknowledge task after execution is finished
)

logger.info(f"Celery initialized with Redis broker/backend: {REDIS_URL}")
