"""Сервис параллельной загрузки медиа.

Отвечает за то, чтобы превратить поток постов от :class:`BoostyClient` в файлы
на диске. Здесь исправлены баги исходного ``main.py``:

* картинка больше не пишется дважды (раньше ``shutil.copyfileobj`` + ``f.write``);
* скачивание идёт через ``stream=True`` чанками, а не ``r.raw``/``r.content``;
* выбор качества видео смотрит на поле ``type`` (``high``/``medium``/``low``),
  а не на несуществующее ``url['low']``;
* при ошибке одного файла весь процесс не падает.

Параллельность — :class:`~concurrent.futures.ThreadPoolExecutor` (загрузка
упирается в I/O, так что потоков достаточно). Resume — пропуск существующих
файлов по имени. Прогресс и логи уходят через callbacks, чтобы модуль был
независим от конкретного UI.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from .api import BoostyClient, IMAGES_BASE
from .config import AppConfig
from .exceptions import BoostyError, DownloadError
from .models import MediaCounters, MediaItem, MediaType, Post

log = logging.getLogger(__name__)

# Колбэки. Храним как тип, чтобы в сигнатурах было читаемо.
ProgressCallback = Callable[[int, int], None]   # (done, total)
LogCallback = Callable[[str], None]
ErrorCallback = Callable[[str, str], None]       # (сообщение, media_id)


@dataclass(slots=True)
class DownloadStats:
    """Итоговая статистика одной сессии загрузки."""

    images_downloaded: int = 0
    videos_downloaded: int = 0
    images_skipped: int = 0
    videos_skipped: int = 0
    errors: int = 0

    @property
    def total_processed(self) -> int:
        return self.images_downloaded + self.videos_downloaded + self.images_skipped + self.videos_skipped

    def as_log(self) -> str:
        return (
            f"Готово. Скачано: изображений {self.images_downloaded}, "
            f"видео {self.videos_downloaded}. "
            f"Пропущено (уже есть): {self.images_skipped + self.videos_skipped}. "
            f"Ошибок: {self.errors}."
        )


class DownloadService:
    """Координирует многопоточную загрузку медиа.

    Использование::

        service = DownloadService(client, config)
        stats = service.download_blog(
            blog="exampleblog",
            on_progress=lambda d, t: print(f"{d}/{t}"),
            on_log=print,
        )
    """

    def __init__(self, client: BoostyClient, config: AppConfig) -> None:
        self._client = client
        self._config = config
        # Флаг остановки: устанавливается извне через stop().
        self._cancel = threading.Event()

    # --- Публичный API ---------------------------------------------------

    def stop(self) -> None:
        """Запрашивает мягкую остановку: текущие файлы доделываются, новые не стартуют."""
        self._cancel.set()

    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def download_blog(
        self,
        blog: str,
        *,
        on_progress: ProgressCallback | None = None,
        on_log: LogCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> DownloadStats:
        """Скачивает все выбранные медиа блога и возвращает статистику."""
        self._cancel.clear()
        stats = DownloadStats()

        counters = self._client.get_media_counters(blog)
        self._emit(on_log, self._format_counters(counters, blog))

        if counters.total == 0:
            self._emit(on_log, f"У блога «{blog}» нет доступного медиа.")
            return stats

        # Готовим каталоги.
        images_dir = self._config.blog_dir(blog) / "images"
        videos_dir = self._config.blog_dir(blog) / "videos"
        images_dir.mkdir(parents=True, exist_ok=True)
        videos_dir.mkdir(parents=True, exist_ok=True)

        # Собираем плоский список заданий (медиа + целевой путь).
        jobs = self._collect_jobs(blog, counters.total, on_log)
        total = len(jobs)
        self._emit(on_log, f"К загрузке: {total} файлов.")

        done = 0
        lock = threading.Lock()

        def _worker(job: _DownloadJob) -> None:
            nonlocal done
            if self._cancel.is_set():
                return
            try:
                result = self._download_one(job)
            except DownloadError as exc:
                with lock:
                    stats.errors += 1
                    done += 1
                self._emit(on_error, str(exc), job.item.short_id)
                self._emit(on_log, f"[ОШИБКА] {exc}")
            except BoostyError as exc:
                with lock:
                    stats.errors += 1
                    done += 1
                self._emit(on_error, f"Не удалось скачать {job.target.name}: {exc}", job.item.short_id)
                self._emit(on_log, f"[ОШИБКА] {job.target.name}: {exc}")
            else:
                with lock:
                    if result == _Result.SKIPPED:
                        if job.item.kind is MediaType.IMAGE:
                            stats.images_skipped += 1
                        else:
                            stats.videos_skipped += 1
                    elif result == _Result.DOWNLOADED:
                        if job.item.kind is MediaType.IMAGE:
                            stats.images_downloaded += 1
                        else:
                            stats.videos_downloaded += 1
                    done += 1
                self._emit(on_log, f"[{done}/{total}] {job.target.name} — {_result_label(result)}")
            finally:
                self._emit(on_progress, done, total)

        max_workers = max(1, min(self._config.max_workers, total or 1))
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="boosty-dl") as pool:
            futures: list[Future] = [pool.submit(_worker, job) for job in jobs]
            for fut in futures:
                # Пробрасываем непредвиденные исключения (логика ошибок уже внутри _worker).
                fut.result()

        if self._cancel.is_set():
            self._emit(on_log, "Загрузка остановлена пользователем.")
        self._emit(on_log, stats.as_log())
        return stats

    # --- Внутренние методы ----------------------------------------------

    def _collect_jobs(self, blog: str, expected: int, on_log: LogCallback | None) -> list["_DownloadJob"]:
        """Обходит посты и формирует список заданий на загрузку.

        Если при обходе возникла RateLimitError — делаем паузу и продолжаем.
        Прерывается по ``self._cancel``.
        """
        images_dir = self._config.blog_dir(blog) / "images"
        videos_dir = self._config.blog_dir(blog) / "videos"
        jobs: list[_DownloadJob] = []

        try:
            posts: Iterator[Post] = self._client.iter_posts(blog)
            for post in posts:
                if self._cancel.is_set():
                    break
                for item in post.iter_media(images=self._config.download_images, videos=self._config.download_videos):
                    target = self._target_path(item, images_dir, videos_dir)
                    if target is None:
                        continue
                    jobs.append(_DownloadJob(item=item, target=target))
        except BoostyError as exc:
            self._emit(on_log, f"[ВНИМАНИЕ] При сборе списка постов: {exc}")
            log.warning("Сбор постов прерван: %s", exc)

        # Если по каким-то причинам ничего не собралось, хотя счётчик ненулевой —
        # честно сообщаем (раньше это было тихим провалом).
        if not jobs and expected > 0:
            self._emit(on_log, "Не удалось собрать ссылки на медиа (возможно, нет доступа к контенту).")
        return jobs

    def _download_one(self, job: "_DownloadJob") -> "_Result":
        """Скачивает один файл. Возвращает статус. Бросает :class:`DownloadError`."""
        item = job.item
        target = job.target

        # Resume: пропускаем уже существующие файлы.
        if self._config.skip_existing and target.exists() and target.stat().st_size > 0:
            return _Result.SKIPPED

        url = self._source_url(item)
        if not url:
            raise DownloadError("Не найден URL для скачивания", media_id=item.short_id)

        # Пишем во временный файл, чтобы не осталось полу-файла при сбое.
        tmp = target.with_suffix(target.suffix + ".part")
        try:
            received = 0
            with open(tmp, "wb") as f:
                for chunk in self._client.download_stream(url):
                    if self._cancel.is_set():
                        # Отменяем: удаляем partial и выходим без ошибки.
                        f.close()
                        self._safe_remove(tmp)
                        return _Result.SKIPPED
                    f.write(chunk)
                    received += len(chunk)
            if received == 0:
                self._safe_remove(tmp)
                raise DownloadError("Пустой ответ сервера", url=url, media_id=item.short_id)
            tmp.replace(target)
        except OSError as exc:
            self._safe_remove(tmp)
            raise DownloadError(f"Файловая ошибка: {exc}", url=url, media_id=item.short_id) from exc
        return _Result.DOWNLOADED

    def _source_url(self, item: MediaItem) -> str | None:
        """URL источника по типу медиа."""
        if item.kind is MediaType.IMAGE:
            return f"{IMAGES_BASE}/image/{item.media_id}"
        if item.kind is MediaType.OK_VIDEO:
            return item.video_url()
        return None

    @staticmethod
    def _target_path(item: MediaItem, images_dir: Path, videos_dir: Path) -> Path | None:
        """Целевой путь файла. ``None`` — если тип не поддерживается."""
        if item.kind is MediaType.IMAGE:
            ext = ".jpg"
            return images_dir / f"{item.short_id}{ext}"
        if item.kind is MediaType.OK_VIDEO:
            ext = ".mp4"
            return videos_dir / f"{item.short_id}{ext}"
        return None

    @staticmethod
    def _format_counters(counters: MediaCounters, blog: str) -> str:
        lines = [
            f"Блог: {blog}",
            f"  Изображений: {counters.images}",
            f"  Видео: {counters.videos}",
            f"  Аудио: {counters.audios} (пока недоступно)",
            f"  Всего поддерживаемого медиа: {counters.total}",
        ]
        return "\n".join(lines)

    @staticmethod
    def _emit(callback: Callable[..., None] | None, *args: object) -> None:
        """Безопасно зовёт колбэк, если он задан."""
        if callback is not None:
            try:
                callback(*args)  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001 — колбэк пользовательский, не падаем.
                log.debug("Колбэк выбросил исключение", exc_info=True)

    @staticmethod
    def _safe_remove(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


# --- Вспомогательные типы -------------------------------------------------


@dataclass(slots=True)
class _DownloadJob:
    """Одно задание: медиа + куда сохранять."""

    item: MediaItem
    target: Path


class _Result:
    """Константы результата скачивания одного файла."""

    DOWNLOADED = "downloaded"
    SKIPPED = "skipped"


def _result_label(result: str) -> str:
    return {"downloaded": "скачан", "skipped": "уже есть"}.get(result, result)
