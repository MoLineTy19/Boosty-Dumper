"""Настройка логирования.

Два назначения:

1. Писать лог в файл (ротируемый) — для отладки и разбора полетов.
2. Опционально дублировать в GUI через :class:`GuiLogHandler`.

Логи складываются в ``<output_dir>/.logs/``. Для собранного exe это будет
рядом с приложением в ``downloads/.logs/``.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import default_output_dir

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class GuiLogHandler(logging.Handler):
    """Обработчик, пересылающий записи во внешний приёмник.

    Идея: GUI подписывается через :meth:`set_target`, и каждая запись летит в
    лог-панель. Чтобы не плодить связность с Qt, здесь нет импортов PySide6 —
    принимается любая callable, которая примет строку.
    """

    def __init__(self, level: int = logging.INFO) -> None:
        super().__init__(level=level)
        self._target: "callable[[str], None] | None" = None  # type: ignore[valid-type]
        self.setFormatter(logging.Formatter("%(message)s"))

    def set_target(self, target: "callable[[str], None] | None") -> None:  # type: ignore[valid-type]
        """Устанавливает/снимает приёмник сообщений (например, слот Qt)."""
        self._target = target

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        if self._target is None:
            return
        try:
            self._target(self.format(record))
        except Exception:  # noqa: BLE001 — UI мог быть уже закрыт.
            pass


def setup_logging(
    *,
    level: int = logging.INFO,
    output_dir: Path | None = None,
    console: bool = True,
) -> GuiLogHandler:
    """Настраивает root-логгер и возвращает GUI-обработчик.

    :param output_dir: каталог, где создать подпапку ``.logs``;
    :param console:    дублировать ли лог в stderr (полезно для CLI-режима).
    """
    logs_dir = (output_dir or default_output_dir()) / ".logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "boosty_dumper.log"

    root = logging.getLogger()
    root.setLevel(level)
    # Чистим предыдущие хендлеры (на случай повторного вызова).
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    file_handler = RotatingFileHandler(
        log_file, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    root.addHandler(file_handler)

    if console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(level)
        root.addHandler(console_handler)

    gui_handler = GuiLogHandler(level=level)
    gui_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(gui_handler)

    logging.getLogger(__name__).debug("Логирование настроено; файл: %s", log_file)
    return gui_handler
