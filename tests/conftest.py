"""Общие фикстуры и хелперы для тестов BoostyDumper.

FakeSession — заглушка :class:`requests.Session`, которая возвращает заранее
заготовленные ответы по очереди. Это позволяет тестировать
:class:`boosty_dumper.api.BoostyClient` без сети — именно для этого конструктор
клиента принимает ``session`` (dependency injection).
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Ускоряет тесты: убирает настоящие паузы time.sleep (включая вежливую
    паузу пагинации в BoostyClient.iter_posts)."""
    import time
    monkeypatch.setattr(time, "sleep", lambda *_args, **_kwargs: None)


class FakeResponse:
    """Минимальная имитация :class:`requests.Response`."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        json_data: Any = None,
        headers: dict[str, str] | None = None,
        content: bytes = b"",
        raise_on_json: bool = False,
    ) -> None:
        self.status_code = status_code
        self._json = json_data
        self.headers = headers or {}
        self._content = content
        self._raise_on_json = raise_on_json
        self.closed = False
        # Имитируем requests.Response.ok.
        self.ok = 200 <= status_code < 400

    def json(self) -> Any:
        if self._raise_on_json:
            raise ValueError("invalid json")
        return self._json

    def iter_content(self, chunk_size: int = 1) -> list[bytes]:
        """Возвращает контент одним или несколькими чанками."""
        if self._raise_on_json:
            return []
        if not self._content:
            return []
        # Бьём на чанки заданного размера — как настоящий iter_content.
        return [
            self._content[i:i + chunk_size]
            for i in range(0, len(self._content), max(chunk_size, 1))
        ]

    def close(self) -> None:
        self.closed = True


class FakeSession:
    """Заглушка сессии: отдаёт ответы по очереди или по совпадению URL."""

    def __init__(self, responses: list[FakeResponse] | None = None) -> None:
        self._responses: list[FakeResponse] = list(responses or [])
        self.calls: list[dict[str, Any]] = []
        self.headers: dict[str, str] = {}
        self.closed = False

    def add(self, response: FakeResponse) -> None:
        self._responses.append(response)

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if not self._responses:
            raise AssertionError(
                f"FakeSession: нет заготовленного ответа для запроса {url}. "
                f"Добавьте response через .add()."
            )
        return self._responses.pop(0)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_response() -> type[FakeResponse]:
    """Экспонирует класс-фабрику для использования в тестах."""
    return FakeResponse


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


class FakeBoostyClient:
    """Заглушка :class:`boosty_dumper.api.BoostyClient` для тестов загрузчика.

    Не ходит в сеть: счётчики/посты/контент задаются явно в конструкторе.
    """

    def __init__(
        self,
        *,
        counters: "MediaCounters | None" = None,
        posts: "list[Post] | None" = None,
        content_by_url: dict[str, bytes] | None = None,
    ) -> None:
        from boosty_dumper.models import MediaCounters
        self._counters = counters or MediaCounters(images=0, videos=0, audios=0)
        self._posts = posts or []
        self._content_by_url = content_by_url or {}
        self.stream_calls: list[str] = []

    def get_media_counters(self, blog: str):
        return self._counters

    def iter_posts(self, blog: str):
        return iter(list(self._posts))

    def download_stream(self, url: str, *, chunk_size: int = 1024):
        self.stream_calls.append(url)
        content = self._content_by_url.get(url, b"")
        for i in range(0, len(content), max(chunk_size, 1)):
            yield content[i:i + max(chunk_size, 1)]


@pytest.fixture
def fake_client_class():
    """Класс-фабрика fake-клиента (используется тестами downloader)."""
    return FakeBoostyClient
