"""Точка входа: ``python -m boosty_dumper`` или собранный ``BoostyDumper.exe``.

Поддерживает два режима:

* **GUI** (по умолчанию) — окно на PySide6;
* **CLI** (``--cli``) — консольный режим без графического окружения, удобно для
  серверов/скриптов. Все необходимые параметры берутся из ``config.json`` и
  опционально переопределяются флагами.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Sequence

from . import __version__
from .config import AppConfig
from .logging_setup import setup_logging

log = logging.getLogger("boosty_dumper.__main__")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="boosty_dumper",
        description="BoostyDumper — загрузчик медиа с boosty.to.",
    )
    parser.add_argument("--version", action="version", version=f"BoostyDumper {__version__}")
    parser.add_argument("--cli", action="store_true", help="запустить в консольном режиме без GUI")
    parser.add_argument("-c", "--config", default=None, help="путь к config.json")
    parser.add_argument("-b", "--blog", default=None, help="имя блога (только для --cli)")
    parser.add_argument("--token", default=None, help="Bearer-токен (перекрывает config.json)")
    parser.add_argument("--no-images", action="store_true", help="не скачивать изображения")
    parser.add_argument("--no-videos", action="store_true", help="не скачивать видео")
    parser.add_argument("--no-resume", action="store_true", help="перекачивать даже существующие файлы")
    parser.add_argument("--workers", type=int, default=None, help="число потоков загрузки")
    return parser


def _apply_cli_overrides(config: AppConfig, args: argparse.Namespace) -> AppConfig:
    """Накладывает аргументы CLI поверх конфига (in-place)."""
    if args.token:
        config.token = args.token
    if args.workers is not None:
        config.max_workers = args.workers
    if args.no_images:
        config.download_images = False
    if args.no_videos:
        config.download_videos = False
    if args.no_resume:
        config.skip_existing = False
    return config


def _run_cli(config: AppConfig, blog: str | None) -> int:
    """Консольный режим: выкачать медиа блога и выйти."""
    from .api import BoostyClient
    from .downloader import DownloadService
    from .exceptions import BoostyError

    errors = config.validate()
    if blog is None or not blog.strip():
        errors.append("Имя блога обязательно в CLI-режиме (--blog).")
    if errors:
        for err in errors:
            print(f"[ОШИБКА] {err}", file=sys.stderr)
        return 2

    blog = blog.strip()
    try:
        with BoostyClient(config.token, timeout=config.request_timeout) as client:
            service = DownloadService(client, config)
            stats = service.download_blog(
                blog,
                on_progress=lambda done, total: print(f"\r{done}/{total}", end="", flush=True),
                on_log=lambda msg: print(f"\n{msg}" if "\n" not in msg else msg),
            )
        print()  # перевод строки после прогресса
        print(stats.as_log())
        return 0
    except BoostyError as exc:
        print(f"\n[КРИТИЧНО] {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nПрервано пользователем.", file=sys.stderr)
        return 130


def _run_gui(config: AppConfig) -> int:
    """Графический режим."""
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:
        print(
            "PySide6 не установлен. Запустите в --cli режиме или установите: pip install PySide6",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc

    app = QApplication(sys.argv)
    app.setApplicationName("BoostyDumper")
    app.setApplicationVersion(__version__)

    from .gui.main_window import MainWindow

    # Создаём GUI-обработчик логов и подключаем к окну.
    gui_handler = setup_logging(console=False)
    window = MainWindow(config, gui_handler)
    window.show()
    return app.exec()


def main(argv: Sequence[str] | None = None) -> int:
    """Главная функция точки входа."""
    args = _build_parser().parse_args(argv)
    config_path = AppConfig.resolve_config_path(args.config)
    config = AppConfig.load(config_path)
    config = _apply_cli_overrides(config, args)

    # В CLI-режиме логируем в консоль; в GUI — без stderr-дублирования.
    setup_logging(console=args.cli)

    log.info("BoostyDumper %s запущен (режим: %s).", __version__, "cli" if args.cli else "gui")

    if args.cli:
        return _run_cli(config, args.blog)
    return _run_gui(config)


if __name__ == "__main__":
    raise SystemExit(main())
