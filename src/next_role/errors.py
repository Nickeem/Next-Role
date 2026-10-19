"""Domain-level exceptions. The API layer maps these to HTTP responses."""


class DomainError(Exception):
    """Base class for expected, user-facing failures."""

    code = "domain_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(DomainError):
    code = "not_found"


class ConflictError(DomainError):
    code = "conflict"


class InvalidInputError(DomainError):
    code = "invalid_input"


class RateLimitedError(DomainError):
    code = "rate_limited"
