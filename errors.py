"""Public error messages never contain raw provider responses or credentials."""


class MonitorError(Exception):
    pass


class ConfigError(MonitorError):
    pass


class ProviderError(MonitorError):
    pass


class StateError(MonitorError):
    pass


class DeliveryRejected(MonitorError):
    """Telegram explicitly rejected a message; retry on a future run is safe."""


class DeliveryUncertain(MonitorError):
    """A request may have reached Telegram; automatic resend is unsafe."""
