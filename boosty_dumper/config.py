"""Конфигурация приложения.

Конфиг хранится в ``config.json`` рядом с исполняемым файлом (или рядом с
исходниками при запуске из кода). Раньше Bearer-токен был зашит прямо в
``cookies.py`` — это утечка секрета в репозиторий. Теперь токен пользователь
вводит в GUI, и он сохраняется локально; в репозиторий попадает только
``config.json.example`` с пустыми полями.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Буквенно-цифровые символы, дефис и подчёркивание; всё остальное заменяем на "_".
_BLOG_SANITIZE = re.compile(r"[^A-Za-z0-9_-]+")


def _app_base_dir() -> Path:
    """Базовый каталог приложения.

    При работе из собранного PyInstaller'ом exe ``sys.executable`` указывает на
    сам ``.exe``; при запуске из исходников используем каталог проекта.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def default_output_dir() -> Path:
    """Каталог загрузок по умолчанию: ``<база_приложения>/downloads``."""
    return _app_base_dir() / "downloads"


def sanitize_blog_name(name: str) -> str:
    """Делает имя блога безопасным для использования как имя каталога."""
    cleaned = _BLOG_SANITIZE.sub("_", name.strip()).strip("._-")
    return cleaned or "blog"


@dataclass(slots=True)
class AppConfig:
    """Настройки сессии загрузки.

    Все поля имеют осмысленные значения по умолчанию, чтобы конфиг работал
    даже без ``config.json``.
    """

    # Bearer-токен из запроса на boosty.to (Authorization: Bearer ...).
    token: str = ""
    # Куда складывать скачанные медиа.
    output_dir: str = field(default_factory=lambda: str(default_output_dir()))
    # Число параллельных потоков загрузки.
    max_workers: int = 4
    # Что скачивать.
    download_images: bool = True
    download_videos: bool = True
    # Resume: пропускать уже скачанные файлы.
    skip_existing: bool = True
    # Таймаут запроса к API в секундах.
    request_timeout: float = 30.0

    def validate(self) -> list[str]:
        """Возвращает список ошибок валидации (пустой список = всё ок)."""
        errors: list[str] = []
        if not self.token.strip():
            errors.append("Не указан Bearer-токен.")
        if self.max_workers < 1:
            errors.append("Число потоков должно быть не меньше 1.")
        if self.max_workers > 32:
            errors.append("Число потоков не должно превышать 32.")
        if self.request_timeout <= 0:
            errors.append("Таймаут запроса должен быть положительным.")
        if not (self.download_images or self.download_videos):
            errors.append("Выберите хотя бы один тип медиа (изображения и/или видео).")
        return errors

    # --- (Де)сериализация -------------------------------------------------

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "AppConfig":
        """Строит конфиг из словаря, игнорируя неизвестные ключи."""
        known = set(cls.__dataclass_fields__)
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)  # type: ignore[arg-type]

    @classmethod
    def load(cls, path: Path) -> "AppConfig":
        """Загружает конфиг из JSON-файла; при отсутствии возвращает дефолтный.

        Повреждённый файл не валит приложение: логируем и используем умолчания.
        """
        if not path.exists():
            log.debug("Файл конфигурации %s не найден — использую значения по умолчанию.", path)
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Не удалось прочитать конфиг %s: %s. Использую умолчания.", path, exc)
            return cls()
        if not isinstance(data, dict):
            log.warning("Конфиг %s повреждён (ожидался объект). Использую умолчания.", path)
            return cls()
        return cls.from_dict(data)

    def save(self, path: Path) -> None:
        """Сохраняет конфиг в JSON-файл (создаёт родительские каталоги)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        # Ограничиваем права только для текущего пользователя: в файле лежит токен.
        try:
            path.chmod(0o600)
        except OSError:
            # На Windows/некоторых ФС chmod либо игнорируется, либо недоступен.
            pass

    # --- Производные значения --------------------------------------------

    @property
    def output_path(self) -> Path:
        """``output_dir`` как :class:`~pathlib.Path`."""
        return Path(self.output_dir)

    def blog_dir(self, blog: str) -> Path:
        """Полный путь к каталогу конкретного блога."""
        return self.output_path / sanitize_blog_name(blog)

    @staticmethod
    def resolve_config_path(override: str | os.PathLike[str] | None = None) -> Path:
        """Где лежит ``config.json``.

        Приоритет: явный ``override`` → рядом с exe (PyInstaller) → рядом с
        исходниками.
        """
        if override:
            return Path(override)
        return _app_base_dir() / "config.json"
