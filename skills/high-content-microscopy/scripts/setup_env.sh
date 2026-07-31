#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s --env <path> [--extras <comma-separated extras>] [--lock-file <path>] [--python <interpreter>] [--editable]\n' "$0"
  printf 'Example: %s --env .venv-hca --extras ome,qc,review,cellpose --lock-file runtime-lock.json\n' "$0"
}

env_path=""
extras="all"
lock_file=""
python_bin="python3"
editable=false
while [ "$#" -gt 0 ]; do
  case "$1" in
    --env) env_path="$2"; shift 2 ;;
    --extras) extras="$2"; shift 2 ;;
    --lock-file) lock_file="$2"; shift 2 ;;
    --python) python_bin="$2"; shift 2 ;;
    --editable) editable=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done
[ -n "$env_path" ] || { usage; exit 2; }
"$python_bin" -m venv "$env_path"
"$env_path/bin/python" -m pip install --upgrade pip
repo_root=$(cd "$(dirname "$0")/../../.." && pwd)
install_args=(install)
if $editable; then install_args+=(-e); fi
"$env_path/bin/python" -m pip "${install_args[@]}" "${repo_root}[${extras}]"
"$env_path/bin/python" - "$repo_root" <<'PY'
import importlib.metadata
import pathlib
import sys
import tomllib

expected = tomllib.loads((pathlib.Path(sys.argv[1]) / "pyproject.toml").read_text())["project"]["version"]
versions = {distribution.version for distribution in importlib.metadata.distributions()
            if (distribution.metadata.get("Name") or "").lower() == "pi-high-content-microscopy"}
if versions != {expected}:
    raise SystemExit(f"ambiguous PiHCA installation: expected only {expected}, found {sorted(versions)}")
print(f"PiHCA {expected} installed")
PY
"$env_path/bin/python" -c "import hca_contract, hca_runner, hca_release; print('PiHCA modules ready')"
IFS=',' read -ra requested_extras <<< "$extras"
for extra in "${requested_extras[@]}"; do
  case "$extra" in
    ome) "$env_path/bin/python" -c "import tifffile" ;;
    bioformats) "$env_path/bin/python" -c "import bioio" ;;
    qc) "$env_path/bin/python" -c "import numpy, scipy, tifffile" ;;
    review) "$env_path/bin/python" -c "import numpy, tifffile, PIL" ;;
    cellpose) "$env_path/bin/python" -c "import cellpose, torch, tifffile" ;;
    stardist) "$env_path/bin/python" -c "import tensorflow; from stardist.models import StarDist2D; import csbdeep, tifffile" ;;
    all) "$env_path/bin/python" -c "import bioio, cellpose, numpy, scipy, tensorflow, tifffile, torch; from PIL import Image; from stardist.models import StarDist2D" ;;
    *) printf 'Unknown extra: %s\n' "$extra" >&2; exit 2 ;;
  esac
done
if [ -n "$lock_file" ]; then
  "$env_path/bin/python" "$(dirname "$0")/hca_runtime.py" capture --output "$lock_file"
fi
printf 'Environment ready: %s\n' "$env_path"
