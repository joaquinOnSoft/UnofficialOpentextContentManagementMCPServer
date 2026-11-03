#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón
#
# Container entrypoint. In HTTP mode with TLS enabled it makes sure a server
# certificate exists (generating a self-signed one on first start), then hands
# over to the given command. The private key is generated at container start,
# never at image build time, and it is never printed.
set -eu

log() { printf 'entrypoint: %s\n' "$*" >&2; }
fail() { log "ERROR: $*"; exit 1; }

is_false() {
    case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
        false | 0 | no | off) return 0 ;;
        *) return 1 ;;
    esac
}

tmp_dir=""
cleanup() { [ -n "$tmp_dir" ] && rm -rf "$tmp_dir"; return 0; }
trap cleanup EXIT

report_certificate() {
    fingerprint=$(openssl x509 -in "$1" -noout -fingerprint -sha256 | cut -d= -f2)
    expiry=$(openssl x509 -in "$1" -noout -enddate | cut -d= -f2)
    log "certificate SHA-256 fingerprint: $fingerprint"
    log "certificate expires: $expiry"
}

validate_inputs() {
    case "$cn" in
        "" | *[!A-Za-z0-9._*-]*) fail "TLS_CERT_CN may only contain letters, digits, '.', '_', '-' and '*'." ;;
    esac
    case "$san" in
        "" | *[!A-Za-z0-9._*:,-]*) fail "TLS_CERT_SAN contains invalid characters; use e.g. DNS:localhost,IP:127.0.0.1." ;;
    esac
    printf '%s' "$san" | grep -Eq '^(DNS:[A-Za-z0-9._*-]+|IP:[0-9A-Fa-f:.]+)(,(DNS:[A-Za-z0-9._*-]+|IP:[0-9A-Fa-f:.]+))*$' ||
        fail "TLS_CERT_SAN must be a comma-separated list of DNS:<name> or IP:<address> entries."
    case "$days" in
        "" | *[!0-9]*) fail "TLS_CERT_DAYS must be a whole number of days." ;;
    esac
    { [ "$days" -ge 1 ] && [ "$days" -le 3650 ]; } || fail "TLS_CERT_DAYS must be between 1 and 3650."
}

ensure_certificate() {
    cert=${TLS_CERT_FILE:-/certs/server.crt}
    key=${TLS_KEY_FILE:-/certs/server.key}
    auto=${TLS_AUTO_GENERATE:-true}
    cn=${TLS_CERT_CN:-localhost}
    san=${TLS_CERT_SAN:-DNS:localhost,IP:127.0.0.1}
    days=${TLS_CERT_DAYS:-365}

    command -v openssl >/dev/null 2>&1 || fail "openssl is not installed in this image."

    if [ -f "$cert" ] && [ -f "$key" ]; then
        log "reusing existing certificate $cert (it is not overwritten)"
        report_certificate "$cert"
        return 0
    fi
    if [ -e "$cert" ] || [ -e "$key" ]; then
        fail "only one of TLS_CERT_FILE and TLS_KEY_FILE exists; refusing to overwrite it. Remove the stray file or provide both."
    fi
    if is_false "$auto"; then
        fail "no TLS certificate at $cert and TLS_AUTO_GENERATE=false. Mount a certificate and key or enable auto-generation."
    fi

    validate_inputs
    cert_dir=$(dirname "$cert")
    key_dir=$(dirname "$key")
    for dir in "$cert_dir" "$key_dir"; do
        { [ -d "$dir" ] && [ -w "$dir" ]; } || fail "directory $dir is missing or not writable by uid $(id -u) (check the volume permissions)."
    done

    umask 077
    tmp_dir=$(mktemp -d "$cert_dir/.generate.XXXXXX")
    log "generating a self-signed certificate (CN=$cn, valid $days days)"
    openssl ecparam -name prime256v1 -genkey -noout -out "$tmp_dir/key.pem" 2>/dev/null ||
        fail "could not generate the private key."
    openssl req -new -x509 -key "$tmp_dir/key.pem" -sha256 -days "$days" \
        -subj "/CN=$cn" -addext "subjectAltName=$san" -out "$tmp_dir/cert.pem" 2>/dev/null ||
        fail "could not generate the certificate."
    chmod 600 "$tmp_dir/key.pem"
    chmod 644 "$tmp_dir/cert.pem"
    mv "$tmp_dir/key.pem" "$key"
    mv "$tmp_dir/cert.pem" "$cert"
    rmdir "$tmp_dir"
    tmp_dir=""
    report_certificate "$cert"
}

[ "$#" -gt 0 ] || fail "no command given."

if [ "${MCP_TRANSPORT:-stdio}" = "http" ] && ! is_false "${TLS_ENABLED:-true}"; then
    ensure_certificate
fi

exec "$@"
