"""Why the proxy refuses a request. Messages name fields and types, never values."""


class RequestRejected(Exception):  # noqa: N818  (reads as what happened)
    def __init__(self, status: int, error_type: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.error_type = error_type
        self.message = message


def invalid(message: str) -> RequestRejected:
    """400: the request is malformed."""
    return RequestRejected(400, "invalid_request_error", message)


def unsupported(message: str) -> RequestRejected:
    """422: well-formed, but holds something the gateway cannot mask (ADR 0019)."""
    return RequestRejected(422, "unsupported_content", message)
