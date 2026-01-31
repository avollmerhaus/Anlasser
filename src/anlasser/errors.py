class AnlasserError(ValueError):
    """Base class for Anlasser-specific errors."""


class AnlasserVMError(AnlasserError):
    """Raised for invalid or missing VM config, failure to start or something. Could be split up"""

class AnlasserInvalidMessageError(AnlasserError):
    """Raised when a client message is invalid JSON or fails request validation."""

class AnlasserInvalidActionError(AnlasserError):
    """See valid actions in dispatcher"""

class AnlasserInvalidResponseError(AnlasserError):
    """Raised when a response message is invalid JSON or fails request validation."""