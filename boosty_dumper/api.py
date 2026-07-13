"""Клиент Boosty API.

Объединяет всю работу с сетью в одном классе :class:`BoostyClient`:

* ``requests.Session`` с повторами (``urllib3.util.Retry``) для временных сбоев;
* единый таймаут на каждый запрос;
* маппинг HTTP-кодов в типизированные исключения (см. :mod:`boosty_dumper.exceptions`);
* корректная постраничная пагинация постов (исходный код пытался получить
  всё одним запросом с ``limit=count``, что работает ненадёжно при больших блогах).

Идея: слой API ничего не знает про GUI или файловую систему — он только
получает данные и возвращает модели из :mod:`boosty_dumper.models`.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterator

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .exceptions import ApiError, AuthError, NetworkError, NotFoundError, RateLimitError
from .models import MediaCounters, Post

log = logging.getLogger(__name__)

API_BASE = "https://api.boosty.to/v1"
IMAGES_BASE = "https://images.boosty.to"

# Размер страницы постов. Boosty отдаёт посты постранично; берём с запасом,
# но не слишком много, чтобы не упереться в лимиты сервера.
POST_PAGE_LIMIT = 50
# Максимум страниц «впустую» (пустой ответ) подряд, после которого остановимся.
_MAX_EMPTY_PAGES = 3


def _default_headers(token: str) -> dict[str, str]:
    """Базовый набор заголовков. ``token`` подставляется как Bearer."""
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Authorization": f"Bearer {token}",
        "Connection": "keep-alive",
        "Origin": "https://boosty.to",
        "Referer": "https://boosty.to/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
        ),
        "X-App": "web",
        "X-Currency": "RUB",
        "X-Locale": "en_US",
    }


class BoostyClient:
    """Тонкий типизированный клиент над Boosty API.

    Использование::

        client = BoostyClient(token="...")
        counters = client.get_media_counters("blog_name")
        for post in client.iter_posts("blog_name"):
            ...
    """

    def __init__(
        self,
        token: str,
        *,
        timeout: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        if not token or not token.strip():
            raise AuthError("Bearer-токен пустой — аутентификация невозможна.")
        self._token = token.strip()
        self._timeout = timeout
        self._session = session or self._build_session()

    # --- Жизненный цикл --------------------------------------------------

    def close(self) -> None:
        """Закрывает сессию и освобождает соединения."""
        self._session.close()

    def __enter__(self) -> "BoostyClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- Публичные методы ------------------------------------------------

    def get_media_counters(self, blog: str) -> MediaCounters:
        """Возвращает счётчики медиа для блога.

        Endpoint ``/v1/blog/<blog>/media_album/counters/``. 401 здесь
        достоверно означает невалидный токен.
        """
        data = self._get(
            f"/blog/{blog}/media_album/counters/",
            params={"only_allowed": "true"},
            auth_sensitive=True,
        )
        return MediaCounters.from_api(data)

    def iter_posts(self, blog: str) -> Iterator[Post]:
        """Лениво обходит **все** посты блога с медиа, страница за страницей.

        Заменяет прежний подход «один запрос с ``limit=total``». Пагинация
        идёт через ``offset`` до тех пор, пока сервер не перестанет возвращать
        новые посты (или мы не упрёмся в лимит пустых страниц подряд).
        """
        offset = 0
        empty_streak = 0
        seen_ids: set[str] = set()
        while True:
            data = self._get(
                f"/blog/{blog}/media_album/",
                params={
                    "type": "all",
                    "limit": str(POST_PAGE_LIMIT),
                    "offset": str(offset),
                    "limit_by": "media",
                    "only_allowed": "true",
                },
            )
            posts_raw: list[dict[str, Any]] = (
                data.get("data", {}).get("mediaPosts", []) if isinstance(data, dict) else []
            )
            if not posts_raw:
                empty_streak += 1
                if empty_streak >= _MAX_EMPTY_PAGES:
                    log.debug("Получено %d пустых страниц подряд — стоп пагинации.", empty_streak)
                    break
                # Сдвигаем offset и пробуем ещё раз — иногда сервер возвращает
                # пустую страницу «в середине» выдачи.
                offset += POST_PAGE_LIMIT
                continue
            empty_streak = 0

            new_on_page = 0
            for raw in posts_raw:
                post = Post.from_api(raw)
                if post.post_id and post.post_id in seen_ids:
                    continue
                seen_ids.add(post.post_id)
                new_on_page += 1
                yield post

            if new_on_page == 0:
                # Страница была, но все посты уже видели — данные закончились.
                break
            offset += POST_PAGE_LIMIT
            # Вежливая пауза, чтобы не долбить API.
            time.sleep(0.15)

    def download_stream(self, url: str, *, chunk_size: int = 1024) -> Iterator[bytes]:
        """Стримит тело ответа чанками. Используется загрузчиком."""
        response = self._session.get(url, stream=True, timeout=self._timeout)
        self._raise_for_status(response, url=url)
        try:
            for chunk in response.iter_content(chunk_size=chunk_size):
                if chunk:
                    yield chunk
        finally:
            response.close()

    # --- Внутренние хелперы ---------------------------------------------

    def _build_session(self) -> requests.Session:
        """Создаёт сессию с retry-стратегией на временные сбои."""
        session = requests.Session()
        session.headers.update(_default_headers(self._token))

        retry = Retry(
            total=3,
            backoff_factor=1.5,
            status_forcelist=(500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "POST"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _get(self, path: str, *, params: dict[str, str] | None = None, auth_sensitive: bool = False) -> dict[str, Any]:
        """GET-запрос к API с разбором JSON и маппингом ошибок.

        :param auth_sensitive: если True, 401 трактуется как невалидный токен
            (используется для counters — первого проверочного запроса).
        """
        url = f"{API_BASE}{path}"
        try:
            response = self._session.get(url, params=params, timeout=self._timeout)
        except requests.Timeout as exc:
            raise NetworkError(f"Таймаут запроса к {url}") from exc
        except requests.ConnectionError as exc:
            raise NetworkError(f"Нет соединения с {url}: {exc}") from exc
        except requests.RequestException as exc:
            raise NetworkError(f"Ошибка запроса к {url}: {exc}") from exc

        self._raise_for_status(response, url=url, auth_sensitive=auth_sensitive)

        try:
            data = response.json()
        except ValueError as exc:
            raise ApiError(
                f"Некорректный JSON от {url}",
                status_code=response.status_code,
            ) from exc
        if not isinstance(data, dict):
            raise ApiError(f"Ожидался объект в ответе {url}", status_code=response.status_code, payload=data)
        return data

    @staticmethod
    def _raise_for_status(
        response: requests.Response,
        *,
        url: str,
        auth_sensitive: bool = False,
    ) -> None:
        """Превращает HTTP-ошибки в типизированные исключения."""
        if response.ok:
            return
        status = response.status_code
        if status in (401, 403):
            # 403 иногда означает «нет подписки», но для нас это одно поле боя.
            raise AuthError(f"Доступ запрещён (HTTP {status}). Проверьте токен." if auth_sensitive
                            else f"Доступ запрещён (HTTP {status}) для {url}.")
        if status == 404:
            raise NotFoundError(f"Не найдено (HTTP 404): {url}")
        if status == 429:
            retry_after = BoostyClient._parse_retry_after(response)
            raise RateLimitError(
                f"Превышен лимит запросов (HTTP 429) для {url}",
                retry_after=retry_after,
            )
        if 500 <= status < 600:
            raise ApiError(f"Серверная ошибка (HTTP {status}) для {url}", status_code=status)
        # Прочие 4xx — общий API-ошибкой.
        raise ApiError(f"Неожиданный ответ (HTTP {status}) от {url}", status_code=status)

    @staticmethod
    def _parse_retry_after(response: requests.Response) -> float | None:
        """Пытается вытащить ``Retry-After`` (в секундах)."""
        header = response.headers.get("Retry-After")
        if not header:
            return None
        try:
            return float(header)
        except ValueError:
            return None
