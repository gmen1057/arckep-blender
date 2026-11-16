#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ADDON_DIR="${REPO_DIR}/addon"
MANIFEST_PATH="${ADDON_DIR}/blender_manifest.toml"
DIST_DIR="${REPO_DIR}/dist"
BLENDER_BIN="${BLENDER_BIN:-blender}"

VERSION="$(python3 -c 'import pathlib, sys, tomllib; print(tomllib.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))["version"])' "${MANIFEST_PATH}")"
if [[ -z "${VERSION}" ]]; then
  echo "ERROR: Could not read version from ${MANIFEST_PATH}" >&2
  exit 1
fi
ZIP_PATH="${DIST_DIR}/arckep_3d-${VERSION}.zip"

OWN_TMP_RESOURCES=""
if [[ -z "${BLENDER_USER_RESOURCES:-}" ]]; then
  OWN_TMP_RESOURCES="$(mktemp -d "${TMPDIR:-/tmp}/arckep-blender-build-XXXXXX")"
  export BLENDER_USER_RESOURCES="${OWN_TMP_RESOURCES}"
  trap 'rm -rf "${OWN_TMP_RESOURCES}"' EXIT
fi

rm -rf "${DIST_DIR}"
mkdir -p "${DIST_DIR}"

echo "==> [1/3] Validating Blender extension manifest (v${VERSION})..."
"${BLENDER_BIN}" --command extension validate "${ADDON_DIR}"

echo "==> [2/3] Building extension archive ${ZIP_PATH}..."
"${BLENDER_BIN}" --command extension build \
  --source-dir "${ADDON_DIR}" \
  --output-filepath "${ZIP_PATH}"

echo "==> [3/3] Generating static extension repository index.json..."
"${BLENDER_BIN}" --command extension server-generate \
  --repo-dir="${DIST_DIR}"

test -s "${ZIP_PATH}"
test -s "${DIST_DIR}/index.json"
echo "==> Build complete: ${ZIP_PATH} + ${DIST_DIR}/index.json"
