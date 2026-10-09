"""An error the hub answers with: an HTTP status, a short message and optional details."""

from typing import Any


class HubError(Exception):
    def __init__(self, status: int, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status, self.extra = status, extra
