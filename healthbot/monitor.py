"""Оркестрация периодических проверок: по таске на сервис."""

import asyncio
import logging
from datetime import datetime
from typing import Dict, List

import aiohttp

from healthbot.checker import HealthChecker
from healthbot.config import ServiceConfig
from healthbot.models import CheckOutcome, ServiceState, Status
from healthbot.notifier import Notifier

logger = logging.getLogger(__name__)


class Monitor:
    """Запускает asyncio.Task на каждый сервис и шлёт алерты в Notifier.

    Состояние last_status хранится в памяти (без persistence).
    Стартовое значение — UNKNOWN. Алерт UP не шлём при первой
    успешной проверке после старта (UNKNOWN→UP — норма). Алерт
    DOWN при UNKNOWN→DOWN шлём — это полезный сигнал.
    """

    def __init__(
        self,
        *,
        services: List[ServiceConfig],
        notifier: Notifier,
    ) -> None:
        """Сохраняет конфиг и инициализирует state-машину."""
        self._services = services
        self._notifier = notifier
        self._states: Dict[str, ServiceState] = {
            s.name: ServiceState(name=s.name) for s in services
        }
        self._tasks: List[asyncio.Task] = []

    @property
    def states(self) -> Dict[str, ServiceState]:
        """Текущее состояние всех сервисов (для команды /status)."""
        return self._states

    @property
    def services(self) -> List[ServiceConfig]:
        """Конфиг сервисов (для команды /list)."""
        return self._services

    async def run(self) -> None:
        """Поднимает aiohttp-сессию и запускает по таске на сервис.

        Жизненный цикл: создаёт ClientSession, стартует все полл-таски,
        ждёт их вечно (до отмены извне). При отмене корректно гасит
        сессию.
        """
        async with aiohttp.ClientSession() as session:
            checker = HealthChecker(session)
            self._tasks = [
                asyncio.create_task(
                    self._poll_loop(service, checker),
                    name=f"poll-{service.name}",
                )
                for service in self._services
            ]
            try:
                await asyncio.gather(*self._tasks)
            except asyncio.CancelledError:
                for task in self._tasks:
                    task.cancel()
                raise

    async def _poll_loop(
        self,
        service: ServiceConfig,
        checker: HealthChecker,
    ) -> None:
        """Бесконечный цикл проверок одного сервиса."""
        while True:
            try:
                await self._tick(service, checker)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Сбой в poll-цикле сервиса %s — продолжаем",
                    service.name,
                )
            await asyncio.sleep(service.interval)

    async def _tick(
        self,
        service: ServiceConfig,
        checker: HealthChecker,
    ) -> None:
        """Один тик: проверка + обновление state + алерт при смене."""
        result = await checker.check(service)
        state = self._states[service.name]
        now = datetime.now()

        # 429 — сервис жив, не трогаем статус.
        if result.outcome == CheckOutcome.THROTTLED:
            logger.info(
                "%s: 429 (throttled) — статус не меняем", service.name,
            )
            return

        if result.outcome == CheckOutcome.OK:
            state.last_latency_ms = result.latency_ms
            state.last_error = None
            await self._transition_to_up(state, service, now)
        else:
            state.last_error = result.error
            await self._transition_to_down(
                state=state,
                service=service,
                error=result.error,
                attempts=result.attempts,
                now=now,
            )

    async def _transition_to_up(
        self,
        state: ServiceState,
        service: ServiceConfig,
        now: datetime,
    ) -> None:
        """Обработка успешной проверки.

        UNKNOWN→UP — без алерта (норма после старта).
        DOWN→UP — алерт ✅ с downtime.
        UP→UP — ничего не делаем.
        """
        prev = state.status
        if prev == Status.UP:
            return
        if prev == Status.DOWN:
            downtime = (
                now - state.last_change_at
                if state.last_change_at is not None
                else None
            )
            await self._notifier.alert_up(
                service=service,
                latency_ms=state.last_latency_ms,
                downtime=downtime,
                when=now,
            )
        state.status = Status.UP
        state.last_change_at = now

    async def _transition_to_down(
        self,
        *,
        state: ServiceState,
        service: ServiceConfig,
        error: str | None,
        attempts: int,
        now: datetime,
    ) -> None:
        """Обработка неуспешной проверки.

        UNKNOWN→DOWN — алерт (важнее знать сразу, чем избежать дубля).
        UP→DOWN — алерт ❌.
        DOWN→DOWN — ничего не делаем.
        """
        if state.status == Status.DOWN:
            return
        await self._notifier.alert_down(
            service=service,
            error=error,
            attempts=attempts,
            when=now,
        )
        state.status = Status.DOWN
        state.last_change_at = now
