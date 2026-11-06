# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

import pytest

from otcs_mcp import errors


@pytest.mark.parametrize(
    "error_class",
    [
        errors.OtcsAuthError,
        errors.OtcsPermissionError,
        errors.OtcsNotFoundError,
        errors.OtcsBadRequestError,
        errors.OtcsConflictError,
        errors.OtcsRateLimitError,
        errors.OtcsServerError,
        errors.OtcsTimeoutError,
        errors.OtcsConnectionError,
        errors.OtcsResponseError,
    ],
)
def test_otcs_errors_derive_from_otcs_error_when_defined(
    error_class: type[errors.OtcsError],
) -> None:
    assert issubclass(error_class, errors.OtcsError)
    assert issubclass(error_class, errors.McpServerError)


def test_otcs_error_keeps_status_code_when_given() -> None:
    error = errors.OtcsNotFoundError("missing", status_code=404)

    assert error.status_code == 404
    assert str(error) == "missing"


def test_rate_limit_error_keeps_retry_after_when_given() -> None:
    error = errors.OtcsRateLimitError("slow down", status_code=429, retry_after=3.0)

    assert error.retry_after == 3.0
    assert error.status_code == 429


def test_invalid_input_error_names_field_without_echoing_value() -> None:
    error = errors.InvalidInputError("node_id", "must be a positive integer")

    assert error.field == "node_id"
    assert str(error) == "Invalid value for 'node_id': must be a positive integer"


def test_config_error_is_not_an_otcs_error() -> None:
    assert not issubclass(errors.ConfigError, errors.OtcsError)
