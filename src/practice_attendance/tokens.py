"""The tokens of the staff and of each player of each team: derived from the
salt and the team id, never stored (docs/contract.md, *Tokens*), so the links
survive a restart and die with a new salt."""

import hashlib
from pathlib import Path

ALPHABET = "acdefhmnprtu"
LENGTH = 6
COACH_LENGTH = 10


def read_salt(path):
    """The salt file's content, minus surrounding whitespace; it must not be empty."""
    try:
        salt = Path(path).read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ValueError(f"cannot read the salt {path}: {error.strerror}") from None
    if not salt:
        raise ValueError(f"the salt {path} is empty")
    return salt


def _token(text, length=LENGTH):
    """The first `length` base-12 digits of the SHA-256 of `text`."""
    number = int.from_bytes(hashlib.sha256(text.encode()).digest(), "big")
    digits = []
    while number:
        number, digit = divmod(number, len(ALPHABET))
        digits.append(ALPHABET[digit])
    return "".join(reversed(digits))[:length]


def staff_token(salt, team_id):
    return _token(f"{salt}:{team_id}:staff")


def player_token(salt, team_id, player):
    return _token(f"{salt}:{team_id}:player:{player}")


def coach_token(salt, coach_id):
    """The coach's token, longer than the others so that it cannot be mistaken
    for one."""
    return _token(f"{salt}:coach:{coach_id}", COACH_LENGTH)
