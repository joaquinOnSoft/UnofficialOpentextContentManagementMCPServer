# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""Run the real docker/entrypoint.sh with POSIX sh in a temporary directory.

No Docker is needed: the script only uses sh, openssl and coreutils.
"""

import os
import shutil
import stat
import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "docker" / "entrypoint.sh"

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl is required")

Result = subprocess.CompletedProcess[str]
Runner = Callable[..., Result]


@pytest.fixture
def certs(tmp_path: Path) -> Path:
    directory = tmp_path / "certs"
    directory.mkdir()
    return directory


@pytest.fixture
def run(certs: Path) -> Runner:
    def runner(*command: str, **env: str) -> Result:
        environment = {
            "PATH": os.environ["PATH"],
            "MCP_TRANSPORT": "http",
            "TLS_CERT_FILE": str(certs / "server.crt"),
            "TLS_KEY_FILE": str(certs / "server.key"),
            **env,
        }
        return subprocess.run(  # noqa: S603
            ["sh", str(SCRIPT), *(command or ("echo", "started"))],  # noqa: S607
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    return runner


def _openssl(*args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["openssl", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_entrypoint_script_is_executable_and_uses_posix_sh() -> None:
    assert SCRIPT.read_text().startswith("#!/bin/sh\n")
    assert SCRIPT.stat().st_mode & stat.S_IXUSR


def test_certificate_and_key_are_generated_on_first_http_start(run: Runner, certs: Path) -> None:
    result = run()

    assert result.returncode == 0, result.stderr
    assert "started" in result.stdout  # the command was exec'd afterwards
    assert (certs / "server.crt").is_file()
    assert (certs / "server.key").is_file()
    assert "generating a self-signed certificate" in result.stderr


def test_private_key_gets_owner_only_permissions_and_is_never_printed(
    run: Runner, certs: Path
) -> None:
    result = run()

    assert stat.S_IMODE((certs / "server.key").stat().st_mode) == 0o600
    assert stat.S_IMODE((certs / "server.crt").stat().st_mode) == 0o644
    assert "PRIVATE KEY" not in result.stdout + result.stderr
    key_body = (certs / "server.key").read_text().splitlines()[1]
    assert key_body not in result.stdout + result.stderr


def test_certificate_is_ec_p256_sha256_with_requested_subject_and_san(
    run: Runner, certs: Path
) -> None:
    run(TLS_CERT_CN="mcp.example.com", TLS_CERT_SAN="DNS:mcp.example.com,IP:10.0.0.5,DNS:*.lan")

    text = _openssl("x509", "-in", str(certs / "server.crt"), "-noout", "-text")
    assert "CN = mcp.example.com" in text
    assert "DNS:mcp.example.com" in text
    assert "IP Address:10.0.0.5" in text
    assert "DNS:*.lan" in text
    assert "ecdsa-with-SHA256" in text
    assert "prime256v1" in text


def test_certificate_validity_matches_tls_cert_days(run: Runner, certs: Path) -> None:
    run(TLS_CERT_DAYS="30")

    dates = _openssl("x509", "-in", str(certs / "server.crt"), "-noout", "-dates").splitlines()

    def parse(line: str) -> datetime:
        return datetime.strptime(line.split("=")[1], "%b %d %H:%M:%S %Y %Z")

    assert (parse(dates[1]) - parse(dates[0])).days == 30


def test_fingerprint_and_expiry_are_logged_and_match_the_certificate(
    run: Runner, certs: Path
) -> None:
    result = run()

    fingerprint = (
        _openssl("x509", "-in", str(certs / "server.crt"), "-noout", "-fingerprint", "-sha256")
        .split("=")[1]
        .strip()
    )
    assert f"SHA-256 fingerprint: {fingerprint}" in result.stderr
    assert "certificate expires:" in result.stderr


def test_existing_certificate_is_reused_and_never_overwritten(run: Runner, certs: Path) -> None:
    run()
    before = {name: (certs / name).read_bytes() for name in ("server.crt", "server.key")}

    second = run()

    assert second.returncode == 0
    assert "reusing existing certificate" in second.stderr
    assert "generating" not in second.stderr
    assert {name: (certs / name).read_bytes() for name in before} == before


def test_existing_certificate_is_reused_even_when_auto_generation_is_off(
    run: Runner, certs: Path
) -> None:
    run()

    result = run(TLS_AUTO_GENERATE="false")

    assert result.returncode == 0
    assert "reusing existing certificate" in result.stderr


@pytest.mark.parametrize("stray", ["server.crt", "server.key"])
def test_entrypoint_refuses_to_overwrite_when_only_one_file_exists(
    run: Runner, certs: Path, stray: str
) -> None:
    (certs / stray).write_text("precious")

    result = run()

    assert result.returncode == 1
    assert "refusing to overwrite" in result.stderr
    assert (certs / stray).read_text() == "precious"
    assert "started" not in result.stdout


@pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off"])
def test_entrypoint_fails_with_clear_message_when_auto_generate_is_off_and_no_files(
    run: Runner, certs: Path, value: str
) -> None:
    result = run(TLS_AUTO_GENERATE=value)

    assert result.returncode == 1
    assert "TLS_AUTO_GENERATE=false" in result.stderr
    assert list(certs.iterdir()) == []


def test_nothing_is_generated_in_stdio_mode(run: Runner, certs: Path) -> None:
    result = run(MCP_TRANSPORT="stdio")

    assert result.returncode == 0
    assert "started" in result.stdout
    assert list(certs.iterdir()) == []


def test_nothing_is_generated_when_transport_is_unset(run: Runner, certs: Path) -> None:
    result = run(MCP_TRANSPORT="")

    assert result.returncode == 0
    assert list(certs.iterdir()) == []


@pytest.mark.parametrize("value", ["false", "0", "off"])
def test_nothing_is_generated_when_tls_is_disabled(run: Runner, certs: Path, value: str) -> None:
    result = run(TLS_ENABLED=value)

    assert result.returncode == 0
    assert list(certs.iterdir()) == []


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("TLS_CERT_CN", "x/O=evil"),
        ("TLS_CERT_CN", "name with space"),
        ("TLS_CERT_CN", "a\nb"),
        ("TLS_CERT_CN", "$(touch pwned)"),
        ("TLS_CERT_SAN", "DNS:a;touch pwned"),
        ("TLS_CERT_SAN", "DNS:a\nIP:1.2.3.4"),
        ("TLS_CERT_SAN", "DNS:a,evil:b"),
        ("TLS_CERT_SAN", "localhost"),
        ("TLS_CERT_SAN", "DNS:a,"),
        ("TLS_CERT_SAN", "$(id)"),
        ("TLS_CERT_DAYS", "abc"),
        ("TLS_CERT_DAYS", "0"),
        ("TLS_CERT_DAYS", "99999"),
        ("TLS_CERT_DAYS", "10; rm -rf /"),
        ("TLS_CERT_DAYS", "-5"),
    ],
)
def test_malicious_or_invalid_certificate_parameters_are_rejected_before_openssl_runs(
    run: Runner, certs: Path, tmp_path: Path, variable: str, value: str
) -> None:
    result = run(**{variable: value})

    assert result.returncode == 1
    assert "started" not in result.stdout
    assert list(certs.iterdir()) == []
    assert not (tmp_path / "pwned").exists()
    assert not Path("pwned").exists()


def test_missing_certificate_directory_gives_actionable_error(run: Runner, tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"

    result = run(TLS_CERT_FILE=str(missing / "s.crt"), TLS_KEY_FILE=str(missing / "s.key"))

    assert result.returncode == 1
    assert "not writable" in result.stderr or "missing" in result.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write to read-only directories")
def test_read_only_certificate_directory_gives_actionable_error(run: Runner, certs: Path) -> None:
    certs.chmod(0o500)
    try:
        result = run()
    finally:
        certs.chmod(0o700)

    assert result.returncode == 1
    assert "not writable" in result.stderr


def test_no_temporary_files_are_left_behind_after_generation(run: Runner, certs: Path) -> None:
    run()

    assert sorted(path.name for path in certs.iterdir()) == ["server.crt", "server.key"]


def test_failed_generation_leaves_no_partial_files(
    run: Runner, certs: Path, tmp_path: Path
) -> None:
    broken_bin = tmp_path / "bin"
    broken_bin.mkdir()
    (broken_bin / "openssl").write_text("#!/bin/sh\nexit 1\n")
    (broken_bin / "openssl").chmod(0o755)

    result = run(PATH=f"{broken_bin}:{os.environ['PATH']}")

    assert result.returncode == 1
    assert "could not generate" in result.stderr
    assert list(certs.iterdir()) == []


def test_missing_openssl_is_reported_clearly(run: Runner, certs: Path, tmp_path: Path) -> None:
    empty_bin = tmp_path / "emptybin"
    empty_bin.mkdir()
    for tool in (
        "sh",
        "tr",
        "cut",
        "dirname",
        "mktemp",
        "id",
        "grep",
        "chmod",
        "mv",
        "rmdir",
        "rm",
    ):
        found = shutil.which(tool)
        if found:
            (empty_bin / tool).symlink_to(found)

    result = run(PATH=str(empty_bin))

    assert result.returncode == 1
    assert "openssl is not installed" in result.stderr
    assert list(certs.iterdir()) == []


def test_exit_code_and_arguments_of_the_command_are_preserved(run: Runner) -> None:
    result = run("sh", "-c", 'echo "$1"; exit 7', "_", "two words")

    assert result.stdout.strip() == "two words"
    assert result.returncode == 7


def test_entrypoint_fails_when_no_command_is_given(certs: Path) -> None:
    result = subprocess.run(  # noqa: S603
        ["sh", str(SCRIPT)],  # noqa: S607
        env={"PATH": os.environ["PATH"], "MCP_TRANSPORT": "http"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "no command given" in result.stderr
