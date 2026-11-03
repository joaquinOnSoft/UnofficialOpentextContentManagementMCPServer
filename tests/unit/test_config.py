# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

from pathlib import Path

import pytest

from otcs_mcp.config import LogFormat, LogLevel, Settings, Transport, load_settings
from otcs_mcp.errors import ConfigError

MIN_ENV = {
    "OTCS_BASE_URL": "https://otcs.example.com/cs/cs",
    "OTCS_USERNAME": "svc",
    "OTCS_PASSWORD": "top-secret-value",
}
HTTP_ENV = {
    **MIN_ENV,
    "MCP_TRANSPORT": "http",
    "MCP_HTTP_AUTH_USER": "mcp",
    "MCP_HTTP_AUTH_PASSWORD": "mcp-secret-value",
}


def _set(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_defaults_are_safe_when_only_required_variables_are_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set(monkeypatch, MIN_ENV)

    settings = load_settings(env_file=None)

    assert settings.mcp_transport is Transport.STDIO
    assert settings.mcp_read_only is True
    assert settings.otcs_verify_ssl is True
    assert settings.mcp_max_response_items == 50
    assert settings.mcp_max_content_bytes == 100_000
    assert settings.mcp_port == 8443
    assert settings.log_level is LogLevel.INFO
    assert settings.tls_enabled is True
    assert settings.tls_auto_generate is True
    assert settings.mcp_allowed_origins == []
    assert settings.mcp_metrics_enabled is False
    assert settings.mcp_allowed_hosts == ["localhost", "127.0.0.1", "[::1]"]
    assert settings.log_format is LogFormat.JSON


def test_load_settings_reports_every_missing_variable_when_required_ones_are_absent() -> None:
    with pytest.raises(ConfigError) as info:
        load_settings(env_file=None)

    message = str(info.value)
    assert "OTCS_BASE_URL" in message
    assert "OTCS_USERNAME" in message
    assert "OTCS_PASSWORD" in message


def test_password_is_hidden_in_repr_str_and_dumps_when_settings_are_printed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set(monkeypatch, HTTP_ENV)

    settings = load_settings(env_file=None)

    for text in (repr(settings), str(settings), str(settings.model_dump())):
        assert "top-secret-value" not in text
        assert "mcp-secret-value" not in text


@pytest.mark.parametrize(
    "base_url",
    [
        "otcs.example.com",
        "ftp://otcs.example.com",
        "https://",
        "https://u:p@otcs.example.com/cs",
        "https://otcs/cs?x=1",
    ],
)
def test_invalid_base_url_is_rejected_when_not_an_absolute_http_url(
    monkeypatch: pytest.MonkeyPatch, base_url: str
) -> None:
    _set(monkeypatch, {**MIN_ENV, "OTCS_BASE_URL": base_url})

    with pytest.raises(ConfigError, match="OTCS_BASE_URL"):
        load_settings(env_file=None)


def test_base_url_loses_trailing_slash_and_api_url_is_derived(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set(monkeypatch, {**MIN_ENV, "OTCS_BASE_URL": " https://otcs.example.com/cs/cs/ "})

    settings = load_settings(env_file=None)

    assert settings.otcs_base_url == "https://otcs.example.com/cs/cs"
    assert settings.otcs_api_url == "https://otcs.example.com/cs/cs/api"


def test_empty_otcs_password_is_rejected_when_variable_is_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set(monkeypatch, {**MIN_ENV, "OTCS_PASSWORD": ""})

    with pytest.raises(ConfigError, match="OTCS_PASSWORD"):
        load_settings(env_file=None)


@pytest.mark.parametrize(
    "missing",
    [
        {"MCP_HTTP_AUTH_USER": ""},
        {"MCP_HTTP_AUTH_PASSWORD": ""},
        {"drop": "MCP_HTTP_AUTH_USER"},
        {"drop": "MCP_HTTP_AUTH_PASSWORD"},
    ],
)
def test_server_refuses_to_start_when_http_transport_lacks_credentials(
    monkeypatch: pytest.MonkeyPatch, missing: dict[str, str]
) -> None:
    env = dict(HTTP_ENV)
    if "drop" in missing:
        env.pop(missing["drop"])
    else:
        env.update(missing)
    _set(monkeypatch, env)

    with pytest.raises(ConfigError, match="MCP_HTTP_AUTH_USER and MCP_HTTP_AUTH_PASSWORD"):
        load_settings(env_file=None)


def test_stdio_transport_does_not_require_http_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set(monkeypatch, {**MIN_ENV, "MCP_TRANSPORT": "stdio"})

    assert load_settings(env_file=None).mcp_http_auth_user is None


def test_security_warnings_are_empty_for_a_secure_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set(monkeypatch, HTTP_ENV)

    assert load_settings(env_file=None).security_warnings() == []


def test_security_warnings_flag_basic_auth_without_tls_when_http_and_tls_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set(monkeypatch, {**HTTP_ENV, "TLS_ENABLED": "false"})

    warnings = load_settings(env_file=None).security_warnings()

    assert any("unencrypted" in warning for warning in warnings)


def test_security_warnings_flag_unverified_tls_and_write_mode_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set(monkeypatch, {**MIN_ENV, "OTCS_VERIFY_SSL": "false", "MCP_READ_ONLY": "false"})

    warnings = load_settings(env_file=None).security_warnings()

    assert any("OTCS_VERIFY_SSL" in warning for warning in warnings)
    assert any("MCP_READ_ONLY" in warning for warning in warnings)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", []),
        ("https://a.example.com", ["https://a.example.com"]),
        (
            " https://A.example.com/ , http://b.example.com:8080 ",
            ["https://a.example.com", "http://b.example.com:8080"],
        ),
    ],
)
def test_allowed_origins_are_split_and_normalised_when_given_as_comma_list(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[str]
) -> None:
    _set(monkeypatch, {**MIN_ENV, "MCP_ALLOWED_ORIGINS": raw})

    assert load_settings(env_file=None).mcp_allowed_origins == expected


@pytest.mark.parametrize(
    "raw",
    ["not-an-origin", "https://a.example.com/path", "ftp://x", "https://a.example.com\n.evil"],
)
def test_allowed_origins_are_rejected_when_malformed(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    _set(monkeypatch, {**MIN_ENV, "MCP_ALLOWED_ORIGINS": raw})

    with pytest.raises(ConfigError, match="MCP_ALLOWED_ORIGINS"):
        load_settings(env_file=None)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MCP_PORT", "0"),
        ("MCP_PORT", "70000"),
        ("OTCS_TIMEOUT_SECONDS", "0"),
        ("OTCS_MAX_CONCURRENCY", "0"),
        ("OTCS_MAX_RETRIES", "9"),
        ("MCP_MAX_RESPONSE_ITEMS", "0"),
        ("MCP_MAX_CONTENT_BYTES", "10"),
        ("MCP_MAX_BODY_BYTES", "0"),
        ("MCP_RATE_LIMIT_PER_MINUTE", "-1"),
        ("TLS_CERT_DAYS", "0"),
        ("LOG_LEVEL", "LOUD"),
        ("MCP_TRANSPORT", "websocket"),
        ("LOG_FORMAT", "xml"),
    ],
)
def test_out_of_range_values_are_rejected_when_variables_are_invalid(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    _set(monkeypatch, {**MIN_ENV, name: value})

    with pytest.raises(ConfigError, match=name):
        load_settings(env_file=None)


def test_config_error_never_echoes_input_values_when_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set(monkeypatch, {**MIN_ENV, "OTCS_TIMEOUT_SECONDS": "leaky-input-value"})

    with pytest.raises(ConfigError) as info:
        load_settings(env_file=None)

    assert "leaky-input-value" not in str(info.value)
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__ is True


def test_settings_are_loaded_from_env_file_when_path_is_given(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OTCS_BASE_URL=https://file.example.com/cs/cs\nOTCS_USERNAME=fileuser\n"
        "OTCS_PASSWORD=file-secret\nMCP_MAX_RESPONSE_ITEMS=7\n",
        encoding="utf-8",
    )

    settings = load_settings(env_file=env_file)

    assert settings.otcs_username == "fileuser"
    assert settings.mcp_max_response_items == 7


def test_environment_overrides_env_file_when_both_define_a_variable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OTCS_BASE_URL=https://file.example.com/cs/cs\nOTCS_USERNAME=fileuser\nOTCS_PASSWORD=x\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OTCS_USERNAME", "envuser")

    assert load_settings(env_file=env_file).otcs_username == "envuser"


def test_settings_are_immutable_when_assigned_after_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set(monkeypatch, MIN_ENV)
    settings = load_settings(env_file=None)

    with pytest.raises(ValueError, match="frozen"):
        settings.mcp_read_only = False  # type: ignore[misc]


def test_settings_constructor_accepts_keyword_arguments_when_used_directly() -> None:
    settings = Settings(
        _env_file=None,
        otcs_base_url="https://x.example.com/cs",
        otcs_username="u",
        otcs_password="p",
    )

    assert settings.otcs_api_url == "https://x.example.com/cs/api"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("mcp.example.com", ["mcp.example.com"]),
        (" LOCALHOST , 10.0.0.5 ,[::1]", ["localhost", "10.0.0.5", "[::1]"]),
    ],
)
def test_allowed_hosts_are_split_and_normalised_when_given_as_comma_list(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[str]
) -> None:
    _set(monkeypatch, {**MIN_ENV, "MCP_ALLOWED_HOSTS": raw})

    assert load_settings(env_file=None).mcp_allowed_hosts == expected


@pytest.mark.parametrize(
    "raw", ["", " , ", "host:8443", "a b", "https://x.example.com", "x/y", "a\nb"]
)
def test_allowed_hosts_are_rejected_when_empty_or_not_plain_host_names(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    _set(monkeypatch, {**MIN_ENV, "MCP_ALLOWED_HOSTS": raw})

    with pytest.raises(ConfigError, match="MCP_ALLOWED_HOSTS"):
        load_settings(env_file=None)


def test_log_format_text_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, {**MIN_ENV, "LOG_FORMAT": "text"})

    assert load_settings(env_file=None).log_format is LogFormat.TEXT
