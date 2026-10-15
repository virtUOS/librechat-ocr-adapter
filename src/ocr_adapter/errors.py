"""Exceptions that map to HTTP responses. The API layer turns them into
{"message": ..., "type": ...} bodies with the status given here."""


class AdapterError(Exception):
    status = 500
    type = "internal_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InvalidRequest(AdapterError):
    status = 400
    type = "invalid_request"


class NotFound(AdapterError):
    status = 404
    type = "not_found"


class TooLarge(AdapterError):
    status = 413
    type = "too_large"


class UnsupportedMediaType(AdapterError):
    status = 415
    type = "unsupported_media_type"


class Unprocessable(AdapterError):
    status = 422
    type = "unprocessable"


class UpstreamError(AdapterError):
    status = 502
    type = "upstream_error"


class UpstreamUnavailable(AdapterError):
    status = 503
    type = "upstream_unavailable"


class DocumentTimeout(AdapterError):
    status = 504
    type = "timeout"
