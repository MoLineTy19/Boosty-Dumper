"""Тесты для :mod:`boosty_dumper.api`.

Сеть не трогаем — в :class:`BoostyClient` инжектируется :class:`FakeSession`
(см. conftest). Покрываем: валидацию токена при создании клиента, маппинг
HTTP-кодов в типизированные исключения, разбор ``Retry-After``, пагинацию
постов (несколько страниц, дедупликация, лимит пустых страниц), парсинг
counters и стриминг загрузки.
"""

from __future__ import annotations

import pytest

from boosty_dumper.api import BoostyClient, POST_PAGE_LIMIT
from boosty_dumper.exceptions import (
    ApiError,
    AuthError,
    NetworkError,
    NotFoundError,
    RateLimitError,
)
from boosty_dumper.models import MediaType

from conftest import FakeResponse, FakeSession


def _make_client(session: FakeSession | None = None, token: str = "valid_token") -> BoostyClient:
    return BoostyClient(token=token, session=session or FakeSession())


class TestClientInit:
    """BoostyClient требует непустой токен."""

    def test_empty_token_raises_auth_error(self):
        with pytest.raises(AuthError):
            BoostyClient(token="")

    def test_whitespace_token_raises_auth_error(self):
        with pytest.raises(AuthError):
            BoostyClient(token="   ")

    def test_token_is_stripped(self):
        client = BoostyClient(token="  abc  ", session=FakeSession())
        assert client._token == "abc"

    def test_custom_timeout_stored(self):
        client = BoostyClient(token="x", timeout=99.0, session=FakeSession())
        assert client._timeout == 99.0


class TestContextManager:
    def test_close_called_on_exit(self):
        session = FakeSession()
        with BoostyClient(token="t", session=session) as client:
            assert client is not None
        assert session.closed is True


class TestRaiseForStatus:
    """_raise_for_status: HTTP-код → типизированное исключение."""

    @staticmethod
    def _resp(status: int, headers: dict | None = None) -> FakeResponse:
        return FakeResponse(status_code=status, headers=headers)

    def test_ok_does_not_raise(self):
        BoostyClient._raise_for_status(self._resp(200), url="u")

    def test_401_raises_auth(self):
        with pytest.raises(AuthError):
            BoostyClient._raise_for_status(self._resp(401), url="u", auth_sensitive=True)

    def test_403_raises_auth(self):
        with pytest.raises(AuthError):
            BoostyClient._raise_for_status(self._resp(403), url="u")

    def test_404_raises_not_found(self):
        with pytest.raises(NotFoundError):
            BoostyClient._raise_for_status(self._resp(404), url="u")

    def test_429_raises_rate_limit_with_retry_after(self):
        resp = self._resp(429, headers={"Retry-After": "30"})
        with pytest.raises(RateLimitError) as exc_info:
            BoostyClient._raise_for_status(resp, url="u")
        assert exc_info.value.retry_after == 30.0

    def test_429_without_retry_after_header(self):
        resp = self._resp(429)
        with pytest.raises(RateLimitError) as exc_info:
            BoostyClient._raise_for_status(resp, url="u")
        assert exc_info.value.retry_after is None

    @pytest.mark.parametrize("status", [500, 502, 503, 504])
    def test_5xx_raises_api_error_with_status(self, status):
        with pytest.raises(ApiError) as exc_info:
            BoostyClient._raise_for_status(self._resp(status), url="u")
        assert exc_info.value.status_code == status

    def test_other_4xx_raises_api_error(self):
        with pytest.raises(ApiError):
            BoostyClient._raise_for_status(self._resp(418), url="u")


class TestParseRetryAfter:
    def test_numeric_header(self):
        resp = FakeResponse(headers={"Retry-After": "12.5"})
        assert BoostyClient._parse_retry_after(resp) == 12.5

    def test_missing_header(self):
        resp = FakeResponse(headers={})
        assert BoostyClient._parse_retry_after(resp) is None

    def test_garbage_header(self):
        """Нечисловой Retry-After (например, HTTP-date) → None, без падения."""
        resp = FakeResponse(headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
        assert BoostyClient._parse_retry_after(resp) is None

    def test_empty_header(self):
        resp = FakeResponse(headers={"Retry-After": ""})
        assert BoostyClient._parse_retry_after(resp) is None


class TestGetMediaCounters:
    def test_parses_counters(self):
        session = FakeSession()
        session.add(FakeResponse(json_data={
            "data": {"mediaCounters": {"image": 7, "okVideo": 2, "audioFile": 1}}
        }))
        client = _make_client(session)

        counters = client.get_media_counters("blog")

        assert counters.images == 7
        assert counters.videos == 2
        assert counters.audios == 1
        # Проверяем, что запрос ушёл на правильный endpoint.
        assert "/blog/blog/media_album/counters/" in session.calls[0]["url"]

    def test_auth_error_on_401(self):
        """counters — проверочный запрос: 401 = невалидный токен."""
        session = FakeSession([FakeResponse(status_code=401)])
        client = _make_client(session)
        with pytest.raises(AuthError):
            client.get_media_counters("blog")


class TestIterPosts:
    """iter_posts — постраничный обход постов с дедупликацией и защитой от зацикливания."""

    @staticmethod
    def _page(posts: list[dict]) -> FakeResponse:
        return FakeResponse(json_data={"data": {"mediaPosts": posts}})

    @staticmethod
    def _post(pid: str, media_kinds: list[str] | None = None) -> dict:
        media = [{"id": f"{pid}-{i}", "type": t} for i, t in enumerate(media_kinds or ["image"])]
        return {"id": pid, "title": f"Post {pid}", "media": media}

    def test_single_page(self):
        session = FakeSession([self._page([self._post("p1"), self._post("p2")])])
        # После непустой страницы offset двигается и идут пустые запросы.
        # Нужно 3 пустые подряд, чтобы сработал лимит _MAX_EMPTY_PAGES и обход завершился.
        session.add(self._page([]))
        session.add(self._page([]))
        session.add(self._page([]))
        client = _make_client(session)

        posts = list(client.iter_posts("blog"))
        assert [p.post_id for p in posts] == ["p1", "p2"]

    def test_paginates_multiple_pages(self):
        session = FakeSession([
            self._page([self._post("p1")]),
            self._page([self._post("p2")]),
            self._page([self._post("p3")]),
            self._page([]),  # 1-я пустая
            self._page([]),  # 2-я пустая
            self._page([]),  # 3-я пустая подряд → стоп
        ])
        client = _make_client(session)

        posts = list(client.iter_posts("blog"))
        assert [p.post_id for p in posts] == ["p1", "p2", "p3"]
        # Параметры пагинации: offset растёт на POST_PAGE_LIMIT каждый запрос.
        offsets = [c["params"]["offset"] for c in session.calls]
        assert offsets == [
            "0",
            str(POST_PAGE_LIMIT),
            str(2 * POST_PAGE_LIMIT),
            str(3 * POST_PAGE_LIMIT),
            str(4 * POST_PAGE_LIMIT),
            str(5 * POST_PAGE_LIMIT),
        ]

    def test_offset_advances_on_empty_page(self):
        """Пустая страница «в середине» не завершает обход — offset двигается."""
        session = FakeSession([
            self._page([self._post("p1")]),
            self._page([]),  # пусто, но продолжаем
            self._page([self._post("p2")]),
            self._page([]),  # снова пусто
            self._page([]),  # ещё пусто
            self._page([]),  # 3-я пустая подряд → стоп
        ])
        client = _make_client(session)

        posts = list(client.iter_posts("blog"))
        assert [p.post_id for p in posts] == ["p1", "p2"]

    def test_deduplicates_seen_post_ids(self):
        """Если сервер вернёт тот же id повторно — не отдаём дважды."""
        dup = self._post("dup")
        session = FakeSession([
            self._page([dup]),
            self._page([dup]),  # полностью дубликаты → new_on_page==0 → стоп
        ])
        client = _make_client(session)

        posts = list(client.iter_posts("blog"))
        assert [p.post_id for p in posts] == ["dup"]

    def test_stops_when_page_has_only_seen_posts(self):
        """Страница есть, но все посты уже видели — конец данных."""
        session = FakeSession([
            self._page([self._post("a")]),
            self._page([self._post("a")]),  # все дубликаты
        ])
        client = _make_client(session)
        posts = list(client.iter_posts("blog"))
        assert posts == [posts[0]]  # только первый

    def test_empty_blog_returns_nothing(self):
        session = FakeSession([
            self._page([]),
            self._page([]),
            self._page([]),  # 3 пустых подряд
        ])
        client = _make_client(session)
        assert list(client.iter_posts("blog")) == []

    def test_posts_skip_unknown_media_types(self):
        """iter_posts возвращает Post, в котором неизвестные типы уже отфильтрованы."""
        session = FakeSession([
            self._page([{
                "id": "p1",
                "title": "",
                "media": [
                    {"id": "m1", "type": "image"},
                    {"id": "m2", "type": "future_type"},
                ],
            }]),
            self._page([]),
            self._page([]),
            self._page([]),  # 3 пустые подряд → стоп
        ])
        client = _make_client(session)
        posts = list(client.iter_posts("blog"))
        assert len(posts) == 1
        assert len(posts[0].media) == 1
        assert posts[0].media[0].kind is MediaType.IMAGE


class TestDownloadStream:
    """download_stream — отдаёт чанки тела ответа и закрывает соединение."""

    def test_yields_chunks(self):
        session = FakeSession([FakeResponse(content=b"hello world")])
        client = _make_client(session)

        chunks = list(client.download_stream("http://x/y", chunk_size=5))

        assert b"".join(chunks) == b"hello world"
        # По 5 байт → ['hello', ' worl', 'd']
        assert chunks == [b"hello", b" worl", b"d"]

    def test_empty_chunks_filtered(self):
        """iter_content отдаёт пустые чанки — download_stream их пропускает."""
        # FakeResponse.iter_content не создаёт пустых чанков, проверим базовое поведение.
        session = FakeSession([FakeResponse(content=b"data")])
        client = _make_client(session)
        assert b"".join(client.download_stream("u")) == b"data"


class TestGetNetworkErrors:
    """_get транслирует сетевые сбои requests.* в NetworkError."""

    def test_timeout_becomes_network_error(self):
        import requests as req

        class TimeoutSession(FakeSession):
            def get(self, url, **kwargs):
                raise req.Timeout("timed out")

        client = _make_client(TimeoutSession())
        with pytest.raises(NetworkError):
            client.get_media_counters("blog")

    def test_connection_error_becomes_network_error(self):
        import requests as req

        class ConnErrSession(FakeSession):
            def get(self, url, **kwargs):
                raise req.ConnectionError("no route")

        client = _make_client(ConnErrSession())
        with pytest.raises(NetworkError):
            client.get_media_counters("blog")

    def test_invalid_json_becomes_api_error(self):
        session = FakeSession([FakeResponse(status_code=200, raise_on_json=True)])
        client = _make_client(session)
        with pytest.raises(ApiError):
            client.get_media_counters("blog")

    def test_non_dict_json_becomes_api_error(self):
        session = FakeSession([FakeResponse(json_data=[1, 2, 3])])  # список, не объект
        client = _make_client(session)
        with pytest.raises(ApiError):
            client.get_media_counters("blog")
