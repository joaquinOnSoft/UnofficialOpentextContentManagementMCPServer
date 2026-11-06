# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""The README, .env.example, Dockerfile and compose file must agree with the code.

These tests keep the documentation honest: they fail when a variable, service,
volume, port, tool or licence reference drifts away from what the code does.
"""

import importlib
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

from otcs_mcp.config import Settings, load_settings
from otcs_mcp.server import ALL_TOOL_NAMES, READ_TOOL_NAMES, WRITE_TOOL_NAMES

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text(encoding="utf-8")
COMPOSE: dict[str, Any] = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
SETTINGS_VARIABLES = {name.upper() for name in Settings.model_fields}


def _env_example_variables() -> set[str]:
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    return {line.split("=", 1)[0] for line in lines if line and not line.startswith("#")}


def _readme_table_variables() -> set[str]:
    section = README.split("## 5. Configuration reference", 1)[1].split("## 6.", 1)[0]
    return set(re.findall(r"^\| `([A-Z_]+)` \|", section, flags=re.MULTILINE))


# ----------------------------------------------------------------- configuration


def test_env_example_declares_exactly_the_settings_the_code_reads() -> None:
    assert _env_example_variables() == SETTINGS_VARIABLES


def test_readme_configuration_table_lists_exactly_the_settings_the_code_reads() -> None:
    assert _readme_table_variables() == SETTINGS_VARIABLES


def test_env_example_has_no_inline_comments_that_some_parsers_keep_in_the_value() -> None:
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            assert " #" not in line, line


def test_env_example_loads_into_valid_settings(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")

    settings = load_settings(env_file=env_file)

    assert settings.mcp_read_only is True
    assert settings.mcp_allowed_hosts == ["localhost", "127.0.0.1", "[::1]"]
    assert settings.tls_auto_generate is True


def test_env_example_works_for_http_transport_after_editing_only_documented_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("MCP_TRANSPORT", "http")

    assert load_settings(env_file=env_file).mcp_http_auth_user == "mcp_user"


@pytest.mark.parametrize("name", [".gitignore", ".dockerignore"])
def test_real_env_files_are_never_versioned_or_sent_to_docker(name: str) -> None:
    lines = (ROOT / name).read_text(encoding="utf-8").splitlines()

    assert ".env" in lines


# ----------------------------------------------------------------- docker and compose


def test_compose_defines_the_services_volumes_and_networks_the_readme_uses() -> None:
    assert set(COMPOSE["services"]) == {"otcs-mcp", "otcs-mcp-stdio", "otcs-mcp-test"}
    assert "mcp_certs" in COMPOSE["volumes"]
    assert COMPOSE["volumes"]["mcp_certs"]["name"] == "mcp_certs"
    for service in re.findall(r"docker compose [^\n`]*? (otcs-mcp[a-z-]*)", README):
        assert service in COMPOSE["services"], service


def test_every_compose_build_target_is_a_stage_of_the_dockerfile() -> None:
    stages = set(re.findall(r"^FROM .* AS (\w+)$", DOCKERFILE, flags=re.MULTILINE))
    for name, service in COMPOSE["services"].items():
        assert service["build"]["target"] in stages, name


def test_http_service_is_hardened_and_reads_the_env_file_and_certificate_volume() -> None:
    service = COMPOSE["services"]["otcs-mcp"]

    assert service["env_file"] == ".env"
    assert service["environment"]["MCP_TRANSPORT"] == "http"
    assert "mcp_certs:/certs" in service["volumes"]
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]
    assert service["restart"] == "unless-stopped"
    assert "limits" in service["deploy"]["resources"]
    assert service["ports"] == ["${MCP_PORT:-8443}:${MCP_PORT:-8443}"]


def test_stdio_service_publishes_no_port_and_forces_stdio() -> None:
    service = COMPOSE["services"]["otcs-mcp-stdio"]

    assert "ports" not in service
    assert service["environment"]["MCP_TRANSPORT"] == "stdio"
    assert service["stdin_open"] is True
    assert service["profiles"] == ["stdio"]


def test_test_service_has_no_network_and_runs_pytest() -> None:
    service = COMPOSE["services"]["otcs-mcp-test"]

    assert service["network_mode"] == "none"
    assert service["command"] == ["python", "-m", "pytest"]


def test_published_port_and_expose_match_the_default_port_in_settings() -> None:
    default_port = Settings.model_fields["mcp_port"].default

    assert f"EXPOSE {default_port}" in DOCKERFILE
    assert f"MCP_PORT:-{default_port}" in COMPOSE["services"]["otcs-mcp"]["ports"][0]
    assert f"https://localhost:{default_port}/mcp" in README


def test_dockerfile_runs_as_non_root_with_entrypoint_healthcheck_and_no_baked_key() -> None:
    assert "USER app" in DOCKERFILE
    assert 'ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]' in DOCKERFILE
    assert "otcs_mcp.healthcheck" in DOCKERFILE
    assert "HEALTHCHECK" in DOCKERFILE
    assert "PYTHONUNBUFFERED=1" in DOCKERFILE
    assert "openssl" in DOCKERFILE
    assert "openssl req" not in DOCKERFILE  # the certificate is created at start, not at build
    assert (ROOT / "docker" / "entrypoint.sh").is_file()
    importlib.import_module("otcs_mcp.healthcheck")


def test_dockerfile_disables_fastmcp_update_checks_and_banner() -> None:
    assert "FASTMCP_CHECK_FOR_UPDATES=off" in DOCKERFILE
    assert "FASTMCP_SHOW_SERVER_BANNER=false" in DOCKERFILE


def test_readme_documents_every_tls_variable_and_the_certificate_volume() -> None:
    for token in ("TLS_AUTO_GENERATE", "TLS_CERT_SAN", "mcp_certs", "/certs", "fingerprint"):
        assert token in README


# ----------------------------------------------------------------- tools


def _catalog_tables() -> dict[str, set[str]]:
    section = README.split("## 8. Catalog", 1)[1].split("**Arguments and example calls**", 1)[0]
    tables: dict[str, set[str]] = {}
    for heading, body in re.findall(r"### ([^\n]+)\n(.*?)(?=\n### |\Z)", section, flags=re.S):
        tables[heading] = set(re.findall(r"^\| `([a-z_]+)` \|", body, flags=re.MULTILINE))
    return tables


def test_readme_catalog_lists_exactly_the_registered_tools_split_by_kind() -> None:
    tables = _catalog_tables()
    read_documented = (
        tables["Repository and Business Workspaces (read-only)"]
        | tables["Records Management (read-only, Records Management REST API 26.4)"]
    )

    assert read_documented == set(READ_TOOL_NAMES)
    assert tables["Write tools (only when `MCP_READ_ONLY=false`)"] == set(WRITE_TOOL_NAMES)


def test_readme_shows_an_example_call_for_every_tool() -> None:
    for tool in ALL_TOOL_NAMES:
        assert f'"name": "{tool}"' in README, tool


def test_readme_states_the_correct_tool_counts() -> None:
    assert f"**{len(READ_TOOL_NAMES)} read tools**" in README
    assert f"**{len(WRITE_TOOL_NAMES)} write tools**" in README
    assert f"Exposes {len(READ_TOOL_NAMES)} task-oriented read tools" in README
    assert f"the tool list grows to {len(ALL_TOOL_NAMES)}" in README


def test_readme_documents_write_mode_safeguards() -> None:
    section = README.split("### Write mode", 1)[1].split("**Resource.**", 1)[0]

    for token in (
        "MCP_READ_ONLY=false",
        "confirm",
        "destructiveHint",
        "metadata_token",
        "never retried",
        "MCP_MAX_UPLOAD_BYTES",
        "MCP_MAX_BODY_BYTES",
        "audit",
        "not against a real",
    ):
        assert token in section, token


def test_readme_no_longer_claims_the_server_is_read_only_without_qualification() -> None:
    assert "There are no write tools in this version" not in README
    assert "reserved for them" not in README
    assert "(no Records Management swagger was available)" not in README
    assert "read-only by default" in README


def test_readme_mentions_the_resource_and_prompts() -> None:
    for token in ("otcs-mcp://usage-guide", "explore_folder", "find_documents"):
        assert token in README


# ----------------------------------------------------------------- licence and headers


def test_license_is_the_unmodified_apache_text_with_only_the_appendix_filled_in() -> None:
    text = (ROOT / "LICENSE.md").read_text(encoding="utf-8")

    assert "Apache License\n                           Version 2.0, January 2004" in text
    assert "Copyright 2026 Joaquín Garzón" in text
    assert "[yyyy]" not in text
    assert "[name of copyright owner]" not in text
    assert "END OF TERMS AND CONDITIONS" in text
    assert text.count("Apache License") >= 3


def test_pyproject_declares_the_apache_licence_and_ships_the_licence_files() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["license"] == "Apache-2.0"
    assert "LICENSE.md" in project["license-files"]
    assert (ROOT / "NOTICE").is_file()
    assert "[LICENSE.md](LICENSE.md)" in README


def test_every_source_test_and_script_file_carries_spdx_and_copyright_headers() -> None:
    files = [
        path
        for folder in ("src", "tests", "scripts")
        for path in (ROOT / folder).rglob("*.py")
        if path.stat().st_size > 0
    ]
    files += [ROOT / "docker" / "entrypoint.sh", ROOT / "Dockerfile", ROOT / "docker-compose.yml"]
    assert files

    for path in files:
        head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:4])
        assert "SPDX-License-Identifier: Apache-2.0" in head, path
        assert "Copyright 2026 Joaquín Garzón" in head, path


def test_readme_has_all_mandatory_sections_in_order() -> None:
    titles = re.findall(r"^## (\d+)\. ", README, flags=re.MULTILINE)

    assert titles == [str(n) for n in range(1, 13)]
    steps = re.findall(r"^### Step (\d+)", README, flags=re.MULTILINE)
    assert steps == [str(n) for n in range(1, 11)]


def test_dockerfile_builder_copies_every_file_the_package_metadata_needs() -> None:
    """Hatchling silently skips missing licence files, so the image would lose them."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    needed = {"pyproject.toml", project["readme"], *project["license-files"]}
    copy_line = next(
        line for line in DOCKERFILE.splitlines() if line.startswith("COPY pyproject.toml")
    )

    for name in needed:
        assert name in copy_line.split(), name
        assert (ROOT / name).is_file(), name


def test_readme_smoke_output_and_tools_list_example_match_the_registered_read_tools() -> None:
    expected = f"OK: protocol 2026-07-28, {len(READ_TOOL_NAMES)} tools: " + ", ".join(
        READ_TOOL_NAMES
    )

    assert expected in README
    assert f"`tools` array has\n{len(READ_TOOL_NAMES)} entries" in README
    assert not re.search(r"\b11 (tools|entries|read tools)\b", README)
