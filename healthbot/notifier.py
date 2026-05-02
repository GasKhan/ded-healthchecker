"""Рассылка алертов всем подписанным чатам."""

import logging
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramForbiddenError,
    TelegramNotFound,
)

from healthbot.config import ServiceConfig
from healthbot.recipients import RecipientStore

logger = logging.getLogger(__name__)


class Notifier:
    """Отправляет алерты владельцу-подписчикам при смене статуса.

    Список получателей читается из RecipientStore при каждой рассылке —
    добавленные через /subscribe чаты сразу начинают получать алерты.
    """

    def __init__(self, bot: Bot, recipients: RecipientStore) -> None:
        """Сохраняет ссылки на бота и хранилище получателей."""
        self._bot = bot
        self._recipients = recipients

    async def alert_down(
        self,
        *,
        service: ServiceConfig,
        error: Optional[str],
        attempts: int,
        when: datetime,
    ) -> None:
        """Шлёт алерт о падении сервиса."""
        text = self._format_down(
            service=service,
            error=error,
            attempts=attempts,
            when=when,
        )
        await self._broadcast(text)

    async def alert_up(
        self,
        *,
        service: ServiceConfig,
        latency_ms: Optional[int],
        downtime: Optional[timedelta],
        when: datetime,
    ) -> None:
        """Шлёт алерт о восстановлении сервиса."""
        text = self._format_up(
            service=service,
            latency_ms=latency_ms,
            downtime=downtime,
            when=when,
        )
        await self._broadcast(text)

    async def _broadcast(self, text: str) -> None:
        """Рассылает text всем чатам из RecipientStore.

        TelegramForbiddenError / TelegramNotFound означают, что бот
        больше не может писать в чат (кикнут, чат удалён). Удаляем
        такой chat_id из списка автоматически.
        """
        chats = await self._recipients.list_chats()
        if not chats:
            logger.info(
                "Получателей нет — алерт сформирован, но не отправлен."
            )
            return
        for chat_id in chats:
            try:
                await self._bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    disable_web_page_preview=True,
                )
            except (TelegramForbiddenError, TelegramNotFound) as exc:
                logger.warning(
                    "Удаляю недоступного получателя %s: %s",
                    chat_id,
                    exc,
                )
                await self._recipients.remove(chat_id)
            except TelegramAPIError as exc:
                logger.error(
                    "Не смог отправить в чат %s: %s",
                    chat_id,
                    exc,
                )

    @staticmethod
    def _format_down(
        *,
        service: ServiceConfig,
        error: Optional[str],
        attempts: int,
        when: datetime,
    ) -> str:
        """Форматирует сообщение о падении."""
        error_line = error or "неизвестная ошибка"
        return (
            f"❌ {service.name}\n"
            f"URL: {service.url}\n"
            f"Ошибка: {error_line} ({attempts} попыток)\n"
            f"Время: {when.strftime('%Y-%m-%d %H:%M:%S')}"
        )

    @staticmethod
    def _format_up(
        *,
        service: ServiceConfig,
        latency_ms: Optional[int],
        downtime: Optional[timedelta],
        when: datetime,
    ) -> str:
        """Форматирует сообщение о восстановлении."""
        latency_part = (
            f"\nLatency: {latency_ms}ms" if latency_ms is not None else ""
        )
        downtime_part = (
            f"\nDowntime: {_human_duration(downtime)}"
            if downtime is not None
            else ""
        )
        return (
            f"✅ {service.name} восстановлен"
            f"{latency_part}"
            f"{downtime_part}"
            f"\nВремя: {when.strftime('%Y-%m-%d %H:%M:%S')}"
        )


def _human_duration(delta: timedelta) -> str:
    """Преобразует timedelta в строку вида '4m 12s' / '1h 03m'."""
    total = int(delta.total_seconds())
    if total < 0:
        total = 0
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes:02d}m"
    if minutes > 0:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"
