"""Тесты для :mod:`boosty_dumper.__main__}.

Покрываем чистые функции: парсер аргументов и применение CLI-переопределений
поверх конфига. ``main``/``_run_cli``/``_run_gui`` НЕ тестируем — они тянут
сеть/GUI и точку входа приложения.
"""

from __future__ import annotations

import argparse

import pytest

from boosty_dumper import __version__
from boosty_dumper.__main__ import _apply_cli_overrides, _build_parser
from boosty_dumper.config import AppConfig


class TestBuildParser:
    """_build_parser — разбор аргументов CLI."""

    def test_version_flag(self, capsys):
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--version"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert __version__ in out

    def test_cli_flag_stored(self):
        args = _build_parser().parse_args(["--cli"])
        assert args.cli is True

    def test_cli_default_false(self):
        args = _build_parser().parse_args([])
        assert args.cli is False

    def test_blog_argument(self):
        args = _build_parser().parse_args(["-b", "myblog"])
        assert args.blog == "myblog"

    def test_long_form_blog(self):
        args = _build_parser().parse_args(["--blog", "exampleblog"])
        assert args.blog == "exampleblog"

    def test_token_override(self):
        args = _build_parser().parse_args(["--token", "secret123"])
        assert args.token == "secret123"

    def test_workers_int(self):
        args = _build_parser().parse_args(["--workers", "16"])
        assert args.workers == 16

    def test_workers_default_none(self):
        args = _build_parser().parse_args([])
        assert args.workers is None

    def test_config_path(self):
        args = _build_parser().parse_args(["-c", "/tmp/cfg.json"])
        assert args.config == "/tmp/cfg.json"

    @pytest.mark.parametrize("flag,attr", [
        ("--no-images", "no_images"),
        ("--no-videos", "no_videos"),
        ("--no-resume", "no_resume"),
    ])
    def test_negation_flags(self, flag, attr):
        args = _build_parser().parse_args([flag])
        assert getattr(args, attr) is True

    def test_negation_flags_default_false(self):
        args = _build_parser().parse_args([])
        assert args.no_images is False
        assert args.no_videos is False
        assert args.no_resume is False

    def test_multiple_flags_together(self):
        args = _build_parser().parse_args([
            "--cli", "-b", "blog", "--token", "t", "--workers", "8", "--no-images",
        ])
        assert args.cli is True
        assert args.blog == "blog"
        assert args.token == "t"
        assert args.workers == 8
        assert args.no_images is True


class TestApplyCliOverrides:
    """_apply_cli_overrides — накладывает флаги на конфиг in-place."""

    def _ns(self, **kwargs) -> argparse.Namespace:
        """Собирает Namespace с дефолтами парсера + переопределениями."""
        defaults = vars(_build_parser().parse_args([]))
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    def test_no_overrides_unchanged(self):
        cfg = AppConfig(token="orig", max_workers=4)
        result = _apply_cli_overrides(cfg, self._ns())
        assert result.token == "orig"
        assert result.max_workers == 4

    def test_token_override(self):
        cfg = AppConfig(token="")
        result = _apply_cli_overrides(cfg, self._ns(token="newtok"))
        assert result.token == "newtok"

    def test_empty_token_does_not_override(self):
        """Флаг не передан → токен из конфига не затирается пустым."""
        cfg = AppConfig(token="keepme")
        result = _apply_cli_overrides(cfg, self._ns(token=None))
        # token в парсере default=None; _apply_cli_overrides проверяет if args.token
        assert result.token == "keepme"

    def test_workers_override(self):
        cfg = AppConfig(max_workers=4)
        result = _apply_cli_overrides(cfg, self._ns(workers=12))
        assert result.max_workers == 12

    def test_workers_none_keeps_config(self):
        cfg = AppConfig(max_workers=7)
        result = _apply_cli_overrides(cfg, self._ns(workers=None))
        assert result.max_workers == 7

    def test_no_images_disables_images(self):
        cfg = AppConfig(download_images=True)
        result = _apply_cli_overrides(cfg, self._ns(no_images=True))
        assert result.download_images is False

    def test_no_videos_disables_videos(self):
        cfg = AppConfig(download_videos=True)
        result = _apply_cli_overrides(cfg, self._ns(no_videos=True))
        assert result.download_videos is False

    def test_no_resume_disables_resume(self):
        cfg = AppConfig(skip_existing=True)
        result = _apply_cli_overrides(cfg, self._ns(no_resume=True))
        assert result.skip_existing is False

    def test_all_overrides_applied(self):
        cfg = AppConfig(token="old", max_workers=2, download_images=True,
                        download_videos=True, skip_existing=True)
        result = _apply_cli_overrides(cfg, self._ns(
            token="new", workers=10, no_images=True, no_videos=True, no_resume=True,
        ))
        assert result.token == "new"
        assert result.max_workers == 10
        assert result.download_images is False
        assert result.download_videos is False
        assert result.skip_existing is False
