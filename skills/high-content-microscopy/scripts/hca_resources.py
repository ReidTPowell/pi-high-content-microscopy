"""Resource discovery and deterministic GPU admission for PiHCA workloads."""
from __future__ import annotations

import fcntl
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


GPU_QUERY = (
    "index,name,memory.total,memory.used,memory.free,utilization.gpu"
)


def gpu_inventory() -> list[dict]:
    """Return current NVIDIA resources; hosts without nvidia-smi have no GPUs."""
    try:
        output = subprocess.check_output(
            ["nvidia-smi", f"--query-gpu={GPU_QUERY}", "--format=csv,noheader,nounits"],
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return []
    inventory = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 6:
            continue
        try:
            inventory.append({
                "index": int(parts[0]), "name": parts[1], "total_mib": int(parts[2]),
                "used_mib": int(parts[3]), "free_mib": int(parts[4]),
                "utilization_percent": int(parts[5]),
            })
        except ValueError:
            continue
    return sorted(inventory, key=lambda gpu: gpu["index"])


def lock_path(gpu: int) -> Path:
    return Path("/tmp") / f"pihca-gpu-{gpu}.lock"


def lock_available(gpu: int) -> bool:
    """Check a PiHCA cooperative lock without retaining it."""
    path = lock_path(gpu)
    with path.open("a", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return True


def admit_gpus(spec: str, *, min_free_gib: float = 8.0,
               max_utilization: int = 80, require_unlocked: bool = True,
               inventory: list[dict] | None = None) -> list[int]:
    """Resolve auto, none, or explicit IDs against live resource constraints."""
    current = gpu_inventory() if inventory is None else inventory
    by_id = {int(gpu["index"]): gpu for gpu in current}
    if spec == "none":
        return []
    if spec == "auto":
        requested = sorted(by_id)
    else:
        try:
            requested = list(dict.fromkeys(int(value.strip()) for value in spec.split(",") if value.strip()))
        except ValueError as error:
            raise ValueError("--gpus must be auto, none, or comma-separated integer IDs") from error
        missing = sorted(set(requested) - set(by_id))
        if missing:
            raise ValueError(f"requested GPUs are unavailable: {missing}")
    admitted = []
    minimum = int(min_free_gib * 1024)
    for gpu_id in requested:
        gpu = by_id[gpu_id]
        eligible = (int(gpu.get("free_mib", 0)) >= minimum
                    and int(gpu.get("utilization_percent", 0)) <= max_utilization)
        if eligible and (not require_unlocked or lock_available(gpu_id)):
            admitted.append(gpu_id)
        elif spec != "auto":
            raise ValueError(
                f"requested GPU {gpu_id} failed admission control "
                f"(free_mib={gpu.get('free_mib')}, utilization={gpu.get('utilization_percent')})"
            )
    return admitted


def resolve_workers(requested: int, *, gpu_ids: list[int], requires_gpu: bool,
                    cpu_default: int = 1, job_count: int | None = None) -> int:
    """Resolve zero as auto and cap GPU work at one process per admitted GPU."""
    if requested < 0:
        raise ValueError("workers cannot be negative")
    if requires_gpu and not gpu_ids:
        raise ValueError("approved configuration requires a GPU, but none passed admission control")
    automatic = len(gpu_ids) if requires_gpu else max(1, cpu_default)
    workers = automatic if requested == 0 else requested
    if requires_gpu:
        workers = min(workers, len(gpu_ids))
    if job_count is not None:
        workers = min(workers, max(1, job_count))
    return max(1, workers)


@contextmanager
def gpu_reservation(gpu: int | None) -> Iterator[str | None]:
    """Serialize GPU use across independent PiHCA processes on one host."""
    if gpu is None:
        yield None
        return
    path = lock_path(gpu)
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield str(path)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
