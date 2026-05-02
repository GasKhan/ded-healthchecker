"""Хранилище chat_id-получателей алертов.

Список чатов хранится в JSON рядом с конфигом, изменяется командами
бота /subscribe и /unsubscribe. Чтение/запись защищены asyncio.Lock,
запись — атомарная (через временный файл + rename).
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import List, Set

import aiofiles

logger = logging.getLogger(__name__)


class RecipientStore:
    """In-memory + JSON-файл хранилище списка получателей."""

    def __init__(self, path: Path) -> None:
        """Инициализирует хранилище. Загрузку выполнить через load()."""
        self._path = path
        self._chats: Set[int] = set()
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        """Читает recipients.json в память.

        Если файла нет — стартуем с пустого списка. Битый JSON
        логируется и тоже сводится к пустому списку (но файл не
        перезаписываем — пусть владелец увидит и решит вручную).
        """
        if not self._path.exists():
            self._chats = set()
            return
        try:
            async with aiofiles.open(self._path, "r", encoding="utf-8") as f:
                raw = await f.read()
            data = json.loads(raw) if raw.strip() else {}
            chats = data.get("chats", [])
            self._chats = {int(x) for x in chats}
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error(
                "Битый recipients.json (%s): %s. Стартуем с пустым "
                "списком, файл не перезаписан.",
                self._path,
                exc,
            )
            self._chats = set()

    async def list_chats(self) -> List[int]:
        """Снимок текущего списка чатов."""
        async with self._lock:
            return sorted(self._chats)

    async def add(self, chat_id: int) -> bool:
        """Добавляет chat_id. Возвращает True если был новым."""
        async with self._lock:
            if chat_id in self._chats:
                return False
            self._chats.add(chat_id)
            await self._dump()
            return True

    async def remove(self, chat_id: int) -> bool:
        """Удаляет chat_id. Возвращает True если был удалён."""
        async with self._lock:
            if chat_id not in self._chats:
                return False
            self._chats.discard(chat_id)
            await self._dump()
            return True

    async def _dump(self) -> None:
        """Атомарная запись текущего множества в JSON-файл.

        Пишем во временный файл и делаем rename — так нельзя получить
        битый recipients.json при падении в момент записи.
        Вызывается под self._lock.
        """
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        payload = json.dumps(
            {"chats": sorted(self._chats)},
            ensure_ascii=False,
            indent=2,
        )
        async with aiofiles.open(tmp, "w", encoding="utf-8") as f:
            await f.write(payload)
        os.replace(tmp, self._path)
