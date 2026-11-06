# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""Application settings loaded from environment variables and an optional .env file."""

import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from otcs_mcp.errors import ConfigError

WRITE_BODY_OVERHEAD_BYTES = 4096  # JSON-RPC envelope around an uploaded document
_ORIGIN_RE = re.compile(r"https?://[^\s/]+")
_HOST_RE = re.compile(r"[a-z0-9._-]+|\[[0-9a-f:.]+\]")


class Transport(StrEnum):
    """MCP transports supported by the server."""

    STDIO = "stdio"
    HTTP = "http"


class LogFormat(StrEnum):
    """Log output formats."""

    JSON = "json"
    TEXT = "text"


class LogLevel(StrEnum):
    """Allowed log levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class Settings(BaseSettings):
    """Validated, immutable server configuration."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", frozen=True
    )

    # OpenText Content Management connection
    otcs_base_url: str
    otcs_username: str = Field(min_length=1)
    otcs_password: SecretStr
    otcs_verify_ssl: bool = True
    otcs_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    otcs_max_concurrency: int = Field(default=8, ge=1, le=64)
    otcs_max_retries: int = Field(default=2, ge=0, le=5)

    # MCP server
    mcp_transport: Transport = Transport.STDIO
    # Container default: compose publishes the port explicitly, only inside its own network.
    mcp_host: str = Field(default="0.0.0.0", min_length=1)  # noqa: S104  # nosec B104
    mcp_port: int = Field(default=8443, ge=1, le=65535)
    mcp_http_auth_user: str | None = None
    mcp_http_auth_password: SecretStr | None = None
    mcp_read_only: bool = True
    mcp_max_response_items: int = Field(default=50, ge=1, le=500)
    mcp_max_content_bytes: int = Field(default=100_000, ge=1_000, le=2_000_000)
    mcp_max_upload_bytes: int = Field(default=500_000, ge=1_000, le=50_000_000)
    mcp_allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    mcp_allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "[::1]"]
    )
    mcp_max_body_bytes: int = Field(default=1_048_576, ge=1)
    mcp_rate_limit_per_minute: int = Field(default=120, ge=0)
    mcp_metrics_enabled: bool = False
    log_level: LogLevel = LogLevel.INFO
    log_format: LogFormat = LogFormat.JSON

    # TLS (HTTP transport)
    tls_enabled: bool = True
    tls_cert_file: Path = Path("/certs/server.crt")
    tls_key_file: Path = Path("/certs/server.key")
    tls_auto_generate: bool = True
    tls_cert_cn: str = Field(default="localhost", min_length=1)
    tls_cert_san: str = "DNS:localhost,IP:127.0.0.1"
    tls_cert_days: int = Field(default=365, ge=1, le=3650)

    @field_validator("otcs_base_url")
    @classmethod
    def _check_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(
                "must be an absolute http(s) URL such as https://otcs.example.com/cs/cs"
            )
        if parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError("must not contain a query string, fragment or credentials")
        return value

    @field_validator("otcs_password")
    @classmethod
    def _check_password(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            raise ValueError("must not be empty")
        return value

    @field_validator("mcp_allowed_origins", "mcp_allowed_hosts", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item for item in (part.strip() for part in value.split(",")) if item]
        return value

    @field_validator("mcp_allowed_origins")
    @classmethod
    def _check_origins(cls, value: list[str]) -> list[str]:
        normalised = [origin.strip().rstrip("/").lower() for origin in value]
        if any(not _ORIGIN_RE.fullmatch(origin) for origin in normalised):
            raise ValueError("each origin must look like https://host[:port]")
        return normalised

    @field_validator("mcp_allowed_hosts")
    @classmethod
    def _check_hosts(cls, value: list[str]) -> list[str]:
        normalised = [host.strip().lower() for host in value]
        if not normalised or any(not _HOST_RE.fullmatch(host) for host in normalised):
            raise ValueError("must be a comma-separated list of host names or IPs without ports")
        return normalised

    @model_validator(mode="after")
    def _check_http_credentials(self) -> Self:
        if self.mcp_transport is Transport.HTTP:
            password = self.mcp_http_auth_password
            if not self.mcp_http_auth_user or password is None or not password.get_secret_value():
                raise ValueError(
                    "MCP_HTTP_AUTH_USER and MCP_HTTP_AUTH_PASSWORD are required "
                    "when MCP_TRANSPORT=http"
                )
        return self

    @property
    def otcs_api_url(self) -> str:
        """Base URL of the Content Server REST API."""
        return f"{self.otcs_base_url}/api"

    def security_warnings(self) -> list[str]:
        """Return human-readable warnings about insecure but allowed settings."""
        warnings: list[str] = []
        if self.mcp_transport is Transport.HTTP and not self.tls_enabled:
            warnings.append(
                "TLS_ENABLED=false: HTTP Basic credentials will travel unencrypted. "
                "Use TLS or a TLS-terminating reverse proxy."
            )
        if not self.otcs_verify_ssl:
            warnings.append(
                "OTCS_VERIFY_SSL=false: the OpenText server certificate is NOT verified."
            )
        if not self.mcp_read_only:
            warnings.append("MCP_READ_ONLY=false: write tools are enabled.")
            base64_size = (self.mcp_max_upload_bytes + 2) // 3 * 4 + WRITE_BODY_OVERHEAD_BYTES
            if self.mcp_transport is Transport.HTTP and base64_size > self.mcp_max_body_bytes:
                warnings.append(
                    "MCP_MAX_UPLOAD_BYTES does not fit in MCP_MAX_BODY_BYTES once "
                    "base64-encoded: uploads near the limit will be rejected with HTTP 413. "
                    "Raise MCP_MAX_BODY_BYTES."
                )
        return warnings


def _format_validation_error(exc: ValidationError) -> str:
    lines = ["Invalid configuration:"]
    for err in exc.errors(include_input=False, include_url=False, include_context=False):
        name = ".".join(str(part) for part in err["loc"]).upper() or "CONFIGURATION"
        message = err["msg"].removeprefix("Value error, ")
        lines.append(f"  - {name}: {message}")
    return "\n".join(lines)


def load_settings(env_file: str | Path | None = ".env") -> Settings:
    """Load and validate settings, failing fast with a clear message.

    The validation error is deliberately not chained (``from None``): pydantic
    errors carry the offending input values, which may be passwords.
    """
    try:
        return Settings(_env_file=env_file)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc)) from None
