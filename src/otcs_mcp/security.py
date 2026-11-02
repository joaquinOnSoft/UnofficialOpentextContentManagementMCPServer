# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""Security helpers: client authentication, input validation and output delimiting.

Everything in this module is a security boundary and is held to 100 % branch
coverage by ``make test-security``.
"""

import base64
import re
import secrets
import time
import unicodedata
from collections import deque
from collections.abc import Callable, Collection

from otcs_mcp.errors import InvalidInputError

MAX_AUTH_HEADER_LENGTH = 8192
MAX_NODE_ID = 2**63 - 1
UNTRUSTED_TAG = "untrusted_content"

SEARCH_FORBIDDEN_CHARS = frozenset('"():[]{}\\<>=~^|')

_SOURCE_RE = re.compile(r"[a-z0-9_.-]{1,32}")
_TAG_RE = re.compile(rf"<(/?)({UNTRUSTED_TAG})", re.IGNORECASE)


def parse_basic_header(value: str | None) -> tuple[str, str] | None:
    """Parse an ``Authorization: Basic`` header into ``(user, password)``.

    Returns ``None`` for any malformed, oversized or non-Basic header.
    """
    if not value or len(value) > MAX_AUTH_HEADER_LENGTH:
        return None
    scheme, _, token = value.partition(" ")
    token = token.strip()
    if scheme.lower() != "basic" or not token:
        return None
    try:
        decoded = base64.b64decode(token, validate=True).decode("utf-8")
    except ValueError:  # covers binascii.Error and UnicodeDecodeError
        return None
    user, separator, password = decoded.partition(":")
    if not separator:
        return None
    return user, password


def verify_basic_auth(header: str | None, *, expected_user: str, expected_password: str) -> bool:
    """Check HTTP Basic credentials in constant time.

    Both comparisons always run, so timing does not reveal which part was wrong.
    """
    credentials = parse_basic_header(header)
    supplied_user, supplied_password = credentials if credentials else ("", "")
    user_ok = secrets.compare_digest(supplied_user.encode(), expected_user.encode())
    password_ok = secrets.compare_digest(supplied_password.encode(), expected_password.encode())
    return credentials is not None and user_ok and password_ok


def is_origin_allowed(origin: str | None, allowed_origins: Collection[str]) -> bool:
    """Apply the Origin policy.

    A request without ``Origin`` (non-browser client) is allowed here and must
    still pass authentication. A request that sends ``Origin`` must match the
    allowlist exactly (case-insensitive, trailing slash ignored).
    """
    if origin is None:
        return True
    candidate = origin.strip().rstrip("/").lower()
    return candidate in {item.strip().rstrip("/").lower() for item in allowed_origins}


def validate_node_id(value: object, field: str = "node_id") -> int:
    """Validate an OpenText node identifier (a positive integer)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidInputError(field, "must be an integer")
    if value < 1 or value > MAX_NODE_ID:
        raise InvalidInputError(field, "must be a positive integer")
    return value


def validate_text(value: object, *, field: str, max_length: int, allow_empty: bool = False) -> str:
    """Validate free text: a bounded string without control characters."""
    if not isinstance(value, str):
        raise InvalidInputError(field, "must be a string")
    text = value.strip()
    if not text and not allow_empty:
        raise InvalidInputError(field, "must not be empty")
    if len(text) > max_length:
        raise InvalidInputError(field, f"must be at most {max_length} characters")
    if any(unicodedata.category(char) in {"Cc", "Cs"} for char in text):
        raise InvalidInputError(field, "must not contain control characters")
    return text


def validate_search_text(value: object, *, field: str = "query", max_length: int = 200) -> str:
    """Validate plain search words.

    Characters that carry meaning in OpenText's query language (LQL) are
    rejected so user text can never turn into query syntax. This is defence in
    depth: tools also never use the ``complexquery`` search mode.
    """
    text = validate_text(value, field=field, max_length=max_length)
    if any(char in SEARCH_FORBIDDEN_CHARS for char in text):
        forbidden = " ".join(sorted(SEARCH_FORBIDDEN_CHARS))
        raise InvalidInputError(field, f"must be plain words without any of: {forbidden}")
    return text


def validate_choice(value: object, allowed: Collection[str], field: str) -> str:
    """Validate that ``value`` is one of the allowed strings."""
    if not isinstance(value, str) or value not in allowed:
        options = ", ".join(sorted(allowed))
        raise InvalidInputError(field, f"must be one of: {options}")
    return value


def _validate_positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidInputError(field, "must be an integer")
    if value < 1:
        raise InvalidInputError(field, "must be 1 or greater")
    return value


def validate_page_args(
    page: object, limit: object, *, max_items: int, default_limit: int | None = None
) -> tuple[int, int]:
    """Validate pagination arguments and clamp ``limit`` to ``max_items``.

    ``None`` selects the defaults (page 1, ``default_limit`` or ``max_items``).
    """
    page_number = 1 if page is None else _validate_positive_int(page, "page")
    if limit is None:
        limit_value = default_limit if default_limit is not None else max_items
    else:
        limit_value = _validate_positive_int(limit, "limit")
    return page_number, min(limit_value, max_items)


def wrap_untrusted(content: str, *, source: str, node_id: int | None = None) -> str:
    """Delimit OpenText-provided text so the model treats it as data.

    Any occurrence of the delimiter tag inside the content is neutralised so
    the content cannot close the block and smuggle instructions outside it.
    """
    if not _SOURCE_RE.fullmatch(source):
        raise InvalidInputError("source", "must be 1-32 characters of [a-z0-9_.-]")
    safe_content = _TAG_RE.sub(lambda match: f"&lt;{match.group(1)}{match.group(2)}", content)
    attributes = f'source="{source}"'
    if node_id is not None:
        attributes += f' node_id="{validate_node_id(node_id)}"'
    return f"<{UNTRUSTED_TAG} {attributes}>\n{safe_content}\n</{UNTRUSTED_TAG}>"


class SlidingWindowRateLimiter:
    """Per-key sliding-window rate limiter (single event loop, not thread-safe)."""

    def __init__(
        self,
        limit_per_minute: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        window_seconds: float = 60.0,
    ) -> None:
        self._limit = limit_per_minute
        self._clock = clock
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        """Record a request for ``key`` and return whether it is within the limit."""
        if self._limit == 0:
            return True
        now = self._clock()
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        return True
