#!/usr/bin/env python3
"""Run OpenPhenom in an isolated environment under a reproducible embedding contract."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from hca_contract import atomic_write_json, sha256


def resolve_environment_python(conda: Path, environment: str) -> Path:
    requested = Path(environment).expanduser()
    if requested.is_dir() and (requested / "bin/python").is_file():
        return requested / "bin/python"
    process = subprocess.run([str(conda), "env", "list", "--json"], capture_output=True, text=True)
    if process.returncode:
        raise ValueError(process.stderr.strip() or "could not query Conda environments")
    environments = json.loads(process.stdout).get("envs", [])
    matches = [Path(path) / "bin/python" for path in environments if Path(path).name == environment]
    if len(matches) != 1 or not matches[0].is_file():
        raise ValueError(f"Conda environment not found or ambiguous: {environment}")
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--well", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--adapter-script", required=True, type=Path,
                        help="Versioned OpenPhenom adapter implementing extract-embeddings")
    parser.add_argument("--conda", default="/opt/anaconda3/bin/conda")
    parser.add_argument("--environment", default="openphenom")
    parser.add_argument("--model-revision", help="Optional immutable Hugging Face OpenPhenom revision")
    parser.add_argument("--channelwise", action="store_true")
    args = parser.parse_args()
    if not args.adapter_script.is_file():
        parser.error(f"OpenPhenom adapter does not exist: {args.adapter_script}")
    try:
        environment_python = resolve_environment_python(Path(args.conda), args.environment)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    command = [str(environment_python), str(args.adapter_script), "extract-embeddings",
               "--path", str(args.source_root), "--well", args.well, "--format", "json"]
    if args.channelwise:
        command.append("--channelwise")
    environment = os.environ.copy()
    environment.update({"CONDA_PREFIX": str(environment_python.parents[1]), "CONDA_DEFAULT_ENV": args.environment})
    if args.model_revision:
        environment["OPENPHENOM_REVISION"] = args.model_revision
    environment["PATH"] = str(environment_python.parent) + os.pathsep + environment.get("PATH", "")
    process = subprocess.run(command, capture_output=True, text=True, env=environment)
    try:
        embedding_result = json.loads(process.stdout) if process.returncode == 0 else None
    except json.JSONDecodeError:
        embedding_result = None
    revision_match = re.search(r"/snapshots/([0-9a-f]{7,64})", process.stderr)
    result_count = len(embedding_result.get("results", [])) if isinstance(embedding_result, dict) else None
    if process.returncode == 0 and result_count == 0:
        process_returncode = 2
        validation_error = f"adapter returned no embedding groups for well {args.well}"
    else:
        process_returncode = process.returncode
        validation_error = None
    payload = {"schema_version": 1, "provider": "recursionpharma/OpenPhenom", "well": args.well,
               "source_root": str(args.source_root.resolve()), "adapter_script": str(args.adapter_script.resolve()),
               "adapter_sha256": sha256(args.adapter_script), "environment": args.environment,
               "environment_python": str(environment_python),
               "channelwise": args.channelwise, "returncode": process_returncode,
               "requested_model_revision": args.model_revision,
               "model_revision": revision_match.group(1) if revision_match else args.model_revision,
               "result": embedding_result, "result_count": result_count, "validation_error": validation_error,
               "stdout": None if embedding_result is not None else process.stdout[-200000:], "stderr": process.stderr[-10000:]}
    atomic_write_json(args.output, payload)
    print(json.dumps({"provider": payload["provider"], "well": args.well, "returncode": process_returncode,
                      "result_count": result_count, "output": str(args.output)}))
    return 0 if process_returncode == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
