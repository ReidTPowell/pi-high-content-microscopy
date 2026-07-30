#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s --env <path> [--extras <comma-separated extras>] [--lock-file <path>]\n' "$0"
  printf 'Example: %s --env .venv-hca --extras qc,cellpose --lock-file runtime-lock.json\n' "$0"
}

env_path=""
extras="qc"
lock_file=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --env) env_path="$2"; shift 2 ;;
    --extras) extras="$2"; shift 2 ;;
    --lock-file) lock_file="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done
[ -n "$env_path" ] || { usage; exit 2; }
python3 -m venv "$env_path"
"$env_path/bin/python" -m pip install --upgrade pip
repo_root=$(cd "$(dirname "$0")/../../.." && pwd)
"$env_path/bin/python" -m pip install -e "${repo_root}[${extras}]"
"$env_path/bin/python" -c "import numpy, tifffile; print('Imaging runtime ready')" 2>/dev/null || true
if [ -n "$lock_file" ]; then
  "$env_path/bin/python" "$(dirname "$0")/hca_runtime.py" capture --output "$lock_file"
fi
printf 'Environment ready: %s\n' "$env_path"
