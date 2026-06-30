from __future__ import annotations


class AuthenticationError(Exception):
    """Raised when authentication fails (bad credential, disabled consumer)."""


class AuthorizationError(Exception):
    """Raised when an authenticated principal lacks a required scope."""
