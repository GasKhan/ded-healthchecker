"""Точка входа: python -m healthbot.

Поднимает Bot, RecipientStore, Monitor и параллельно запускает
long-polling бота и периодические проверки сервисов.
"""

import asyncio
import logging
import signal
import sys

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from healthbot.bot import build_dispatcher, run_bot
from healthbot.config import Settings, load_services
from healthbot.monitor import Monitor
from healthbot.notifier import Notifier
from healthbot.recipients import RecipientStore


def _setup_logging() -> None:
    """Базовая настройка логов в stdout."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


async def main() -> None:
    """Главный async-вход. Поднимает все компоненты и держит цикл."""
    _setup_logging()
    log = logging.getLogger("healthbot.main")

    settings = Settings()
    services = load_services(settings.services_config_path)
    log.info("Загружено сервисов: %d", len(services))

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    recipients = RecipientStore(settings.recipients_path)
    await recipients.load()
    notifier = Notifier(bot=bot, recipients=recipients)
    monitor = Monitor(services=services, notifier=notifier)
    dispatcher = build_dispatcher(
        owner_id=settings.owner_id,
        recipients=recipients,
        monitor=monitor,
    )

    bot_task = asyncio.create_task(
        run_bot(bot, dispatcher), name="bot",
    )
    monitor_task = asyncio.create_task(monitor.run(), name="monitor")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # Windows / некоторые окружения — fallback на KeyboardInterrupt
            pass

    done, pending = await asyncio.wait(
        {bot_task, monitor_task, asyncio.create_task(stop_event.wait())},
        return_when=asyncio.FIRST_COMPLETED,
    )
    log.info("Останавливаемся...")
    for task in pending:
        task.cancel()
    for task in (bot_task, monitor_task):
        if not task.done():
            task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
