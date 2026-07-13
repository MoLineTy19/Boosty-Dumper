"""Иерархия исключений приложения.

Все ошибки пакета наследуются от :class:`BoostyError`, что позволяет вызывающему
коду перехватывать их единым блоком ``except BoostyError`` и при этом различать
конкретные типы сбоев.
"""

from __future__ import annotations


class BoostyError(Exception):
    """Базовый класс всех ошибок BoostyDumper."""


# --- Аутентификация и доступ ----------------------------------------------


class AuthError(BoostyError):
    """Токен недействителен или отсутствует (HTTP 401/403)."""


class RateLimitError(BoostyError):
    """Превышен лимит запросов (HTTP 429).

    :attribute retry_after: рекомендованная сервером пауза в секундах (или ``None``).
    """

    def __init__(self, message: str = "Превышен лимит запросов", retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


# --- Сеть и API -----------------------------------------------------------


class NetworkError(BoostyError):
    """Сетевой сбой (таймаут, обрыв соединения, неразрешимый хост)."""


class ApiError(BoostyError):
    """API вернул неожиданный ответ или некорректный JSON.

    :attribute status_code: HTTP-код ответа (если есть);
    :attribute payload:     тело ответа (если удалось разобрать).
    """

    def __init__(self, message: str, status_code: int | None = None, payload: object | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class NotFoundError(BoostyError):
    """Запрошенный блог или медиа не найдены (HTTP 404)."""


# --- Загрузка ------------------------------------------------------------


class DownloadError(BoostyError):
    """Ошибка при скачивании конкретного файла."""

    def __init__(self, message: str, *, url: str | None = None, media_id: str | None = None) -> None:
        super().__init__(message)
        self.url = url
        self.media_id = media_id
