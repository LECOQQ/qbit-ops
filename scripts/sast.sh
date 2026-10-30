#!/usr/bin/env bash
# Scan the repository with a pinned engine and pinned rule sources. Shared by
# `make sast` and the CI workflow. SAST_ENGINE picks the engine (semgrep, the
# default, or opengrep): both read the same rules and write the same SARIF.
# Rule sources live in .sast/sources; house rules in .sast/rules.
set -euo pipefail

SAST_ENGINE="${SAST_ENGINE:-semgrep}"
OPENGREP_VERSION="v1.33.1"
SEMGREP_VERSION="1.183.0"
OPENGREP_ASSET="opengrep_manylinux_x86"
SAST_HOME="${SAST_HOME:-${XDG_CACHE_HOME:-$HOME/.cache}/sast}"
SOURCES_FILE="${SAST_SOURCES:-.sast/sources}"
HOUSE_RULES="${SAST_RULES:-.sast/rules}"
BASE_URL="https://github.com/opengrep/opengrep/releases/download/${OPENGREP_VERSION}"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "${TMP_ROOT}"' EXIT

die() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

install_opengrep() {
  local dir="${SAST_HOME}/${OPENGREP_VERSION}"
  [ -x "${dir}/opengrep" ] && return 0
  command -v cosign >/dev/null 2>&1 \
    || die "cosign is required to verify Opengrep (https://docs.sigstore.dev/cosign/system_config/installation/)."
  [ "$(uname -sm)" = "Linux x86_64" ] \
    || die "only Linux x86_64 is wired; install Opengrep yourself and set OPENGREP_BIN."
  local tmp="${TMP_ROOT}/opengrep"
  mkdir -p "${tmp}"
  for f in "${OPENGREP_ASSET}" "${OPENGREP_ASSET}.cert" "${OPENGREP_ASSET}.sig"; do
    curl -fsSL --retry 3 --max-time 120 -o "${tmp}/${f}" "${BASE_URL}/${f}"
  done
  cosign verify-blob \
    --cert "${tmp}/${OPENGREP_ASSET}.cert" \
    --signature "${tmp}/${OPENGREP_ASSET}.sig" \
    --certificate-identity-regexp "https://github.com/opengrep/opengrep.+" \
    --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
    "${tmp}/${OPENGREP_ASSET}" >/dev/null 2>&1 \
    || die "signature check failed for Opengrep ${OPENGREP_VERSION}; refusing to run it."
  mkdir -p "${dir}"
  install -m 0755 "${tmp}/${OPENGREP_ASSET}" "${dir}/opengrep"
}

# Download one source at its commit into the cache, keeping only `paths`.
fetch_source() {
  local repo="$1" sha="$2" dir="$3"
  shift 3
  [ -d "${dir}" ] && return 0
  local p tmp="${TMP_ROOT}/${repo//\//-}"
  mkdir -p "${tmp}"
  curl -fsSL --retry 3 --max-time 120 -o "${tmp}/rules.tar.gz" \
    "https://github.com/${repo}/archive/${sha}.tar.gz"
  mkdir -p "${tmp}/src" "${tmp}/keep"
  tar -xzf "${tmp}/rules.tar.gz" -C "${tmp}/src" --strip-components=1
  for p in "$@"; do
    mkdir -p "${tmp}/keep/${p}"
    cp -R "${tmp}/src/${p}/." "${tmp}/keep/${p}/"
  done
  mkdir -p "$(dirname "${dir}")"
  mv "${tmp}/keep" "${dir}"
}

# Read .sast/sources: fill SOURCES ("repo sha path...") and EXCLUDES
# ("repo path"), skipping comments and blank lines.
read_sources() {
  local -a f
  SOURCES=()
  EXCLUDES=()
  while read -r -a f; do
    { [ "${#f[@]}" -eq 0 ] || [[ "${f[0]}" == \#* ]]; } && continue
    if [ "${f[0]}" = "exclude" ]; then
      EXCLUDES+=("${f[1]} ${f[2]}")
    else
      SOURCES+=("${f[*]}")
    fi
  done <"${SOURCES_FILE}"
}

# Install the pinned Semgrep CE with uv unless that exact version is present.
install_semgrep() {
  local bin="${SEMGREP_BIN:-semgrep}"
  if command -v "${bin}" >/dev/null 2>&1 \
    && [ "$("${bin}" --version 2>/dev/null | tail -1)" = "${SEMGREP_VERSION}" ]; then
    ENGINE_BIN="${bin}"
    return 0
  fi
  [ -z "${SEMGREP_BIN:-}" ] \
    || die "${SEMGREP_BIN} is not semgrep ${SEMGREP_VERSION}."
  command -v uv >/dev/null 2>&1 \
    || die "uv is required to install semgrep==${SEMGREP_VERSION} (https://docs.astral.sh/uv/)."
  uv tool install --quiet "semgrep==${SEMGREP_VERSION}"
  ENGINE_BIN="$(command -v semgrep || true)"
  [ -n "${ENGINE_BIN}" ] \
    || die "semgrep is installed but not on PATH; run 'uv tool update-shell'."
}

select_engine() {
  case "${SAST_ENGINE}" in
    opengrep)
      if [ -z "${OPENGREP_BIN:-}" ]; then
        install_opengrep
        OPENGREP_BIN="${SAST_HOME}/${OPENGREP_VERSION}/opengrep"
      fi
      ENGINE_BIN="${OPENGREP_BIN}"
      ;;
    semgrep)
      install_semgrep
      # Local rules send no metrics; say so rather than rely on that.
      ENGINE_FLAGS=(--metrics=off)
      export SEMGREP_ENABLE_VERSION_CHECK=0
      ;;
    *) die "SAST_ENGINE must be semgrep or opengrep, got '${SAST_ENGINE}'." ;;
  esac
}

ENGINE_FLAGS=()
select_engine

[ -f "${SOURCES_FILE}" ] || die "no rule sources file at ${SOURCES_FILE}."
read_sources
args=(scan --quiet "${ENGINE_FLAGS[@]}")
[ -d "${HOUSE_RULES}" ] && args+=(--config "${HOUSE_RULES}")
for entry in "${SOURCES[@]}"; do
  read -r -a f <<<"${entry}"
  repo="${f[0]}"
  sha="${f[1]}"
  dir="${SAST_HOME}/rules-${repo//\//-}-${sha}"
  fetch_source "${repo}" "${sha}" "${dir}" "${f[@]:2}"
  for ex in "${EXCLUDES[@]}"; do
    [ "${ex%% *}" = "${repo}" ] && rm -f "${dir}/${ex#* }"
  done
  for p in "${f[@]:2}"; do args+=(--config "${dir}/${p}"); done
done
[ -n "${SAST_SARIF:-}" ] && args+=(--sarif-output="${SAST_SARIF}")
[ "${SAST_STRICT:-0}" = "1" ] && args+=(--error)
# exec skips the EXIT trap.
rm -rf "${TMP_ROOT}"
exec "${ENGINE_BIN}" "${args[@]}" "$@" .
