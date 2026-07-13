"""Тесты для :mod:`boosty_dumper.logging_setup`.

:func:`setup_logging` меняет глобальный root-логгер и создаёт файлы — тесты
осторожны: сохраняем и восстанавливаем обработчики root до/после, файлы пишем
во ``tmp_path``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from boosty_dumper.logging_setup import GuiLogHandler, setup_logging


@pytest.fixture
def isolated_root_logger():
    """Сохраняет/восстанавливает состояние root-логгера вокруг теста.

    setup_logging добавляет RotatingFileHandler с открытым файловым
    дескриптором; при очистке handlers нужно явно закрывать, иначе GC
    позже выбросит незакрытый FileIO → PytestUnraisableExceptionWarning
    (который в нашем pytest.ini превращается в error).
    """
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    yield root
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()  # освобождаем файловые дескрипторы
    for h in saved_handlers:
        root.addHandler(h)
    root.setLevel(saved_level)


class TestGuiLogHandler:
    """GuiLogHandler — пересылает записи в callable-приёмник."""

    def test_default_target_is_none(self):
        handler = GuiLogHandler()
        assert handler._target is None

    def test_set_target_and_emit(self):
        """Запись лога долетает до приёмника отформатированной строкой."""
        handler = GuiLogHandler()
        received: list[str] = []
        handler.set_target(received.append)

        logger = logging.getLogger("test.gui.handler")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            logger.info("hello world")
        finally:
            logger.removeHandler(handler)

        assert len(received) == 1
        assert "hello world" in received[0]

    def test_emit_without_target_is_noop(self):
        """Без приёмника emit не должен падать."""
        handler = GuiLogHandler()
        record = logging.LogRecord(
            "x", logging.INFO, __file__, 1, "msg", None, None,
        )
        handler.emit(record)  # не должно бросать

    def test_set_target_none_disables(self):
        handler = GuiLogHandler()
        received: list[str] = []
        handler.set_target(received.append)
        handler.set_target(None)

        logger = logging.getLogger("test.gui.disabled")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            logger.info("nope")
        finally:
            logger.removeHandler(handler)
        assert received == []

    def test_emit_swallows_target_exceptions(self):
        """Если приёмник бросает (например, Qt-слот на удалённом объекте) — не падаем."""
        handler = GuiLogHandler()

        def bad_target(_msg: str) -> None:
            raise RuntimeError("boom")

        handler.set_target(bad_target)
        record = logging.LogRecord(
            "x", logging.INFO, __file__, 1, "msg", None, None,
        )
        # Не должно бросить — ошибка проглатывается.
        handler.emit(record)


class TestSetupLogging:
    """setup_logging — настраивает root-логгер и возвращает GUI-обработчик."""

    def test_returns_gui_handler(self, tmp_path, isolated_root_logger):
        handler = setup_logging(output_dir=tmp_path, console=False)
        assert isinstance(handler, GuiLogHandler)

    def test_creates_log_file(self, tmp_path, isolated_root_logger):
        """После настройки лог-файл существует в <output>/.logs/."""
        setup_logging(output_dir=tmp_path, console=False)
        expected = tmp_path / ".logs" / "boosty_dumper.log"
        assert expected.exists()

    def test_logs_go_to_file(self, tmp_path, isolated_root_logger):
        """Запись через root должна попасть в файл."""
        setup_logging(output_dir=tmp_path, console=False)
        logging.getLogger("test.file").info("file-message-12345")

        log_text = (tmp_path / ".logs" / "boosty_dumper.log").read_text(encoding="utf-8")
        assert "file-message-12345" in log_text

    def test_clears_previous_handlers(self, tmp_path, isolated_root_logger):
        """Повторный вызов не должен дублировать обработчики.

        Примечание: сам ``setup_logging`` при замене handlers НЕ закрывает
        старый ``RotatingFileHandler`` (микро-лик FD в продакшен-коде). Здесь
        мы его закрываем вручную, чтобы GC не выбросил незакрытый FileIO как
        unraisable — но сам факт стоит зафиксировать как известную оговорку.
        """
        setup_logging(output_dir=tmp_path, console=False)
        first_handlers = list(logging.getLogger().handlers)
        n_after_first = len(first_handlers)

        setup_logging(output_dir=tmp_path, console=False)
        n_after_second = len(logging.getLogger().handlers)

        # Закрываем «оторванные» обработчики первой настройки — их уже нет в
        # root, но файловый дескриптор ещё жив.
        for h in first_handlers:
            h.close()

        assert n_after_first == n_after_second

    def test_console_handler_added_when_requested(self, tmp_path, isolated_root_logger):
        """console=True → в обработчиках есть StreamHandler в stderr (не файловый)."""
        setup_logging(output_dir=tmp_path, console=True)
        has_stream = any(
            isinstance(h, logging.StreamHandler)
            and not isinstance(h, GuiLogHandler)
            and not isinstance(h, logging.FileHandler)  # FileHandler — потомок StreamHandler
            for h in logging.getLogger().handlers
        )
        assert has_stream

    def test_console_handler_absent_when_disabled(self, tmp_path, isolated_root_logger):
        setup_logging(output_dir=tmp_path, console=False)
        has_stream = any(
            isinstance(h, logging.StreamHandler)
            and not isinstance(h, GuiLogHandler)
            and not isinstance(h, logging.FileHandler)
            for h in logging.getLogger().handlers
        )
        assert not has_stream
