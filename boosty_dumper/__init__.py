"""BoostyDumper — загрузчик медиа (изображения и видео) с платформы Boosty.

Пакет разделён на слои:

* :mod:`boosty_dumper.config`      — конфигурация приложения (``config.json``);
* :mod:`boosty_dumper.models`      — типизированные модели данных;
* :mod:`boosty_dumper.api`         — клиент Boosty API (сессия, retry, пагинация);
* :mod:`boosty_dumper.downloader`  — сервис параллельной загрузки (resume);
* :mod:`boosty_dumper.gui`         — графический интерфейс на PySide6.

Точка входа: :mod:`boosty_dumper.__main__`.
"""

from __future__ import annotations

__version__ = "2.0.0"
__author__ = "MoLineTy19"
__all__ = ["__version__"]
