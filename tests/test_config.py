"""Тесты для :mod:`boosty_dumper.config`.

Покрываем: санитизацию имени блога, валидацию конфига, (де)сериализацию
в JSON, файловый round-trip (load/save), устойчивость к повреждённому
конфигу и производные пути (blog_dir).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from boosty_dumper.config import AppConfig, sanitize_blog_name


class TestSanitizeBlogName:
    """sanitize_blog_name — превращает имя блога в безопасное имя каталога."""

    def test_keeps_alphanum_dash_underscore(self):
        assert sanitize_blog_name("exampleblog") == "exampleblog"
        assert sanitize_blog_name("my-blog_99") == "my-blog_99"

    def test_replaces_spaces_and_punctuation(self):
        # Пробелы, точки, слэши, двоеточия → подчёркивание.
        assert sanitize_blog_name("My Blog!") == "My_Blog"
        assert sanitize_blog_name("a.b/c:d") == "a_b_c_d"

    def test_replaces_cyrillic_runs_with_single_underscore(self):
        """Смежные неподдержанные символы схлопываются в ОДНО подчёркивание ('+' квантор)."""
        # «xблогy» → кириллица схлопывается в одно '_' → "x_y"
        assert sanitize_blog_name("xблогy") == "x_y"

    def test_pure_cyrillic_falls_back_to_blog(self):
        """Чистая кириллица: после замены всё '_', обрезка краёв даёт '' → 'blog'."""
        assert sanitize_blog_name("блог") == "blog"

    def test_strips_leading_trailing_underscores_and_dots(self):
        """После замены остаются граничные «мусорные» символы — срезаем их."""
        # "...weird..." -> "__weird__" -> "weird"
        assert sanitize_blog_name("...weird...") == "weird"
        assert sanitize_blog_name("___name___") == "name"

    def test_empty_string_falls_back_to_blog(self):
        assert sanitize_blog_name("") == "blog"

    def test_only_punctuation_falls_back_to_blog(self):
        """После санитизации и обрезки ничего не осталось — дефолт 'blog'."""
        assert sanitize_blog_name("...") == "blog"
        assert sanitize_blog_name("   ") == "blog"
        assert sanitize_blog_name("!!!") == "blog"


class TestAppConfigDefaults:
    """AppConfig создаётся с осмысленными значениями по умолчанию."""

    def test_defaults_present(self):
        cfg = AppConfig()
        assert cfg.token == ""
        assert cfg.max_workers == 4
        assert cfg.download_images is True
        assert cfg.download_videos is True
        assert cfg.skip_existing is True
        assert cfg.request_timeout == 30.0

    def test_output_path_is_pathlib(self):
        cfg = AppConfig()
        assert isinstance(cfg.output_path, Path)

    def test_blog_dir_uses_sanitized_name(self):
        cfg = AppConfig(output_dir="/tmp/out")
        assert cfg.blog_dir("My Blog!") == Path("/tmp/out/My_Blog")


class TestAppConfigValidate:
    """AppConfig.validate — список ошибок (пустой = валиден)."""

    def test_valid_config_no_errors(self):
        cfg = AppConfig(token="abc123")
        assert cfg.validate() == []

    def test_empty_token_error(self):
        cfg = AppConfig(token="")
        errors = cfg.validate()
        assert any("токен" in e.lower() for e in errors)

    def test_whitespace_only_token_error(self):
        cfg = AppConfig(token="   ")
        assert cfg.validate() != []

    def test_workers_below_one_error(self):
        cfg = AppConfig(token="x", max_workers=0)
        errors = cfg.validate()
        assert any("1" in e for e in errors)

    def test_workers_above_32_error(self):
        cfg = AppConfig(token="x", max_workers=33)
        errors = cfg.validate()
        assert any("32" in e for e in errors)

    def test_workers_boundaries_valid(self):
        assert AppConfig(token="x", max_workers=1).validate() == []
        assert AppConfig(token="x", max_workers=32).validate() == []

    def test_timeout_must_be_positive(self):
        cfg = AppConfig(token="x", request_timeout=0)
        errors = cfg.validate()
        assert any("таймаут" in e.lower() for e in errors)

        cfg = AppConfig(token="x", request_timeout=-5)
        assert cfg.validate() != []

    def test_must_pick_at_least_one_media_type(self):
        cfg = AppConfig(token="x", download_images=False, download_videos=False)
        errors = cfg.validate()
        assert any("медиа" in e.lower() for e in errors)

    def test_collects_multiple_errors(self):
        cfg = AppConfig(token="", max_workers=0, request_timeout=0,
                        download_images=False, download_videos=False)
        errors = cfg.validate()
        # Как минимум по одной на каждое нарушенное правило.
        assert len(errors) >= 4


class TestAppConfigSerialization:
    """to_dict / from_dict round-trip и устойчивость."""

    def test_round_trip_preserves_fields(self):
        original = AppConfig(
            token="tok",
            output_dir="/some/where",
            max_workers=8,
            download_images=False,
            download_videos=True,
            skip_existing=False,
            request_timeout=12.5,
        )
        restored = AppConfig.from_dict(original.to_dict())
        assert restored == original

    def test_from_dict_ignores_unknown_keys(self):
        """Лишние ключи (от старой версии конфига) не ломают загрузку."""
        cfg = AppConfig.from_dict({
            "token": "t",
            "max_workers": 5,
            "thisFieldDoesNotExist": 123,
            "another_stranger": True,
        })
        assert cfg.token == "t"
        assert cfg.max_workers == 5

    def test_from_dict_partial_uses_defaults(self):
        """Недостающие поля берутся из дефолтов."""
        cfg = AppConfig.from_dict({"token": "only_token"})
        assert cfg.token == "only_token"
        assert cfg.max_workers == 4  # дефолт


class TestAppConfigLoadSave:
    """Файловый round-trip: save → load и устойчивость к ошибкам."""

    def test_save_then_load_round_trip(self, tmp_path):
        path = tmp_path / "config.json"
        original = AppConfig(token="secret", max_workers=7)
        original.save(path)

        assert path.exists(), "save должен создать файл"
        loaded = AppConfig.load(path)
        assert loaded == original

    def test_save_writes_valid_json(self, tmp_path):
        path = tmp_path / "config.json"
        AppConfig(token="t").save(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["token"] == "t"
        assert data["max_workers"] == 4

    def test_save_creates_parent_dirs(self, tmp_path):
        """save должен создать недостающие родительские каталоги."""
        path = tmp_path / "nested" / "deep" / "config.json"
        AppConfig(token="t").save(path)
        assert path.exists()

    def test_load_missing_file_returns_default(self, tmp_path):
        path = tmp_path / "nope.json"
        cfg = AppConfig.load(path)
        assert cfg == AppConfig()

    def test_load_corrupted_json_returns_default(self, tmp_path):
        """Повреждённый JSON не должен валить приложение."""
        path = tmp_path / "bad.json"
        path.write_text("{ this is not : json ]", encoding="utf-8")
        cfg = AppConfig.load(path)
        assert cfg == AppConfig()

    def test_load_non_object_json_returns_default(self, tmp_path):
        """JSON-массив вместо объекта — тоже повреждение."""
        path = tmp_path / "arr.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        cfg = AppConfig.load(path)
        assert cfg == AppConfig()

    def test_token_persisted_after_save(self, tmp_path):
        """Токен должен переживать save/load (он хранится локально)."""
        path = tmp_path / "config.json"
        AppConfig(token="bearer_xyz").save(path)
        assert AppConfig.load(path).token == "bearer_xyz"


class TestResolveConfigPath:
    """AppConfig.resolve_config_path — приоритет override."""

    def test_explicit_override_used(self):
        path = AppConfig.resolve_config_path("/custom/path.json")
        assert path == Path("/custom/path.json")

    def test_none_uses_app_base(self):
        path = AppConfig.resolve_config_path(None)
        assert path.name == "config.json"
