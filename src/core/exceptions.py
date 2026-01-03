class TLUError(Exception):
    """Base exception for TLU Client."""
    pass

class LoginError(TLUError):
    """Raised when login fails."""
    pass

class NetworkError(TLUError):
    """Raised when network requests fail."""
    pass

class SessionExpiredError(TLUError):
    """Raised when session token is invalid or expired."""
    pass
