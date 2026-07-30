#!/usr/bin/env python3
"""Operate a shared-filesystem Pi HCA queue with serialized SQLite state changes."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from hca_contract import atomic_write_json, gpu_inventory, sha256

SCHEMA_VERSION = 1
TERMINAL_STATES = {"complete", "failed", "cancelled"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def queue_paths(root: Path) -> dict[str, Path]:
    return {"root": root, "database": root / "queue.sqlite", "configs": root / "configs", "jobs": root / "jobs", "results": root / "results"}


def initialise(root: Path) -> None:
    paths = queue_paths(root)
    for path in paths.values():
        if path != paths["database"]:
            path.mkdir(parents=True, exist_ok=True)
    with connect(root) as database:
        database.executescript("""
            CREATE TABLE IF NOT EXISTS configs (
                id TEXT PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL, path TEXT NOT NULL,
                operator TEXT NOT NULL, reviewer TEXT NOT NULL, review_path TEXT NOT NULL, published_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workers (
                id TEXT PRIMARY KEY, capabilities TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, state TEXT NOT NULL, operator TEXT NOT NULL,
                plan TEXT NOT NULL, output_dir TEXT NOT NULL, config_id TEXT NOT NULL,
                workers INTEGER NOT NULL, retries INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                submitted_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, worker_id TEXT,
                result_path TEXT, error TEXT, FOREIGN KEY(config_id) REFERENCES configs(id)
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, timestamp TEXT NOT NULL,
                state TEXT NOT NULL, message TEXT NOT NULL
            );
        """)


@contextmanager
def connect(root: Path):
    database = sqlite3.connect(queue_paths(root)["database"], timeout=30, isolation_level=None)
    try:
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA busy_timeout = 30000")
        yield database
    finally:
        database.close()


def event(database: sqlite3.Connection, job_id: str | None, state: str, message: str) -> None:
    database.execute("INSERT INTO events(job_id, timestamp, state, message) VALUES (?, ?, ?, ?)", (job_id, now(), state, message))


def require_initialised(root: Path) -> None:
    if not queue_paths(root)["database"].is_file():
        raise ValueError(f"queue is not initialized: {root}")


def publish_config(root: Path, config: Path, review: Path, operator: str) -> dict:
    require_initialised(root)
    decision = json.loads(review.read_text(encoding="utf-8"))
    if decision.get("review_status") != "approved" or not decision.get("reviewer"):
        raise ValueError("config publication requires an approved review with a named reviewer")
    digest = sha256(config)
    config_id = f"cfg-{digest[:12]}"
    destination = queue_paths(root)["configs"] / f"{config_id}.json"
    if not destination.exists():
        destination.write_bytes(config.read_bytes())
    with connect(root) as database:
        database.execute("BEGIN IMMEDIATE")
        database.execute("INSERT OR IGNORE INTO configs VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (config_id, digest, str(destination.resolve()), operator, decision["reviewer"], str(review.resolve()), now()))
        database.commit()
    return {"id": config_id, "sha256": digest, "path": str(destination), "reviewer": decision["reviewer"]}


def register_worker(root: Path, worker_id: str, capabilities: dict) -> dict:
    require_initialised(root)
    payload = {"hostname": os.uname().nodename, "gpus": gpu_inventory(), **capabilities}
    with connect(root) as database:
        database.execute("BEGIN IMMEDIATE")
        database.execute("INSERT INTO workers VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET capabilities=excluded.capabilities, updated_at=excluded.updated_at",
                         (worker_id, json.dumps(payload, sort_keys=True), now()))
        database.commit()
    return {"id": worker_id, "capabilities": payload}


def config_record(database: sqlite3.Connection, config_id: str) -> dict:
    row = database.execute("SELECT * FROM configs WHERE id = ?", (config_id,)).fetchone()
    if row is None:
        raise ValueError(f"unknown published config: {config_id}")
    return dict(row)


def submit(root: Path, plan: Path, output_dir: Path, config_id: str, runtime_lock: Path, operator: str, workers: int, retries: int) -> dict:
    require_initialised(root)
    if workers < 1 or retries < 0:
        raise ValueError("workers must be at least 1 and retries cannot be negative")
    if not plan.is_file():
        raise ValueError(f"well plan does not exist: {plan}")
    if not runtime_lock.is_file():
        raise ValueError(f"runtime lock does not exist: {runtime_lock}")
    with connect(root) as database:
        database.execute("BEGIN IMMEDIATE")
        config = config_record(database, config_id)
        job_id = f"job-{uuid.uuid4().hex[:16]}"
        submitted_at = now()
        request = {"schema_version": SCHEMA_VERSION, "job_id": job_id, "operator": operator,
                   "plan": str(plan.resolve()), "output_dir": str(output_dir.resolve()),
                   "config": {"id": config_id, "path": config["path"], "sha256": config["sha256"]},
                   "runtime_lock": {"path": str(runtime_lock.resolve()), "sha256": sha256(runtime_lock)},
                   "workers": workers, "retries": retries, "submitted_at": submitted_at}
        atomic_write_json(queue_paths(root)["jobs"] / f"{job_id}.json", request)
        database.execute("INSERT INTO jobs(id, state, operator, plan, output_dir, config_id, workers, retries, submitted_at) VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?)",
                         (job_id, operator, request["plan"], request["output_dir"], config_id, workers, retries, submitted_at))
        event(database, job_id, "queued", f"submitted by {operator}")
        database.commit()
    return request


def claim(root: Path, worker_id: str) -> dict | None:
    require_initialised(root)
    with connect(root) as database:
        database.execute("BEGIN IMMEDIATE")
        row = database.execute("SELECT * FROM jobs WHERE state = 'queued' ORDER BY submitted_at, id LIMIT 1").fetchone()
        if row is None:
            database.commit()
            return None
        job = dict(row)
        database.execute("UPDATE jobs SET state='running', worker_id=?, started_at=?, attempts=attempts+1 WHERE id=?", (worker_id, now(), job["id"]))
        event(database, job["id"], "running", f"claimed by {worker_id}")
        database.commit()
    request = json.loads((queue_paths(root)["jobs"] / f"{job['id']}.json").read_text(encoding="utf-8"))
    request["attempt"] = job["attempts"] + 1
    return request


def finish(root: Path, job_id: str, worker_id: str, returncode: int, output_dir: str, detail: str = "") -> dict:
    require_initialised(root)
    state = "complete" if returncode == 0 else "failed"
    result = {"schema_version": SCHEMA_VERSION, "job_id": job_id, "state": state, "worker_id": worker_id,
              "finished_at": now(), "returncode": returncode, "output_dir": output_dir, "detail": detail[-4000:]}
    result_path = queue_paths(root)["results"] / f"{job_id}.json"
    atomic_write_json(result_path, result)
    with connect(root) as database:
        database.execute("BEGIN IMMEDIATE")
        row = database.execute("SELECT state, worker_id FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown job: {job_id}")
        if row["state"] != "running" or row["worker_id"] != worker_id:
            raise ValueError(f"job {job_id} is not running on worker {worker_id}")
        database.execute("UPDATE jobs SET state=?, finished_at=?, result_path=?, error=? WHERE id=?", (state, result["finished_at"], str(result_path), detail[-4000:] or None, job_id))
        event(database, job_id, state, detail[-4000:] or f"worker {worker_id} finished")
        database.commit()
    return result


def cancel(root: Path, job_id: str) -> None:
    with connect(root) as database:
        database.execute("BEGIN IMMEDIATE")
        row = database.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown job: {job_id}")
        if row["state"] != "queued":
            raise ValueError("only queued jobs can be cancelled; running jobs must finish or be stopped by their worker")
        database.execute("UPDATE jobs SET state='cancelled', finished_at=? WHERE id=?", (now(), job_id))
        event(database, job_id, "cancelled", "cancelled before assignment")
        database.commit()


def retry(root: Path, job_id: str) -> None:
    with connect(root) as database:
        database.execute("BEGIN IMMEDIATE")
        row = database.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None or row["state"] not in {"failed", "cancelled"}:
            raise ValueError("only failed or cancelled jobs can be retried")
        database.execute("UPDATE jobs SET state='queued', started_at=NULL, finished_at=NULL, worker_id=NULL, result_path=NULL, error=NULL WHERE id=?", (job_id,))
        event(database, job_id, "queued", "requeued by operator")
        database.commit()


def run_claimed(root: Path, worker_id: str, command: str, min_free_gib: int) -> dict | None:
    request = claim(root, worker_id)
    if request is None:
        return None
    script_dir = Path(__file__).parent
    invocation = [sys.executable, str(script_dir / "hca_runner.py"), "--plan", request["plan"], "--output-dir", request["output_dir"],
                  "--command", command, "--config", request["config"]["path"], "--workers", str(request["workers"]),
                  "--retries", str(request["retries"]), "--min-free-gib", str(min_free_gib),
                  "--runtime-lock", request["runtime_lock"]["path"]]
    process = subprocess.run(invocation, capture_output=True, text=True)
    return finish(root, request["job_id"], worker_id, process.returncode, request["output_dir"], process.stdout + "\n" + process.stderr)


def rows(root: Path, table: str) -> list[dict]:
    require_initialised(root)
    with connect(root) as database:
        return [dict(row) for row in database.execute(f"SELECT * FROM {table} ORDER BY " + ("submitted_at DESC" if table == "jobs" else "id"))]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue-dir", required=True, type=Path)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("init")
    publish = commands.add_parser("publish-config")
    publish.add_argument("--config", required=True, type=Path); publish.add_argument("--review", required=True, type=Path); publish.add_argument("--operator", required=True)
    worker = commands.add_parser("register-worker")
    worker.add_argument("--worker-id", required=True); worker.add_argument("--capabilities", default="{}")
    submit_parser = commands.add_parser("submit")
    submit_parser.add_argument("--plan", required=True, type=Path); submit_parser.add_argument("--output-dir", required=True, type=Path); submit_parser.add_argument("--config-id", required=True); submit_parser.add_argument("--runtime-lock", required=True, type=Path); submit_parser.add_argument("--operator", required=True); submit_parser.add_argument("--workers", type=int, default=1); submit_parser.add_argument("--retries", type=int, default=1)
    for action in ("status", "report", "workers"):
        commands.add_parser(action)
    for action in ("cancel", "retry"):
        item = commands.add_parser(action); item.add_argument("--job-id", required=True)
    dispatch = commands.add_parser("dispatch")
    dispatch.add_argument("--worker-id", required=True); dispatch.add_argument("--command", required=True); dispatch.add_argument("--min-free-gib", type=int, default=8); dispatch.add_argument("--max-jobs", type=int, default=1)
    args = parser.parse_args()
    try:
        if args.action == "init":
            initialise(args.queue_dir); payload = {"queue_dir": str(args.queue_dir), "status": "ready"}
        elif args.action == "publish-config":
            payload = publish_config(args.queue_dir, args.config, args.review, args.operator)
        elif args.action == "register-worker":
            payload = register_worker(args.queue_dir, args.worker_id, json.loads(args.capabilities))
        elif args.action == "submit":
            payload = submit(args.queue_dir, args.plan, args.output_dir, args.config_id, args.runtime_lock, args.operator, args.workers, args.retries)
        elif args.action == "cancel":
            cancel(args.queue_dir, args.job_id); payload = {"job_id": args.job_id, "state": "cancelled"}
        elif args.action == "retry":
            retry(args.queue_dir, args.job_id); payload = {"job_id": args.job_id, "state": "queued"}
        elif args.action == "workers":
            payload = rows(args.queue_dir, "workers")
        elif args.action == "status":
            payload = rows(args.queue_dir, "jobs")
        elif args.action == "report":
            jobs = rows(args.queue_dir, "jobs"); payload = {"jobs": len(jobs), "states": {state: sum(item["state"] == state for item in jobs) for state in sorted({item["state"] for item in jobs})}, "recent": jobs[:10]}
        else:
            completed = [result for _ in range(args.max_jobs) if (result := run_claimed(args.queue_dir, args.worker_id, args.command, args.min_free_gib)) is not None]
            payload = {"worker_id": args.worker_id, "completed": completed}
        print(json.dumps(payload, indent=2))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
