"""The tokens of the staff and of each player: derived from the salt, never
stored (docs/contract.md, *Tokens*), so the links survive a restart and die with
a new salt."""

import hashlib
from pathlib import Path

ALPHABET = "acdefhmnprtu"
LENGTH = 6


def read_salt(path):
    """The salt file's content, minus surrounding whitespace; it must not be empty."""
    try:
        salt = Path(path).read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ValueError(f"cannot read the salt {path}: {error.strerror}") from None
    if not salt:
        raise ValueError(f"the salt {path} is empty")
    return salt


def _token(text):
    """The first six base-12 digits of the SHA-256 of `text`."""
    number = int.from_bytes(hashlib.sha256(text.encode()).digest(), "big")
    digits = []
    while number:
        number, digit = divmod(number, len(ALPHABET))
        digits.append(ALPHABET[digit])
    return "".join(reversed(digits))[:LENGTH]


def staff_token(salt):
    return _token(f"{salt}:staff")


def player_token(salt, player):
    return _token(f"{salt}:player:{player}")
