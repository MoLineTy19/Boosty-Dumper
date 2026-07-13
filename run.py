#!/usr/bin/env python3
"""Тонкая точка входа для запуска из исходников.

Запуск::

    python run.py            # графический интерфейс
    python run.py --cli -b блог   # консольный режим

Вся логика — в пакете :mod:`boosty_dumper`.
"""

from __future__ import annotations

import sys

from boosty_dumper.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
