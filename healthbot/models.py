"""Модели данных: статусы, результаты проверок, состояние сервиса."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Status(str, Enum):
    """Статус сервиса."""

    UNKNOWN = "unknown"      # ещё не проверяли (стартовое значение)
    UP = "up"                # последняя проверка успешна
    DOWN = "down"             # сервис не отвечает / отвечает ошибкой


class CheckOutcome(str, Enum):
    """Результат одной попытки проверки эндпоинта."""

    OK = "ok"                # 2xx + ожидаемое тело
    THROTTLED = "throttled"   # 429 — жив, но тротлит; не считаем DOWN
    FAILURE = "failure"       # 5xx, timeout, conn error и т.п.


@dataclass(slots=True)
class CheckResult:
    """Результат итогового цикла проверки (с учётом retry)."""

    outcome: CheckOutcome
    latency_ms: Optional[int] = None
    error: Optional[str] = None
    attempts: int = 1


@dataclass(slots=True)
class ServiceState:
    """Текущее состояние сервиса в памяти монитора."""

    name: str
    status: Status = Status.UNKNOWN
    last_change_at: Optional[datetime] = None
    last_latency_ms: Optional[int] = None
    last_error: Optional[str] = None
    paused: bool = False  # зарезервировано под /pause-команду в будущем

    def __post_init__(self) -> None:
        """Совместимость с slots для возможного будущего расширения."""
        # Намеренно пусто — slots-датакласс не требует init-логики.
        return None
