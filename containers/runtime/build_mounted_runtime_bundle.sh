#!/usr/bin/env bash
set -euo pipefail

OUTPUT_PATH="${1:-/opt/openhands-runtime/current}"
CONTAINER_PATH="${2:-/opt/openhands-runtime}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
MICROMAMBA_IMAGE="${MICROMAMBA_IMAGE:-mambaorg/micromamba:1.5.10}"
DOCKER_PLATFORM="${DOCKER_PLATFORM:-linux/amd64}"
INSTALL_RUNTIME_GROUP="${INSTALL_RUNTIME_GROUP:-0}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP_PATH="${OUTPUT_PATH}.tmp.$$"

if [[ "${CONTAINER_PATH}" != /* ]]; then
  echo "Container path must be absolute: ${CONTAINER_PATH}" >&2
  exit 1
fi

if [[ -e "${OUTPUT_PATH}" && "${FORCE:-0}" != "1" ]]; then
  echo "Output path already exists: ${OUTPUT_PATH}" >&2
  echo "Set FORCE=1 to replace it." >&2
  exit 1
fi

cleanup() {
  rm -rf "${TMP_PATH}"
}
trap cleanup EXIT

mkdir -p "$(dirname "${OUTPUT_PATH}")"
mkdir -p "${TMP_PATH}"

docker run --rm \
  --platform "${DOCKER_PLATFORM}" \
  --user "$(id -u):$(id -g)" \
  -e CONTAINER_PATH="${CONTAINER_PATH}" \
  -e HOME=/tmp \
  -e MAMBA_ROOT_PREFIX=/tmp/micromamba \
  -e PYTHON_VERSION="${PYTHON_VERSION}" \
  -e INSTALL_RUNTIME_GROUP="${INSTALL_RUNTIME_GROUP}" \
  -e UV_CACHE_DIR=/tmp/uv-cache \
  -v "${REPO_ROOT}:/src:ro" \
  -v "${TMP_PATH}:${CONTAINER_PATH}" \
  --entrypoint /bin/bash \
  "${MICROMAMBA_IMAGE}" \
  -lc '
    set -euo pipefail
    cd /src
    micromamba create -y -p "${CONTAINER_PATH}/env" -c conda-forge "python=${PYTHON_VERSION}" pip uv
    export PATH="${CONTAINER_PATH}/env/bin:${PATH}"
    export LD_LIBRARY_PATH="${CONTAINER_PATH}/env/lib:${LD_LIBRARY_PATH:-}"
    export UV_PROJECT_ENVIRONMENT="${CONTAINER_PATH}/env"
    if [ "${INSTALL_RUNTIME_GROUP}" = "1" ]; then
      "${CONTAINER_PATH}/env/bin/uv" sync --frozen --only-group runtime-core --only-group runtime --no-install-project
    else
      "${CONTAINER_PATH}/env/bin/uv" sync --frozen --only-group runtime-core --no-install-project
    fi
    micromamba install -y -p "${CONTAINER_PATH}/env" -c conda-forge poetry tmux setuptools wheel
    "${CONTAINER_PATH}/env/bin/uv" pip install --python "${CONTAINER_PATH}/env/bin/python" /src --no-deps
    find "${CONTAINER_PATH}/env/lib" -path "*/site-packages/distutils-precedence.pth" -delete

    mkdir -p "${CONTAINER_PATH}/bin" "${CONTAINER_PATH}/meta"
    cat > "${CONTAINER_PATH}/bin/python" <<EOF
#!/usr/bin/env sh
export LD_LIBRARY_PATH="${CONTAINER_PATH}/env/lib:\${LD_LIBRARY_PATH:-}"
exec "${CONTAINER_PATH}/env/bin/python" "\$@"
EOF
    chmod +x "${CONTAINER_PATH}/bin/python"

    cat > "${CONTAINER_PATH}/bin/pip" <<EOF
#!/usr/bin/env sh
export LD_LIBRARY_PATH="${CONTAINER_PATH}/env/lib:\${LD_LIBRARY_PATH:-}"
exec "${CONTAINER_PATH}/env/bin/python" -m pip "\$@"
EOF
    chmod +x "${CONTAINER_PATH}/bin/pip"

    cat > "${CONTAINER_PATH}/bin/uv" <<EOF
#!/usr/bin/env sh
export LD_LIBRARY_PATH="${CONTAINER_PATH}/env/lib:\${LD_LIBRARY_PATH:-}"
exec "${CONTAINER_PATH}/env/bin/uv" "\$@"
EOF
    chmod +x "${CONTAINER_PATH}/bin/uv"

    cat > "${CONTAINER_PATH}/bin/poetry" <<EOF
#!/usr/bin/env sh
export LD_LIBRARY_PATH="${CONTAINER_PATH}/env/lib:\${LD_LIBRARY_PATH:-}"
exec "${CONTAINER_PATH}/env/bin/poetry" "\$@"
EOF
    chmod +x "${CONTAINER_PATH}/bin/poetry"
  '

if git -C "${REPO_ROOT}" rev-parse HEAD >"${TMP_PATH}/meta/git_commit" 2>/dev/null; then
  :
else
  echo "unknown" >"${TMP_PATH}/meta/git_commit"
fi
date -u +"%Y-%m-%dT%H:%M:%SZ" >"${TMP_PATH}/meta/built_at"
echo "${CONTAINER_PATH}" >"${TMP_PATH}/meta/container_path"

if [[ -e "${OUTPUT_PATH}" ]]; then
  rm -rf "${OUTPUT_PATH}"
fi
mv "${TMP_PATH}" "${OUTPUT_PATH}"
trap - EXIT

echo "Mounted runtime bundle written to ${OUTPUT_PATH}"
echo "Mount it at container path: ${CONTAINER_PATH}"
echo "Python executable in sandbox: ${CONTAINER_PATH}/bin/python"
