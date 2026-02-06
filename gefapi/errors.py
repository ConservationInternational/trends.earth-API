"""GEF API ERRORS"""


class Error(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(message)

    @property
    def serialize(self):
        return {"message": self.message}


class UserNotFound(Error):
    pass


class UserDuplicated(Error):
    pass


class AuthError(Error):
    pass


class InvalidFile(Error):
    pass


class ScriptNotFound(Error):
    pass


class ScriptDuplicated(Error):
    pass


class NotAllowed(Error):
    pass


class ExecutionNotFound(Error):
    pass


class ScriptStateNotValid(Error):
    pass


class EmailError(Error):
    pass


class PasswordValidationError(Error):
    pass


class AccountLockedError(Error):
    """Raised when a user account is locked due to too many failed login attempts."""

    def __init__(
        self,
        message: str,
        minutes_remaining: int | None = None,
        requires_password_reset: bool = False,
    ):
        super().__init__(message)
        self.minutes_remaining = minutes_remaining
        self.requires_password_reset = requires_password_reset

    @property
    def serialize(self):
        return {
            "message": self.message,
            "error_code": "account_locked",
            "minutes_remaining": self.minutes_remaining,
            "requires_password_reset": self.requires_password_reset,
        }
