"""Тесты для :mod:`boosty_dumper.exceptions`.

Иерархия спроектирована так, чтобы любой сбой ловился единым
``except BoostyError``. Проверяем наследование и полезные атрибуты
конкретных классов (retry_after, status_code, payload, url, media_id).
"""

from __future__ import annotations

import pytest

from boosty_dumper.exceptions import (
    ApiError,
    AuthError,
    BoostyError,
    DownloadError,
    NetworkError,
    NotFoundError,
    RateLimitError,
)


class TestExceptionHierarchy:
    """Все ошибки пакета наследуются от BoostyError — единый catch."""

    @pytest.mark.parametrize("exc_cls", [
        AuthError, RateLimitError, NetworkError, ApiError,
        NotFoundError, DownloadError,
    ])
    def test_subclass_of_boosty_error(self, exc_cls):
        assert issubclass(exc_cls, BoostyError)

    def test_boosty_error_is_exception(self):
        assert issubclass(BoostyError, Exception)

    def test_caught_by_base(self):
        """Любую конкретную ошибку ловит ``except BoostyError``."""
        for exc in [AuthError("x"), RateLimitError(), NetworkError(),
                    ApiError("x"), NotFoundError("x"), DownloadError("x")]:
            try:
                raise exc
            except BoostyError as caught:
                assert caught is exc


class TestRateLimitError:
    def test_default_retry_after_is_none(self):
        err = RateLimitError()
        assert err.retry_after is None

    def test_custom_retry_after(self):
        err = RateLimitError(retry_after=42.5)
        assert err.retry_after == 42.5

    def test_message_customizable(self):
        err = RateLimitError("custom message")
        assert "custom message" in str(err)


class TestApiError:
    def test_defaults_none(self):
        err = ApiError("boom")
        assert err.status_code is None
        assert err.payload is None
        assert "boom" in str(err)

    def test_with_status_and_payload(self):
        err = ApiError("bad", status_code=502, payload={"k": "v"})
        assert err.status_code == 502
        assert err.payload == {"k": "v"}


class TestDownloadError:
    def test_defaults_none(self):
        err = DownloadError("fail")
        assert err.url is None
        assert err.media_id is None

    def test_with_context(self):
        err = DownloadError("fail", url="http://x/y", media_id="m1")
        assert err.url == "http://x/y"
        assert err.media_id == "m1"
