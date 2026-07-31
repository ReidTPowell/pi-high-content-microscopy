#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s --env <path> [--conda <path>] [--lock-file <path>]\n' "$0"
}

env_path=""
conda_path="/opt/anaconda3/bin/conda"
lock_file=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --env) env_path="$2"; shift 2 ;;
    --conda) conda_path="$2"; shift 2 ;;
    --lock-file) lock_file="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

[ -n "$env_path" ] || { usage; exit 2; }
"$conda_path" create --yes --prefix "$env_path" python=3.10 pip
"$env_path/bin/python" -m pip install \
  'numpy>=1.26,<3' 'Pillow>=10,<13' 'pandas>=2,<3' \
  'torch>=2.2' 'huggingface-hub>=0.24' 'timm>=1,<2' \
  'safetensors>=0.4' 'PyYAML>=6,<7'
"$env_path/bin/python" -c 'import PIL,huggingface_hub,numpy,torch; print("OpenPhenom adapter runtime ready")'
if [ -n "$lock_file" ]; then
  mkdir -p "$(dirname "$lock_file")"
  "$env_path/bin/python" -m pip freeze > "$lock_file"
fi
printf 'OpenPhenom environment ready: %s\n' "$env_path"
printf 'Model weights are downloaded separately; review their license before enabling embeddings.\n'
