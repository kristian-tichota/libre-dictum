class LibreDictumError(Exception):
    """Base class for every error raised by libre-dictum."""


class ConfigError(LibreDictumError):
    """The configuration is missing something or contradicts itself."""


class CommandSyntaxError(LibreDictumError):
    """A response string could not be parsed into an executable token list."""


class CameraError(LibreDictumError):
    """The webcam could not be opened, or stopped delivering frames."""


class ProtocolError(LibreDictumError):
    """A display socket frame or path is unusable."""


class AlreadyRunningError(LibreDictumError):
    """Another session is already running for this user."""


class DeviceError(LibreDictumError):
    """The virtual input devices could not be opened."""
