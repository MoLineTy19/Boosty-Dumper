"""Главное окно приложения.

Собирает форму ввода (токен, блог, папка, типы медиа, число потоков), панель
прогресса и лог-панель. Связывается с :class:`DownloadWorker` только через
сигналы — потокобезопасно и без зависания UI.

Файл ``style.qss`` лежит рядом; подгружается в :meth:`MainWindow._apply_style`.
При сборке exe он включается в bundle (см. ``boosty_dumper.spec``).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..config import AppConfig
from ..exceptions import BoostyError
from .workers import DownloadWorker

log = logging.getLogger(__name__)

_QSS_FILENAME = "style.qss"


def _resource_path(name: str) -> Path:
    """Возвращает путь к ресурсу: рядом с модулем (включая bundle PyInstaller)."""
    return Path(__file__).resolve().parent / name


class MainWindow(QMainWindow):
    """Основное окно GUI."""

    def __init__(self, config: AppConfig, gui_log_handler) -> None:
        super().__init__()
        self._config = config
        self._gui_log_handler = gui_log_handler
        self._worker: DownloadWorker | None = None

        self.setWindowTitle("BoostyDumper")
        self.setMinimumSize(720, 560)

        self._build_ui()
        self._populate_from_config()
        self._apply_style()

        # Лог-панель как приёмник логов из logging.
        self._gui_log_handler.set_target(self._append_log)
        self._append_log(f"BoostyDumper готов к работе. Папка загрузок: {self._config.output_path}")

    # --- UI --------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # --- Группа «Параметры» ---
        params = QGroupBox("Параметры")
        form = QFormLayout(params)

        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_edit.setPlaceholderText("Bearer eyJ... из запроса boosty.to")
        form.addRow("Bearer-токен:", self.token_edit)

        self.blog_edit = QLineEdit()
        self.blog_edit.setPlaceholderText("например: maria_help")
        form.addRow("Имя блога:", self.blog_edit)

        # Папка + кнопка выбора.
        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("Куда сохранять медиа")
        browse_btn = QPushButton("Обзор…")
        browse_btn.clicked.connect(self._choose_folder)
        folder_row.addWidget(self.folder_edit)
        folder_row.addWidget(browse_btn)
        folder_widget = QWidget()
        folder_widget.setLayout(folder_row)
        form.addRow("Папка:", folder_widget)

        # Что качать.
        types_row = QHBoxLayout()
        self.images_cb = QCheckBox("Изображения")
        self.videos_cb = QCheckBox("Видео")
        self.images_cb.setChecked(True)
        self.videos_cb.setChecked(True)
        types_row.addWidget(self.images_cb)
        types_row.addWidget(self.videos_cb)
        types_row.addStretch()
        types_widget = QWidget()
        types_widget.setLayout(types_row)
        form.addRow("Типы медиа:", types_widget)

        # Потоки + resume.
        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, 32)
        self.workers_spin.setValue(4)
        self.skip_existing_cb = QCheckBox("Пропускать уже скачанные (resume)")
        self.skip_existing_cb.setChecked(True)
        form.addRow("Потоков:", self.workers_spin)
        form.addRow("", self.skip_existing_cb)

        root.addWidget(params)

        # --- Управление ---
        controls = QHBoxLayout()
        self.start_btn = QPushButton("Старт")
        self.stop_btn = QPushButton("Стоп")
        self.stop_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._on_start)
        self.stop_btn.clicked.connect(self._on_stop)
        controls.addStretch()
        controls.addWidget(self.start_btn)
        controls.addWidget(self.stop_btn)
        controls_widget = QWidget()
        controls_widget.setLayout(controls)
        root.addWidget(controls_widget)

        # --- Прогресс ---
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_label = QLabel("Ожидание запуска")
        root.addWidget(self.progress_label)
        root.addWidget(self.progress_bar)

        # --- Лог ---
        log_group = QGroupBox("Лог")
        log_layout = QVBoxLayout(log_group)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        log_layout.addWidget(self.log_view)
        root.addWidget(log_group, stretch=1)

        # Меню: простое «Файл → Выход».
        menu = self.menuBar().addMenu("Файл")
        quit_action = QAction("Выход", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        menu.addAction(quit_action)

    def _populate_from_config(self) -> None:
        """Заполняем поля сохранёнными значениями из конфига."""
        if self._config.token:
            self.token_edit.setText(self._config.token)
        self.folder_edit.setText(str(self._config.output_path))
        self.images_cb.setChecked(self._config.download_images)
        self.videos_cb.setChecked(self._config.download_videos)
        self.workers_spin.setValue(self._config.max_workers)
        self.skip_existing_cb.setChecked(self._config.skip_existing)

    def _apply_style(self) -> None:
        qss_path = _resource_path(_QSS_FILENAME)
        if qss_path.exists():
            try:
                self.setStyleSheet(qss_path.read_text(encoding="utf-8"))
            except OSError as exc:
                log.warning("Не удалось прочитать %s: %s", qss_path, exc)

    # --- Слоты ------------------------------------------------------------

    def _choose_folder(self) -> None:
        current = self.folder_edit.text() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Папка для загрузок", current)
        if chosen:
            self.folder_edit.setText(chosen)

    def _on_start(self) -> None:
        # Переносим значения из UI в конфиг и валидируем.
        self._config.token = self.token_edit.text().strip()
        self._config.output_dir = self.folder_edit.text().strip() or str(Path.home() / "downloads")
        self._config.download_images = self.images_cb.isChecked()
        self._config.download_videos = self.videos_cb.isChecked()
        self._config.max_workers = self.workers_spin.value()
        self._config.skip_existing = self.skip_existing_cb.isChecked()

        errors = self._config.validate()
        blog = self.blog_edit.text().strip()
        if not blog:
            errors.append("Укажите имя блога.")
        if errors:
            QMessageBox.critical(self, "Проверьте ввод", "\n".join(f"• {e}" for e in errors))
            return

        # Сохраняем конфиг, чтобы не вводить заново.
        try:
            self._config.save(AppConfig.resolve_config_path())
        except OSError as exc:
            log.warning("Не удалось сохранить конфиг: %s", exc)

        # Запуск воркера.
        self._set_running(True)
        self.progress_bar.setValue(0)
        self.progress_label.setText("Подготовка…")
        self._append_log(f"Старт загрузки: блог «{blog}», потоков {self._config.max_workers}.")

        self._worker = DownloadWorker(self._config, blog)
        self._worker.progress.connect(self._on_progress)
        self._worker.log_message.connect(self._append_log)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.start()

    def _on_stop(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._append_log("Останавливаю… (текущие файлы будут завершены)")
            self._worker.request_stop()
        self.stop_btn.setEnabled(False)

    def _on_progress(self, done: int, total: int) -> None:
        if total <= 0:
            self.progress_bar.setRange(0, 0)  # «бегущий» индикатор
            self.progress_label.setText(f"Обработка… ({done})")
            return
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(done)
        percent = int(done * 100 / total) if total else 0
        self.progress_label.setText(f"{done} / {total}  ({percent}%)")

    def _on_failed(self, message: str) -> None:
        self._append_log(f"[КРИТИЧНО] {message}")
        QMessageBox.critical(self, "Ошибка", message)
        self._set_running(False)

    def _on_finished(self, stats) -> None:
        self._append_log(stats.as_log())
        self.progress_label.setText("Готово")
        self._set_running(False)

    # --- Хелперы ---------------------------------------------------------

    def _set_running(self, running: bool) -> None:
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        # Блокируем редактирование на время загрузки.
        for widget in (
            self.token_edit, self.blog_edit, self.folder_edit,
            self.images_cb, self.videos_cb, self.workers_spin, self.skip_existing_cb,
        ):
            widget.setEnabled(not running)

    def _append_log(self, message: str) -> None:
        self.log_view.appendPlainText(str(message))

    # --- Закрытие окна ---------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 — имя задаёт Qt.
        if self._worker is not None and self._worker.isRunning():
            reply = QMessageBox.question(
                self,
                "Подтвердите выход",
                "Загрузка ещё выполняется. Остановить и выйти?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._worker.request_stop()
            self._worker.wait(5000)
        event.accept()
