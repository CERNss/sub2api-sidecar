from __future__ import annotations


class ProvisioningError(Exception):
    """Base exception for provisioning orchestration errors."""


class FlowNotFoundError(ProvisioningError):
    """Raised when a flow cannot be found from the provided identifier."""


class InvalidOAuthStateError(ProvisioningError):
    """Raised when oauth state is missing or invalid."""


class ConfigurationError(ProvisioningError):
    """Raised when required application configuration is missing or invalid."""


class InvalidOAuthCallbackPayloadError(ProvisioningError):
    """Raised when pasted callback data cannot be parsed into code/state."""
