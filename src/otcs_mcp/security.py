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
from collections.abc import Callable, Collection, Mapping

from otcs_mcp.errors import InvalidInputError

MAX_AUTH_HEADER_LENGTH = 8192
MAX_NODE_ID = 2**63 - 1
UNTRUSTED_TAG = "untrusted_content"

MAX_NODE_NAME_LENGTH = 248
MAX_DESCRIPTION_LENGTH = 1000
MAX_CATEGORY_VALUES = 50
MAX_CATEGORY_VALUE_LENGTH = 1000
NODE_NAME_FORBIDDEN_CHARS = frozenset('/\\:*?"<>|')
SEARCH_FORBIDDEN_CHARS = frozenset('"():[]{}\\<>=~^|')

_MIME_RE = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,63}")
_CATEGORY_KEY_RE = re.compile(r"(\d{1,18})_(\d{1,18})")
_TOKEN_RE = re.compile(r"[A-Za-z0-9]{8,64}")
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


def host_without_port(host_header: str) -> str:
    """Return the lower-cased host of a ``Host`` header value, without any port."""
    host = host_header.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        return host[: end + 1] if end != -1 else host
    if host.count(":") == 1:
        return host.split(":", 1)[0]
    return host


def is_host_allowed(host_header: str | None, allowed_hosts: Collection[str]) -> bool:
    """Protect against DNS rebinding: the ``Host`` header must be an allowed host name."""
    if not host_header:
        return False
    return host_without_port(host_header) in {host.strip().lower() for host in allowed_hosts}


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


def validate_node_name(value: object, field: str = "name") -> str:
    """Validate the name of a node to create (also used as the uploaded file name)."""
    text = validate_text(value, field=field, max_length=MAX_NODE_NAME_LENGTH)
    if any(char in NODE_NAME_FORBIDDEN_CHARS for char in text):
        forbidden = " ".join(sorted(NODE_NAME_FORBIDDEN_CHARS))
        raise InvalidInputError(field, f"must not contain any of: {forbidden}")
    if set(text) == {"."}:
        raise InvalidInputError(field, "must not consist only of dots")
    return text


def validate_description(value: object, field: str = "description") -> str | None:
    """Validate an optional node description; blank text becomes ``None``."""
    if value is None:
        return None
    text = validate_text(value, field=field, max_length=MAX_DESCRIPTION_LENGTH, allow_empty=True)
    return text or None


def validate_mime_type(value: object, field: str = "mime_type") -> str | None:
    """Validate an optional ``type/subtype`` media type."""
    if value is None:
        return None
    if not isinstance(value, str) or not _MIME_RE.fullmatch(value.strip().lower()):
        raise InvalidInputError(field, "must look like type/subtype, e.g. application/pdf")
    return value.strip().lower()


def validate_confirmation(confirm: object) -> None:
    """Require an explicit ``true`` for operations that overwrite existing data."""
    if confirm is not True:
        raise InvalidInputError(
            "confirm", "must be true; set it only after the user explicitly approved this change"
        )


def validate_metadata_token(value: object, field: str = "metadata_token") -> str | None:
    """Validate an optional OpenText metadata state token."""
    if value is None:
        return None
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise InvalidInputError(field, "must be an alphanumeric token of 8 to 64 characters")
    return value


def validate_category_values(category_id: int, values: object) -> dict[str, str]:
    """Validate ``{"<category>_<attribute>": "text"}`` updates for one category.

    Every key must belong to ``category_id`` (no cross-category writes); attribute 1
    is reserved by OpenText for the metadata token; complex keys such as set rows or
    multilingual values are not supported.
    """
    if not isinstance(values, Mapping) or not values:
        raise InvalidInputError("values", "must be a non-empty object of attribute values")
    if len(values) > MAX_CATEGORY_VALUES:
        raise InvalidInputError("values", f"must contain at most {MAX_CATEGORY_VALUES} entries")
    cleaned: dict[str, str] = {}
    for key, value in values.items():
        match = _CATEGORY_KEY_RE.fullmatch(key) if isinstance(key, str) else None
        if match is None or match.group(1) != str(category_id):
            raise InvalidInputError(
                "values", f"keys must look like '{category_id}_<attribute id>' for this category"
            )
        if match.group(2) == "1":
            raise InvalidInputError(
                "values", "attribute 1 is reserved for the metadata token; use metadata_token"
            )
        cleaned[key] = validate_text(
            value, field=f"values.{key}", max_length=MAX_CATEGORY_VALUE_LENGTH, allow_empty=True
        )
    return cleaned


def decode_upload(content_text: object, content_base64: object, max_bytes: int) -> bytes:
    """Return the bytes to upload from exactly one of ``content_text``/``content_base64``."""
    if (content_text is None) == (content_base64 is None):
        raise InvalidInputError("content", "provide exactly one of content_text or content_base64")
    if content_text is not None:
        if not isinstance(content_text, str):
            raise InvalidInputError("content_text", "must be a string")
        data = content_text.encode("utf-8")
    else:
        if not isinstance(content_base64, str):
            raise InvalidInputError("content_base64", "must be a string")
        compact = "".join(content_base64.split())
        if len(compact) * 3 // 4 > max_bytes + 2:  # reject before decoding anything large
            raise InvalidInputError("content_base64", f"must decode to at most {max_bytes} bytes")
        try:
            data = base64.b64decode(compact, validate=True)
        except ValueError:
            raise InvalidInputError("content_base64", "must be valid base64") from None
    if not data:
        raise InvalidInputError("content", "must not be empty")
    if len(data) > max_bytes:
        raise InvalidInputError("content", f"must be at most {max_bytes} bytes")
    return data


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
