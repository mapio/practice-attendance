"""The contract's error body, `{"error": code, "detail": text}`."""


class ApiError(Exception):
    def __init__(self, status, code, detail):
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail


def not_found():
    return ApiError(404, "not_found", "not found")


def invalid(detail):
    return ApiError(422, "invalid_value", detail)
