"""Типизированные модели данных.

Раньше по коду гуляли сырые словари и некорректные аннотации (``data: json``,
``-> [dict, int]``). Здесь собраны dataclass'ы, через которые слои общаются между
собой: это даёт автодополнение, проверки типов и читаемые имена полей.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator


class MediaType(str, Enum):
    """Тип медиа-объекта в посте Boosty.

    Значения соответствуют полю ``type`` в ответе API.
    """

    IMAGE = "image"
    OK_VIDEO = "ok_video"
    VIDEO = "video"
    AUDIO = "audioFile"

    @classmethod
    def from_api(cls, raw: str) -> "MediaType | None":
        """Возвращает enum по строке из API или ``None``, если тип неизвестен."""
        try:
            return cls(raw)
        except ValueError:
            return None


@dataclass(slots=True, frozen=True)
class MediaCounters:
    """Счётчики доступного медиа для блога (endpoint ``/counters``)."""

    images: int
    videos: int
    audios: int

    @property
    def total(self) -> int:
        """Сумма поддерживаемых типов (аудио пока не скачивается)."""
        return self.images + self.videos

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "MediaCounters":
        """Собирает объект из ``data.mediaCounters`` ответа counters."""
        counters = data.get("data", {}).get("mediaCounters", {})
        return cls(
            images=int(counters.get("image", 0)),
            videos=int(counters.get("okVideo", 0)),
            audios=int(counters.get("audioFile", 0)),
        )


@dataclass(slots=True, frozen=True)
class MediaItem:
    """Один медиа-объект внутри поста.

    :attribute media_id: исходный ``id`` (может содержать суффикс ``-XXXXX``);
    :attribute short_id: id без суффикса — используется как имя файла;
    :attribute kind:      тип медиа (распознанный);
    :attribute raw:       исходный словарь (нужен для извлечения URL видео и т. п.).
    """

    media_id: str
    short_id: str
    kind: MediaType
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "MediaItem | None":
        """Строит элемент из записи ``media`` поста.

        Возвращает ``None``, если тип не распознан и не обрабатывается.
        """
        kind = MediaType.from_api(data.get("type", ""))
        if kind is None:
            return None
        media_id = str(data.get("id", "unknown"))
        return cls(
            media_id=media_id,
            short_id=media_id.split("-", 1)[0],
            kind=kind,
            raw=data,
        )

    def video_url(self) -> str | None:
        """Выбирает лучший доступный URL воспроизведения для ok_video.

        Исправляет баг исходного кода, где проверялось несуществующее
        ``url['low'] == 'low'``. Теперь корректно смотрим на поле ``type``:
        предпочитаем ``high``, затем ``medium``, затем ``low``.
        """
        preference = ("high", "medium", "low", "ultra")
        urls_by_type = {item.get("type"): item.get("url", "") for item in self.raw.get("playerUrls", [])}
        for quality in preference:
            url = urls_by_type.get(quality)
            if url:
                return url
        # На всякий случай — первый непустой URL из списка.
        for item in self.raw.get("playerUrls", []):
            url = item.get("url", "")
            if url:
                return url
        return None


@dataclass(slots=True, frozen=True)
class Post:
    """Пост с медиа. Содержит только нужное для загрузки."""

    post_id: str
    title: str
    media: list[MediaItem]

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Post":
        """Строит пост из записи ``mediaPosts``. Пропускает неизвестные типы."""
        media = [item for item in (MediaItem.from_api(m) for m in data.get("media", [])) if item is not None]
        return cls(
            post_id=str(data.get("id", "")),
            title=str(data.get("title", "") or ""),
            media=media,
        )

    def iter_media(self, *, images: bool = True, videos: bool = True) -> Iterator[MediaItem]:
        """Фильтрует медиа по выбранным пользователем категориям."""
        for item in self.media:
            if item.kind is MediaType.IMAGE and images:
                yield item
            elif item.kind is MediaType.OK_VIDEO and videos:
                yield item
