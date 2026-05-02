"""Конфигурация бота: env-переменные + загрузка services.yml."""

from pathlib import Path
from typing import List

import yaml
from pydantic import BaseModel, Field, HttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки из переменных окружения / .env."""

    bot_token: str = Field(
        ...,
        description="Токен Telegram-бота от @BotFather.",
    )
    owner_id: int = Field(
        ...,
        description=(
            "Telegram user_id владельца — единственный, кто может "
            "управлять списком получателей."
        ),
    )
    services_config_path: Path = Field(
        default=Path("services.yml"),
        description="Путь к YAML-конфигу сервисов.",
    )
    recipients_path: Path = Field(
        default=Path("recipients.json"),
        description="Путь к JSON-файлу со списком чатов-получателей.",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


class ServiceDefaults(BaseModel):
    """Параметры опроса по умолчанию для всех сервисов."""

    interval: int = Field(default=60, ge=1)
    timeout: int = Field(default=10, ge=1)
    retries: int = Field(default=3, ge=0)
    retry_delay: int = Field(default=5, ge=0)


class ServiceConfig(BaseModel):
    """Параметры одного отслеживаемого сервиса."""

    name: str = Field(..., min_length=1)
    url: HttpUrl
    interval: int = Field(default=60, ge=1)
    timeout: int = Field(default=10, ge=1)
    retries: int = Field(default=3, ge=0)
    retry_delay: int = Field(default=5, ge=0)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        """Убирает пробелы по краям имени сервиса."""
        return value.strip()


def load_services(path: Path) -> List[ServiceConfig]:
    """Загружает список сервисов из YAML, применяя defaults.

    Структура файла:
        defaults:
          interval, timeout, retries, retry_delay
        services:
          - name, url, [interval, timeout, retries, retry_delay]

    Любой параметр сервиса переопределяет соответствующий default.
    Возвращает список валидированных ServiceConfig.
    Бросает ValueError при пустом или некорректном списке сервисов.
    """
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    defaults = ServiceDefaults(**(raw.get("defaults") or {}))
    services_raw = raw.get("services") or []

    if not services_raw:
        raise ValueError(
            f"В {path} нет ни одного сервиса в секции 'services'."
        )

    services: List[ServiceConfig] = []
    seen_names: set[str] = set()
    for entry in services_raw:
        merged = {**defaults.model_dump(), **entry}
        service = ServiceConfig(**merged)
        if service.name in seen_names:
            raise ValueError(
                f"Дублирующееся имя сервиса '{service.name}' "
                f"в {path}."
            )
        seen_names.add(service.name)
        services.append(service)

    return services
