"""Тесты для :mod:`boosty_dumper.models`.

Модели — чистые dataclass'ы, разбирающие ответы API в типизированные объекты.
Здесь проверяем: распознавание типов, нормализацию id, выбор URL видео,
фильтрацию медиа по категориям и устойчивость к неизвестным/пустым данным.
"""

from __future__ import annotations

import pytest

from boosty_dumper.models import MediaCounters, MediaItem, MediaType, Post


class TestMediaType:
    """MediaType.from_api — маппинг строк API в enum."""

    def test_from_api_known_types(self):
        assert MediaType.from_api("image") is MediaType.IMAGE
        assert MediaType.from_api("ok_video") is MediaType.OK_VIDEO
        assert MediaType.from_api("video") is MediaType.VIDEO
        assert MediaType.from_api("audioFile") is MediaType.AUDIO

    def test_from_api_unknown_returns_none(self):
        """Неизвестные типы не должны бросать — иначе падает разбор поста."""
        assert MediaType.from_api("foo") is None

    def test_from_api_empty_returns_none(self):
        assert MediaType.from_api("") is None

    def test_is_str_enum(self):
        """MediaType наследует str — удобно для сравнений и сериализации."""
        assert MediaType.IMAGE == "image"


class TestMediaCounters:
    """MediaCounters.from_api — разбор поля data.mediaCounters."""

    def test_from_api_full(self):
        data = {"data": {"mediaCounters": {"image": 5, "okVideo": 3, "audioFile": 2}}}
        counters = MediaCounters.from_api(data)
        assert counters.images == 5
        assert counters.videos == 3
        assert counters.audios == 2

    def test_from_api_missing_fields_default_zero(self):
        """API может не вернуть часть счётчиков — дефолт 0."""
        counters = MediaCounters.from_api({"data": {}})
        assert counters.images == 0
        assert counters.videos == 0
        assert counters.audios == 0

    def test_from_api_empty_dict(self):
        counters = MediaCounters.from_api({})
        assert counters.total == 0

    def test_from_api_completely_empty(self):
        # Нет ни data, ни mediaCounters — должно падать тихо, давать нули.
        assert MediaCounters.from_api({}).total == 0

    def test_total_sums_images_and_videos_not_audios(self):
        """Аудио пока не скачивается, в total не входит."""
        counters = MediaCounters(images=10, videos=4, audios=99)
        assert counters.total == 14

    def test_total_zero(self):
        assert MediaCounters(images=0, videos=0, audios=0).total == 0


class TestMediaItemFromApi:
    """MediaItem.from_api — построение элемента из записи media."""

    def test_builds_image_item(self):
        data = {"id": "abc123", "type": "image"}
        item = MediaItem.from_api(data)
        assert item is not None
        assert item.media_id == "abc123"
        assert item.kind is MediaType.IMAGE

    def test_short_id_strips_suffix(self):
        """id из API может содержать суффикс «-XXXXX»; short_id — без него."""
        data = {"id": "abc123-99999", "type": "ok_video"}
        item = MediaItem.from_api(data)
        assert item is not None
        assert item.short_id == "abc123"

    def test_short_id_without_suffix_is_full_id(self):
        data = {"id": "plainid", "type": "image"}
        item = MediaItem.from_api(data)
        assert item is not None
        assert item.short_id == "plainid"
        assert item.media_id == "plainid"

    def test_unknown_type_returns_none(self):
        assert MediaItem.from_api({"id": "x", "type": "weird"}) is None

    def test_missing_type_returns_none(self):
        assert MediaItem.from_api({"id": "x"}) is None

    def test_missing_id_falls_back_to_unknown(self):
        """id нет — не падаем, используем строку 'unknown'."""
        item = MediaItem.from_api({"type": "image"})
        assert item is not None
        assert item.media_id == "unknown"
        assert item.short_id == "unknown"

    def test_raw_payload_preserved(self):
        """Сырой словарь нужен для извлечения URL видео."""
        data = {"id": "x", "type": "image", "extra": 42}
        item = MediaItem.from_api(data)
        assert item is not None
        assert item.raw is data


class TestMediaItemVideoUrl:
    """MediaItem.video_url — выбор качества воспроизведения ok_video.

    Это место фикса исходного бага: раньше код проверял несуществующее
    ``url['low'] == 'low'``. Теперь смотрим на поле ``type`` и предпочитаем
    high → medium → low → ultra.
    """

    @staticmethod
    def _item(player_urls: list[dict]) -> MediaItem:
        return MediaItem(
            media_id="m1",
            short_id="m1",
            kind=MediaType.OK_VIDEO,
            raw={"playerUrls": player_urls},
        )

    def test_prefers_high_over_medium_and_low(self):
        item = self._item([
            {"type": "low", "url": "http://low"},
            {"type": "medium", "url": "http://med"},
            {"type": "high", "url": "http://high"},
        ])
        assert item.video_url() == "http://high"

    def test_falls_back_to_medium_when_no_high(self):
        item = self._item([
            {"type": "low", "url": "http://low"},
            {"type": "medium", "url": "http://med"},
        ])
        assert item.video_url() == "http://med"

    def test_falls_back_to_low(self):
        item = self._item([{"type": "low", "url": "http://low"}])
        assert item.video_url() == "http://low"

    def test_uses_ultra_when_present(self):
        item = self._item([{"type": "ultra", "url": "http://ultra"}])
        assert item.video_url() == "http://ultra"

    def test_prefers_any_nonempty_when_qualities_unknown(self):
        """Все типы не из предпочтительного списка — берём первый непустой."""
        item = self._item([
            {"type": "weird", "url": "http://w1"},
            {"type": "strange", "url": "http://w2"},
        ])
        assert item.video_url() == "http://w1"

    def test_skips_empty_urls_in_known_quality(self):
        item = self._item([
            {"type": "high", "url": ""},
            {"type": "medium", "url": "http://med"},
        ])
        assert item.video_url() == "http://med"

    def test_returns_none_when_no_player_urls(self):
        item = self._item([])
        assert item.video_url() is None

    def test_returns_none_when_field_absent(self):
        item = MediaItem("m", "m", MediaType.OK_VIDEO, raw={})
        assert item.video_url() is None

    def test_returns_none_when_all_urls_empty(self):
        item = self._item([{"type": "high", "url": ""}])
        assert item.video_url() is None


class TestPost:
    """Post.from_api и Post.iter_media."""

    def test_from_api_builds_post_with_media(self):
        data = {
            "id": 777,
            "title": "Заголовок",
            "media": [
                {"id": "img1", "type": "image"},
                {"id": "vid2-1", "type": "ok_video"},
            ],
        }
        post = Post.from_api(data)
        assert post.post_id == "777"
        assert post.title == "Заголовок"
        assert len(post.media) == 2
        assert post.media[0].kind is MediaType.IMAGE
        assert post.media[1].kind is MediaType.OK_VIDEO

    def test_from_api_skips_unknown_media_types(self):
        """Неизвестные типы отфильтровываются, пост не падает."""
        data = {
            "id": "p1",
            "title": "",
            "media": [
                {"id": "x", "type": "image"},
                {"id": "y", "type": "unsupported"},
                {"id": "z", "type": "ok_video"},
            ],
        }
        post = Post.from_api(data)
        assert len(post.media) == 2
        assert {m.media_id for m in post.media} == {"x", "z"}

    def test_from_api_empty_media(self):
        post = Post.from_api({"id": "p", "title": "t"})
        assert post.media == []

    def test_from_api_missing_title_defaults_empty(self):
        post = Post.from_api({"id": "p"})
        assert post.title == ""

    def test_from_api_none_title_becomes_empty(self):
        """API может вернуть title: null — приводим к ''."""
        post = Post.from_api({"id": "p", "title": None})
        assert post.title == ""

    def test_from_api_missing_id_defaults_empty(self):
        post = Post.from_api({"title": "t"})
        assert post.post_id == ""

    def test_iter_media_includes_images_and_videos_by_default(self):
        post = Post.from_api({
            "id": "p",
            "title": "",
            "media": [
                {"id": "i1", "type": "image"},
                {"id": "v1", "type": "ok_video"},
            ],
        })
        ids = [m.media_id for m in post.iter_media()]
        assert ids == ["i1", "v1"]

    def test_iter_media_images_only(self):
        post = Post.from_api({
            "id": "p",
            "title": "",
            "media": [
                {"id": "i1", "type": "image"},
                {"id": "v1", "type": "ok_video"},
            ],
        })
        ids = [m.media_id for m in post.iter_media(images=True, videos=False)]
        assert ids == ["i1"]

    def test_iter_media_videos_only(self):
        post = Post.from_api({
            "id": "p",
            "title": "",
            "media": [
                {"id": "i1", "type": "image"},
                {"id": "v1", "type": "ok_video"},
            ],
        })
        ids = [m.media_id for m in post.iter_media(images=False, videos=True)]
        assert ids == ["v1"]

    def test_iter_media_neither_returns_nothing(self):
        post = Post.from_api({
            "id": "p",
            "title": "",
            "media": [{"id": "i1", "type": "image"}],
        })
        assert list(post.iter_media(images=False, videos=False)) == []

    def test_iter_media_excludes_audios(self):
        """Аудио не входит в iter_media вообще — оно не скачивается."""
        post = Post.from_api({
            "id": "p",
            "title": "",
            "media": [
                {"id": "a1", "type": "audioFile"},
                {"id": "i1", "type": "image"},
            ],
        })
        ids = [m.media_id for m in post.iter_media()]
        assert "a1" not in ids
        assert ids == ["i1"]
