import json
import redis.asyncio as redis # Using async redis for FastAPI
from app.config import settings

class RealtimeNotifier:
    def __init__(self):
        self.redis_url = settings.REDIS_URL
        self._redis = None

    async def get_redis(self):
        if self._redis is None:
            # v8.0: Tight timeouts to prevent system-wide hangs in high-latency network (Tailscale)
            self._redis = redis.from_url(
                self.redis_url, 
                decode_responses=True,
                socket_timeout=1.0, 
                socket_connect_timeout=1.0,
                retry_on_timeout=False
            )
        return self._redis

    async def emit(
        self,
        task_id: str,
        status: str,
        progress: int = 0,
        message: str = "",
        **metadata,
    ):
        """
        Publish an update for a specific task to Redis.
        Non-blocking: Fires in the background.
        """
        try:
            import asyncio
            # FIRE AND FORGET: Do not wait for Redis in the main loop
            asyncio.create_task(
                self._do_emit(task_id, status, progress, message, metadata)
            )
        except Exception as e:
            print(f"[NOTIFIER ERROR] Could not spawn background emit: {e}")

    async def _do_emit(
        self,
        task_id: str,
        status: str,
        progress: int = 0,
        message: str = "",
        metadata: dict | None = None,
    ):
        try:
            r = await self.get_redis()
            payload = {
                "task_id": task_id,
                "status": status,
                "progress": progress,
                "message": message,
                "timestamp": str(anyio_datetime_now()) if 'anyio_datetime_now' in globals() else "" # Just metadata
            }
            # Preserve optional telemetry (ETA/FPS/counts) for websocket
            # consumers.  Do not allow callers to overwrite routing fields.
            if metadata:
                payload.update({
                    key: value for key, value in metadata.items()
                    if key not in {"task_id", "status", "progress", "message", "timestamp"}
                })
            # Add current time
            from datetime import datetime
            payload["timestamp"] = datetime.now().isoformat()
            
            # Channel name is unique per task to avoid broad noise
            channel = f"task_updates:{task_id}"
            await r.publish(channel, json.dumps(payload))
            
            # Fallback global channel for general monitoring
            await r.publish("task_updates:global", json.dumps(payload))
            
            print(f"[NOTIFIER] Emitted update for {task_id}: {status} ({progress}%)")
        except Exception as e:
            print(f"[NOTIFIER ERROR] Failed to emit: {e}")

notifier = RealtimeNotifier()
