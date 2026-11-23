"""Logs estructurados en JSON con correlation_id, ambiente, servicio y track.

Cada línea queda en stdout (kubectl logs) y, si CLOUDWATCH_LOGS=true, se envía
a CloudWatch Logs en el grupo /aegisvolt/<env>/<service>. El correlation_id de
una SAGA es su saga_id, así que con un solo filtro en CloudWatch se ve el
recorrido completo de la transacción por los tres microservicios:

    aws logs filter-log-events --log-group-name-prefix /aegisvolt/test/ \
        --filter-pattern '{ $.correlation_id = "<saga_id>" }'
"""

from __future__ import annotations

import contextvars
import json
import logging
import queue
import socket
import sys
import threading
import time
from datetime import UTC, datetime

from .config import Settings

correlation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("correlation_id", default=None)

_RESERVED = set(logging.LogRecord("x", 0, "x", 0, "x", None, None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "service": self.settings.service_name,
            "environment": self.settings.environment,
            "version": self.settings.app_version,
            "track": self.settings.deploy_track,
            "correlation_id": getattr(record, "correlation_id", None) or correlation_id_var.get(),
            "message": record.getMessage(),
            "logger": record.name,
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class CloudWatchHandler(logging.Handler):
    """Envía los logs a CloudWatch en lotes desde un hilo en segundo plano.

    Si CloudWatch no responde, el servicio sigue funcionando: los logs siguen
    en stdout. Nunca debe tumbar una transacción por un problema de logging.
    """

    def __init__(self, settings: Settings, flush_seconds: float = 2.0) -> None:
        super().__init__()
        from . import aws

        self.settings = settings
        self.client = aws.client(settings, "logs")
        self.group = settings.log_group
        self.stream = f"{socket.gethostname()}-{settings.deploy_track}-{datetime.now(UTC):%Y%m%d}"
        self.queue: queue.Queue[dict] = queue.Queue(maxsize=10_000)
        self.flush_seconds = flush_seconds
        self._ready = False
        threading.Thread(target=self._run, name="cloudwatch-logs", daemon=True).start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait({"timestamp": int(record.created * 1000), "message": self.format(record)})
        except queue.Full:
            pass

    def _ensure_stream(self) -> None:
        if self._ready:
            return
        for call, kwargs in (
            (self.client.create_log_group, {"logGroupName": self.group}),
            (self.client.create_log_stream, {"logGroupName": self.group, "logStreamName": self.stream}),
        ):
            try:
                call(**kwargs)
            except self.client.exceptions.ResourceAlreadyExistsException:
                pass
        self._ready = True

    def _run(self) -> None:
        while True:
            time.sleep(self.flush_seconds)
            batch: list[dict] = []
            while not self.queue.empty() and len(batch) < 500:
                batch.append(self.queue.get_nowait())
            if not batch:
                continue
            try:
                self._ensure_stream()
                self.client.put_log_events(logGroupName=self.group, logStreamName=self.stream, logEvents=sorted(batch, key=lambda e: e["timestamp"]))
            except Exception as exc:  # noqa: BLE001 - el logging nunca debe romper el servicio
                self._ready = False
                print(json.dumps({"level": "WARNING", "message": f"CloudWatch no disponible: {exc}"}), file=sys.stderr)


def configure_logging(settings: Settings) -> logging.Logger:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)
    stdout = logging.StreamHandler(sys.stdout)
    stdout.setFormatter(JsonFormatter(settings))
    root.addHandler(stdout)
    if settings.cloudwatch_logs:
        cw = CloudWatchHandler(settings)
        cw.setFormatter(JsonFormatter(settings))
        root.addHandler(cw)
    for noisy in ("botocore", "boto3", "urllib3", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").disabled = True
    return logging.getLogger(settings.service_name)
