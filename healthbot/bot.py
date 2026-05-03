"""Telegram-бот: команды управления подписками и просмотр статусов."""

import logging
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from healthbot.models import Status
from healthbot.monitor import Monitor
from healthbot.recipients import RecipientStore

logger = logging.getLogger(__name__)


def build_dispatcher(
    *,
    admin_chat_id: int,
    recipients: RecipientStore,
    monitor: Monitor,
) -> Dispatcher:
    """Создаёт Dispatcher с зарегистрированными хендлерами.

    Все команды управления (/subscribe, /unsubscribe, /recipients)
    защищены фильтром по admin_chat_id — принимаются только из
    указанного чата (личка владельца или группа администраторов).
    Команды просмотра (/status, /list) — для всех подписчиков.
    """
    dp = Dispatcher()
    router = Router()

    admin_filter = F.chat.id == admin_chat_id

    @router.message(Command("start"))
    async def cmd_start(message: Message) -> None:
        """Стартовое сообщение — короткая справка."""
        await message.answer(
            "ded-healthbot. Доступные команды:\n"
            "/status — снимок состояния всех сервисов\n"
            "/list — список настроенных сервисов\n"
            "/subscribe — подписать текущий чат на алерты "
            "(только для владельца)\n"
            "/unsubscribe — отписать текущий чат (только для владельца)\n"
            "/recipients — список получателей (только для владельца)"
        )

    @router.message(Command("status"))
    async def cmd_status(message: Message) -> None:
        """Снимок состояния всех сервисов."""
        lines = []
        for service in monitor.services:
            state = monitor.states[service.name]
            lines.append(_format_status_row(state, service.name))
        await message.answer("\n".join(lines) or "Нет сервисов.")

    @router.message(Command("list"))
    async def cmd_list(message: Message) -> None:
        """Список настроенных сервисов из services.yml."""
        if not monitor.services:
            await message.answer("Сервисов в конфиге нет.")
            return
        rows = [
            f"• {s.name} — {s.url} (interval={s.interval}s, "
            f"timeout={s.timeout}s, retries={s.retries})"
            for s in monitor.services
        ]
        await message.answer("\n".join(rows))

    @router.message(Command("subscribe"), admin_filter)
    async def cmd_subscribe(
        message: Message, command: CommandObject,
    ) -> None:
        """Подписать чат на алерты.

        Без аргумента — подписывает текущий чат. С аргументом — чат
        с указанным id (полезно когда бот уже сидит в группе и нужно
        подписать её, не открывая её самим).
        """
        chat_id = _parse_chat_id_arg(command, fallback=message.chat.id)
        if chat_id is None:
            await message.answer(
                "Не понял chat_id. Пример: /subscribe -1001234567890"
            )
            return
        added = await recipients.add(chat_id)
        await message.answer(
            f"Добавлен: {chat_id}" if added else f"Уже был: {chat_id}"
        )

    @router.message(Command("unsubscribe"), admin_filter)
    async def cmd_unsubscribe(
        message: Message, command: CommandObject,
    ) -> None:
        """Отписать чат от алертов."""
        chat_id = _parse_chat_id_arg(command, fallback=message.chat.id)
        if chat_id is None:
            await message.answer(
                "Не понял chat_id. Пример: /unsubscribe -1001234567890"
            )
            return
        removed = await recipients.remove(chat_id)
        await message.answer(
            f"Удалён: {chat_id}" if removed else f"Не был в списке: {chat_id}"
        )

    @router.message(Command("recipients"), admin_filter)
    async def cmd_recipients(message: Message) -> None:
        """Список текущих получателей алертов."""
        chats = await recipients.list_chats()
        if not chats:
            await message.answer("Список получателей пуст.")
            return
        await message.answer(
            "Получатели:\n" + "\n".join(f"• {c}" for c in chats)
        )

    dp.include_router(router)
    return dp


async def run_bot(bot: Bot, dispatcher: Dispatcher) -> None:
    """Запускает long-polling бота. Корректно гасит при отмене."""
    try:
        await dispatcher.start_polling(bot, handle_signals=False)
    finally:
        await bot.session.close()


def _parse_chat_id_arg(
    command: CommandObject,
    fallback: int,
) -> Optional[int]:
    """Возвращает chat_id из аргумента команды или fallback.

    None — если аргумент задан, но не парсится в целое число.
    """
    if not command.args:
        return fallback
    raw = command.args.strip()
    try:
        return int(raw)
    except ValueError:
        return None


def _format_status_row(state, name: str) -> str:
    """Одна строка для /status."""
    if state.status == Status.UP:
        latency = (
            f"{state.last_latency_ms}ms"
            if state.last_latency_ms is not None
            else "ok"
        )
        return f"✅ {name} — {latency}"
    if state.status == Status.DOWN:
        return f"❌ {name} — {state.last_error or 'down'}"
    return f"❓ {name} — ещё не проверяли"
