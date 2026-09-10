"""Privacy-preserving compromised-password screening via HIBP ranges.

Only the first five characters of an uppercase SHA-1 digest leave this
process.  Call this only after a user submits a complete password form; do
not expose it as a keystroke-by-keystroke browser endpoint.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from urllib.error import URLError
from urllib.request import Request, urlopen

RANGE_URL = "https://api.pwnedpasswords.com/range/"
DEFAULT_TIMEOUT_SECONDS = 3
USER_AGENT = "MARKETSHELPER-password-safety/1.0"


class PasswordBreachCheckError(RuntimeError):
    """The remote compromised-password check could not be completed safely."""


def pwned_password_count(
    password: str,
    *,
    opener: Callable | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> int:
    """Return the public breach count for ``password`` using an HIBP range.

    The response is parsed in memory and discarded.  Neither the password nor
    its full digest is included in an exception, URL, or return value.
    """
    digest = hashlib.sha1((password or "").encode("utf-8")).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]
    request = Request(
        RANGE_URL + prefix,
        headers={"Add-Padding": "true", "User-Agent": USER_AGENT},
    )
    transport = opener or urlopen
    try:
        with transport(request, timeout=max(1, int(timeout_seconds))) as response:
            body = response.read().decode("ascii")
    except (OSError, URLError, UnicodeError, ValueError) as exc:
        raise PasswordBreachCheckError("Не удалось проверить пароль по базе утечек.") from exc

    for line in body.splitlines():
        candidate_suffix, separator, count = line.partition(":")
        if separator and candidate_suffix.upper() == suffix:
            try:
                return int(count)
            except ValueError as exc:
                raise PasswordBreachCheckError("Не удалось проверить пароль по базе утечек.") from exc
    return 0
