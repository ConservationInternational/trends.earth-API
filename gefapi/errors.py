"""GEF API ERRORS"""


class Error(Exception):
    def __init__(self, message):
        self.message = message

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
