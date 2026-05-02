"""HTTP-проверка одного сервиса с retry-логикой."""

import asyncio
import logging
import time
from typing import Optional

import aiohttp

from healthbot.config import ServiceConfig
from healthbot.models import CheckOutcome, CheckResult

logger = logging.getLogger(__name__)


class HealthChecker:
    """Проверяет один сервис: GET с retry до успеха либо до отказа.

    Использует общую aiohttp.ClientSession, передаваемую снаружи —
    это позволяет переиспользовать TCP/HTTPS соединения.
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Сохраняет ссылку на разделяемую aiohttp-сессию."""
        self._session = session

    async def check(self, service: ServiceConfig) -> CheckResult:
        """Прогоняет проверку сервиса с учётом retries.

        Возвращает CheckResult:
        - OK — хотя бы одна попытка завершилась 2xx;
        - THROTTLED — последняя попытка вернула 429 (жив, но тротлит);
        - FAILURE — все retries упали (5xx/timeout/conn error/иное).

        429 не входит в число неудач: считаем сервис живым и пропускаем
        тик. Это важно, чтобы случайный rate-лимит на стороне сервиса
        не превращался в ложный DOWN-алерт.
        """
        last_error: Optional[str] = None
        attempts = service.retries + 1
        for attempt in range(1, attempts + 1):
            outcome, latency_ms, error = await self._try_once(service)
            if outcome == CheckOutcome.OK:
                return CheckResult(
                    outcome=CheckOutcome.OK,
                    latency_ms=latency_ms,
                    attempts=attempt,
                )
            if outcome == CheckOutcome.THROTTLED:
                return CheckResult(
                    outcome=CheckOutcome.THROTTLED,
                    latency_ms=latency_ms,
                    error=error,
                    attempts=attempt,
                )
            last_error = error
            if attempt < attempts:
                await asyncio.sleep(service.retry_delay)

        return CheckResult(
            outcome=CheckOutcome.FAILURE,
            error=last_error,
            attempts=attempts,
        )

    async def _try_once(
        self,
        service: ServiceConfig,
    ) -> tuple[CheckOutcome, Optional[int], Optional[str]]:
        """Одна HTTP-попытка. Возвращает (outcome, latency_ms, error)."""
        url = str(service.url)
        timeout = aiohttp.ClientTimeout(total=service.timeout)
        start = time.perf_counter()
        try:
            async with self._session.get(
                url, timeout=timeout,
            ) as response:
                latency_ms = int((time.perf_counter() - start) * 1000)
                if response.status == 429:
                    return (
                        CheckOutcome.THROTTLED,
                        latency_ms,
                        "HTTP 429 (rate limited)",
                    )
                if 200 <= response.status < 300:
                    return (CheckOutcome.OK, latency_ms, None)
                error = f"HTTP {response.status}"
                return (CheckOutcome.FAILURE, latency_ms, error)
        except asyncio.TimeoutError:
            return (
                CheckOutcome.FAILURE,
                None,
                f"timeout after {service.timeout}s",
            )
        except aiohttp.ClientError as exc:
            return (CheckOutcome.FAILURE, None, f"{type(exc).__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Unexpected error while checking %s",
                service.name,
            )
            return (
                CheckOutcome.FAILURE,
                None,
                f"unexpected: {type(exc).__name__}: {exc}",
            )
