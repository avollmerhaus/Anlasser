class AnlasserError(ValueError):
    """Base class for Anlasser-specific errors."""


class AnlasserInvalidVMConfigError(AnlasserError):
    """Raised for invalid VM config option, duplicate MAC, or similar issues"""

class AnlasserInvalidActionError(AnlasserError):
    """See valid actions in dispatcher"""
