import pyperclip


def script(*args: str) -> None:
    """Put the first argument on the clipboard."""
    pyperclip.copy(args[0])
