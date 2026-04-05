class AnlasserError(ValueError):
    """Base class for Anlasser-specific errors."""


class AnlasserBhyveControllerError(AnlasserError):
    """Raised for invalid or missing bhyve controller config, failure to start or similar operational problems."""


class AnlasserInvalidMessageError(AnlasserError):
    """Raised when a client message is invalid JSON or fails request validation."""


class AnlasserInvalidActionError(AnlasserError):
    """See valid actions in dispatcher"""


class AnlasserInvalidResponseError(AnlasserError):
    """Raised when a response message is invalid JSON or fails request validation."""


class AnlasserCommandFailedError(AnlasserError):
    """Raised when server returns a valid response with non-2xx status."""
