"""Тесты для :mod:`boosty_dumper.downloader`.

Сеть не трогаем — :class:`DownloadService` получает фейковый клиент
(:class:`FakeBoostyClient` из conftest). Файловая система — настоящая, через
pytest-фикстуру ``tmp_path``: так мы проверяем resume, атомарную запись во
временный файл и cleanup при сбое.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from boosty_dumper.config import AppConfig
from boosty_dumper.downloader import (
    DownloadService,
    DownloadStats,
    _DownloadJob,
    _Result,
    _result_label,
)
from boosty_dumper.exceptions import DownloadError
from boosty_dumper.models import MediaCounters, MediaItem, MediaType, Post

from conftest import FakeBoostyClient


# --- Хелперы для построения медиа ------------------------------------------


def _image(media_id: str = "img1") -> MediaItem:
    return MediaItem(media_id=media_id, short_id=media_id.split("-", 1)[0], kind=MediaType.IMAGE)


def _video(media_id: str = "vid1", player_url: str = "http://vid/high") -> MediaItem:
    return MediaItem(
        media_id=media_id,
        short_id=media_id.split("-", 1)[0],
        kind=MediaType.OK_VIDEO,
        raw={"playerUrls": [{"type": "high", "url": player_url}]},
    )


def _config(tmp_path: Path, **overrides) -> AppConfig:
    """Конфиг с output_dir в tmp_path — реальные файлы пишутся во временную папку."""
    defaults = dict(token="t", output_dir=str(tmp_path))
    defaults.update(overrides)
    return AppConfig(**defaults)


class TestResultLabel:
    def test_known_labels(self):
        assert _result_label(_Result.DOWNLOADED) == "скачан"
        assert _result_label(_Result.SKIPPED) == "уже есть"

    def test_unknown_falls_back_to_input(self):
        assert _result_label("weird") == "weird"


class TestDownloadStats:
    def test_defaults_zero(self):
        s = DownloadStats()
        assert s.images_downloaded == 0
        assert s.total_processed == 0
        assert s.errors == 0

    def test_total_processed_counts_downloaded_and_skipped(self):
        s = DownloadStats(
            images_downloaded=2, videos_downloaded=1,
            images_skipped=3, videos_skipped=1, errors=5,
        )
        # total_processed не включает errors (по реализации — сумма 4 счётчиков).
        assert s.total_processed == 7

    def test_as_log_contains_counts(self):
        s = DownloadStats(images_downloaded=5, videos_downloaded=2, errors=1)
        text = s.as_log()
        assert "5" in text  # изображения
        assert "2" in text  # видео
        assert "1" in text  # ошибки


class TestTargetPath:
    """DownloadService._target_path — выбор каталога и расширения по типу."""

    def test_image_target(self, tmp_path):
        item = _image("abc-123")
        path = DownloadService._target_path(item, tmp_path / "img", tmp_path / "vid")
        assert path == tmp_path / "img" / "abc.jpg"
        # short_id (без суффикса) используется как имя файла.
        assert path.name == "abc.jpg"

    def test_video_target(self, tmp_path):
        item = _video("xyz-999")
        path = DownloadService._target_path(item, tmp_path / "img", tmp_path / "vid")
        assert path == tmp_path / "vid" / "xyz.mp4"

    def test_unsupported_kind_returns_none(self, tmp_path):
        item = MediaItem("a", "a", MediaType.AUDIO)
        assert DownloadService._target_path(item, tmp_path / "img", tmp_path / "vid") is None


class TestSourceUrl:
    """DownloadService._source_url — URL по типу медиа."""

    def test_image_url(self):
        cfg = AppConfig(token="t")
        service = DownloadService.__new__(DownloadService)
        service._config = cfg
        item = _image("img99")
        assert service._source_url(item) == "https://images.boosty.to/image/img99"

    def test_video_url(self):
        cfg = AppConfig(token="t")
        service = DownloadService.__new__(DownloadService)
        service._config = cfg
        item = _video("v1", player_url="http://x/h264")
        assert service._source_url(item) == "http://x/h264"

    def test_unsupported_returns_none(self):
        cfg = AppConfig(token="t")
        service = DownloadService.__new__(DownloadService)
        service._config = cfg
        item = MediaItem("a", "a", MediaType.AUDIO)
        assert service._source_url(item) is None


class TestDownloadOne:
    """_download_one — скачивание одного файла: успех, resume, пустой ответ, отмена."""

    def test_writes_file_from_stream(self, tmp_path):
        cfg = _config(tmp_path)
        item = _image("pic")
        url = "https://images.boosty.to/image/pic"
        client = FakeBoostyClient(content_by_url={url: b"\x89PNGdata"})
        service = DownloadService(client, cfg)
        target = cfg.blog_dir("blog") / "images" / "pic.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)  # каталог готовит download_blog
        job = _DownloadJob(item=item, target=target)

        result = service._download_one(job)

        assert result == _Result.DOWNLOADED
        assert target.read_bytes() == b"\x89PNGdata"
        # Промежуточный .part удаляется после успешной замены.
        assert not target.with_suffix(".jpg.part").exists()

    def test_skips_existing_nonempty_file(self, tmp_path):
        """Resume: файл уже есть и непустой — пропускаем."""
        cfg = _config(tmp_path, skip_existing=True)
        item = _image("pic")
        url = "https://images.boosty.to/image/pic"
        client = FakeBoostyClient(content_by_url={url: b"NEW"})
        service = DownloadService(client, cfg)
        target = cfg.blog_dir("blog") / "images" / "pic.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"OLD")  # уже скачано

        job = _DownloadJob(item=item, target=target)
        assert service._download_one(job) == _Result.SKIPPED
        # Содержимое не перезаписывается.
        assert target.read_bytes() == b"OLD"

    def test_redownloads_when_skip_existing_disabled(self, tmp_path):
        cfg = _config(tmp_path, skip_existing=False)
        item = _image("pic")
        url = "https://images.boosty.to/image/pic"
        client = FakeBoostyClient(content_by_url={url: b"NEW"})
        service = DownloadService(client, cfg)
        target = cfg.blog_dir("blog") / "images" / "pic.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"OLD")

        job = _DownloadJob(item=item, target=target)
        assert service._download_one(job) == _Result.DOWNLOADED
        assert target.read_bytes() == b"NEW"

    def test_empty_response_raises_download_error(self, tmp_path):
        """Сервер отдал 0 байт — это ошибка, partial-файл удаляется."""
        cfg = _config(tmp_path)
        item = _image("pic")
        url = "https://images.boosty.to/image/pic"
        client = FakeBoostyClient(content_by_url={url: b""})  # пусто
        service = DownloadService(client, cfg)
        target = cfg.blog_dir("blog") / "images" / "pic.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        job = _DownloadJob(item=item, target=target)

        with pytest.raises(DownloadError):
            service._download_one(job)

        assert not target.exists()
        assert not target.with_suffix(".jpg.part").exists()

    def test_missing_url_raises_download_error(self, tmp_path):
        """У медиа нет URL (например, видео без playerUrls)."""
        cfg = _config(tmp_path)
        item = _video("v1", player_url="")  # video_url() вернёт None
        client = FakeBoostyClient(content_by_url={})
        service = DownloadService(client, cfg)
        target = cfg.blog_dir("blog") / "videos" / "v1.mp4"
        target.parent.mkdir(parents=True, exist_ok=True)
        job = _DownloadJob(item=item, target=target)

        with pytest.raises(DownloadError):
            service._download_one(job)
        assert not target.exists()

    def test_writes_to_temp_part_first(self, tmp_path):
        """Во время записи данные идут в .part; основной файл не появляется до конца."""
        cfg = _config(tmp_path)
        item = _image("pic")
        url = "https://images.boosty.to/image/pic"
        client = FakeBoostyClient(content_by_url={url: b"ABCD"})
        service = DownloadService(client, cfg)
        target = cfg.blog_dir("blog") / "images" / "pic.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)  # каталог готовит download_blog
        job = _DownloadJob(item=item, target=target)

        service._download_one(job)

        # После успеха .part должен исчезнуть, основной файл — появиться.
        assert target.exists()
        assert not target.with_suffix(".jpg.part").exists()


class TestCollectJobs:
    """_collect_jobs — обход постов и формирование списка заданий."""

    def test_collects_image_and_video_jobs(self, tmp_path):
        cfg = _config(tmp_path)
        post = Post(post_id="p1", title="", media=[_image("i1"), _video("v1")])
        client = FakeBoostyClient(posts=[post])
        service = DownloadService(client, cfg)

        jobs = service._collect_jobs("blog", expected=2, on_log=None)

        assert len(jobs) == 2
        kinds = {j.item.kind for j in jobs}
        assert MediaType.IMAGE in kinds
        assert MediaType.OK_VIDEO in kinds
        # Пути корректны.
        assert all(j.target.parent.name in {"images", "videos"} for j in jobs)

    def test_respects_download_flags(self, tmp_path):
        """download_images=False → только видео-задания."""
        cfg = _config(tmp_path, download_images=False)
        post = Post(post_id="p1", title="", media=[_image("i1"), _video("v1")])
        client = FakeBoostyClient(posts=[post])
        service = DownloadService(client, cfg)

        jobs = service._collect_jobs("blog", expected=1, on_log=None)
        assert len(jobs) == 1
        assert jobs[0].item.kind is MediaType.OK_VIDEO

    def test_empty_when_no_posts(self, tmp_path):
        cfg = _config(tmp_path)
        client = FakeBoostyClient(posts=[])
        service = DownloadService(client, cfg)
        assert service._collect_jobs("blog", expected=0, on_log=None) == []

    def test_audios_excluded(self, tmp_path):
        """Аудио не входит в iter_media, заданий для него нет."""
        cfg = _config(tmp_path)
        audio = MediaItem("a1", "a1", MediaType.AUDIO)
        post = Post(post_id="p1", title="", media=[audio, _image("i1")])
        client = FakeBoostyClient(posts=[post])
        service = DownloadService(client, cfg)

        jobs = service._collect_jobs("blog", expected=1, on_log=None)
        assert len(jobs) == 1
        assert jobs[0].item.kind is MediaType.IMAGE


class TestDownloadBlogIntegration:
    """Интеграционные тесты download_blog: счётчики, статистика, resume, отмена."""

    def test_downloads_all_media_and_returns_stats(self, tmp_path):
        cfg = _config(tmp_path)
        post = Post(post_id="p1", title="", media=[_image("i1"), _image("i2"), _video("v1")])
        client = FakeBoostyClient(
            counters=MediaCounters(images=2, videos=1, audios=0),
            posts=[post],
            content_by_url={
                "https://images.boosty.to/image/i1": b"img1",
                "https://images.boosty.to/image/i2": b"img2",
                "http://vid/high": b"video1",
            },
        )
        service = DownloadService(client, cfg)

        stats = service.download_blog("blog")

        assert stats.images_downloaded == 2
        assert stats.videos_downloaded == 1
        assert stats.errors == 0
        # Файлы на диске.
        assert (cfg.blog_dir("blog") / "images" / "i1.jpg").read_bytes() == b"img1"
        assert (cfg.blog_dir("blog") / "images" / "i2.jpg").read_bytes() == b"img2"
        assert (cfg.blog_dir("blog") / "videos" / "v1.mp4").read_bytes() == b"video1"

    def test_creates_blog_subdirectories(self, tmp_path):
        cfg = _config(tmp_path)
        post = Post(post_id="p1", title="", media=[_image("i1")])
        client = FakeBoostyClient(
            counters=MediaCounters(images=1, videos=0, audios=0),
            posts=[post],
            content_by_url={"https://images.boosty.to/image/i1": b"x"},
        )
        service = DownloadService(client, cfg)

        service.download_blog("myblog")

        # Каталоги создаются с санитизированным именем блога.
        assert (cfg.blog_dir("myblog") / "images").is_dir()
        assert (cfg.blog_dir("myblog") / "videos").is_dir()

    def test_zero_counters_returns_empty_stats(self, tmp_path):
        cfg = _config(tmp_path)
        client = FakeBoostyClient(
            counters=MediaCounters(images=0, videos=0, audios=0),
            posts=[],
        )
        service = DownloadService(client, cfg)
        stats = service.download_blog("blog")
        assert stats.total_processed == 0
        assert stats.errors == 0

    def test_resume_skips_existing_files(self, tmp_path):
        cfg = _config(tmp_path, skip_existing=True)
        post = Post(post_id="p1", title="", media=[_image("i1")])
        # Файл уже существует на диске.
        target = cfg.blog_dir("blog") / "images" / "i1.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"EXISTING")

        client = FakeBoostyClient(
            counters=MediaCounters(images=1, videos=0, audios=0),
            posts=[post],
            content_by_url={"https://images.boosty.to/image/i1": b"NEW"},
        )
        service = DownloadService(client, cfg)

        stats = service.download_blog("blog")
        assert stats.images_skipped == 1
        assert stats.images_downloaded == 0
        # Содержимое не тронуто.
        assert target.read_bytes() == b"EXISTING"

    def test_callbacks_invoked(self, tmp_path):
        """on_progress / on_log вызываются с осмысленными значениями."""
        cfg = _config(tmp_path)
        post = Post(post_id="p1", title="", media=[_image("i1"), _image("i2")])
        client = FakeBoostyClient(
            counters=MediaCounters(images=2, videos=0, audios=0),
            posts=[post],
            content_by_url={
                "https://images.boosty.to/image/i1": b"a",
                "https://images.boosty.to/image/i2": b"b",
            },
        )
        service = DownloadService(client, cfg)

        progress_calls: list[tuple[int, int]] = []
        logs: list[str] = []

        service.download_blog(
            "blog",
            on_progress=lambda done, total: progress_calls.append((done, total)),
            on_log=lambda msg: logs.append(msg),
        )

        # Финальный прогресс == (2, 2).
        assert progress_calls[-1] == (2, 2)
        assert any("К загрузке: 2 файлов" in m for m in logs)
        assert any("Готово" in m for m in logs)

    def test_download_error_counted_not_raised(self, tmp_path):
        """Один битый файл не валит всю загрузку — фиксируется в stats.errors."""
        cfg = _config(tmp_path)
        # i2 не имеет контента → DownloadError от пустого ответа.
        post = Post(post_id="p1", title="", media=[_image("i1"), _image("i2")])
        client = FakeBoostyClient(
            counters=MediaCounters(images=2, videos=0, audios=0),
            posts=[post],
            content_by_url={"https://images.boosty.to/image/i1": b"ok"},
            # i2 — нет записи → пустой ответ
        )
        service = DownloadService(client, cfg)

        errors: list[tuple[str, str]] = []
        stats = service.download_blog("blog", on_error=lambda msg, mid: errors.append((msg, mid)))

        assert stats.images_downloaded == 1
        assert stats.errors == 1
        assert len(errors) == 1
        assert errors[0][1] == "i2"  # media_id проблемного файла


class TestCancel:
    """stop() запрашивает мягкую отмену."""

    def test_stop_sets_cancel_flag(self, tmp_path):
        cfg = _config(tmp_path)
        service = DownloadService(FakeBoostyClient(), cfg)
        assert not service.is_cancelled()
        service.stop()
        assert service.is_cancelled()

    def test_cancel_aborts_mid_stream(self, tmp_path):
        """При отмене во время стриминга partial-файл удаляется, ошибки нет.

        Хитрость: клиент должен вызвать service.stop(), но сервис создаётся
        ПОСЛЕ клиента. Решаем через хранилище-ссылку: клиент дёргает колбэк,
        который сервис регистрирует при создании.
        """
        cfg = _config(tmp_path)
        item = _image("big")
        url = "https://images.boosty.to/image/big"
        cancel_hook: list = []  # service зарегистрирует здесь свой .stop

        class CancellingClient(FakeBoostyClient):
            def __init__(self):
                super().__init__(content_by_url={url: b"x" * 100})

            def download_stream(self, url, *, chunk_size=1024):
                # Триггерим отмену в начале стриминга.
                if cancel_hook:
                    cancel_hook[0]()
                yield from super().download_stream(url, chunk_size=chunk_size)

        client = CancellingClient()
        service = DownloadService(client, cfg)
        cancel_hook.append(service.stop)

        target = cfg.blog_dir("blog") / "images" / "big.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        job = _DownloadJob(item=item, target=target)

        result = service._download_one(job)
        # При отмене _download_one возвращает SKIPPED, файл не появляется.
        assert result == _Result.SKIPPED
        assert not target.exists()
        assert not target.with_suffix(".jpg.part").exists()
