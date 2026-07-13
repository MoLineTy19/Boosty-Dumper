"""Фоновый воркер загрузки для GUI.

Вся сетевая/дисковая работа выполняется в :class:`DownloadWorker` (наследник
``QThread``). GUI общается с ним только через сигналы — никаких блокировок
главного потока. Кнопка «Стоп» зовёт :meth:`DownloadWorker.request_stop`,
воркер мягко завершает текущие задачи.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..api import BoostyClient
from ..config import AppConfig
from ..downloader import DownloadService, DownloadStats
from ..exceptions import AuthError, BoostyError, NotFoundError, RateLimitError

log = logging.getLogger(__name__)


class DownloadWorker(QThread):
    """Поток, запускающий :class:`DownloadService` для одного блога."""

    # (done, total)
    progress = Signal(int, int)
    # одно сообщение в лог-панель
    log_message = Signal(str)
    # критическая ошибка, требующая внимания пользователя
    failed = Signal(str)
    # успешное завершение со статистикой
    finished_ok = Signal(object)  # DownloadStats

    def __init__(
        self,
        config: AppConfig,
        blog: str,
        *,
        gui_log_target: "callable[[str], None] | None" = None,  # type: ignore[valid-type]
        parent: object | None = None,
    ) -> None:
        super().__init__(parent=parent)  # type: ignore[arg-type]
        self._config = config
        self._blog = blog
        self._gui_log_target = gui_log_target
        self._service: DownloadService | None = None

    # --- Управление ------------------------------------------------------

    def request_stop(self) -> None:
        """Мягкая остановка: текущие файлы доделываются, новые не стартуют."""
        if self._service is not None:
            self._service.stop()

    # --- Запуск ----------------------------------------------------------

    def run(self) -> None:  # noqa: D401 — точка входа QThread
        """Основная логика потока. Ошибки транслируем в сигналы."""
        try:
            with BoostyClient(self._config.token, timeout=self._config.request_timeout) as client:
                self._service = DownloadService(client, self._config)
                stats: DownloadStats = self._service.download_blog(
                    self._blog,
                    on_progress=self._on_progress,
                    on_log=self._on_log,
                    on_error=lambda msg, _mid: self.log_message.emit(f"[ОШИБКА] {msg}"),
                )
            self.finished_ok.emit(stats)
        except AuthError as exc:
            log.warning("AuthError: %s", exc)
            self.failed.emit(f"Ошибка авторизации: {exc}\nПроверьте Bearer-токен.")
        except NotFoundError as exc:
            self.failed.emit(f"Не найдено: {exc}")
        except RateLimitError as exc:
            self.failed.emit(f"Превышен лимит запросов. Попробуйте позже.\n{exc}")
        except BoostyError as exc:
            self.failed.emit(f"Ошибка: {exc}")
        except Exception as exc:  # noqa: BLE001 — последний рубеж, UI не должен падать.
            log.exception("Непредвиденная ошибка воркера")
            self.failed.emit(f"Непредвиденная ошибка: {exc}")
        finally:
            self._service = None

    # --- Колбэки DownloadService (исполняются в этом потоке) -------------

    def _on_progress(self, done: int, total: int) -> None:
        self.progress.emit(done, total)

    def _on_log(self, message: str) -> None:
        self.log_message.emit(message)
