"""
services/job_queue.py
─────────────────────────────────────────────────────────────────────────────
Simple background job queue using Python's stdlib Queue + threading.

Design goals:
  • Zero external dependencies (no Redis, no Celery needed now)
  • Drop-in replacement path: swap _Worker for Celery task later
  • Thread-safe job submission from any Flask request
  • Failed jobs are logged and never crash the worker
  • Easy to upgrade: just change submit_job() to enqueue a Celery task

Usage:
    from services.job_queue import submit_job

    def my_heavy_function(arg1, arg2):
        ...

    submit_job(my_heavy_function, arg1, arg2)
"""

import queue
import threading
import traceback
import logging
import time
from typing import Callable, Any

logger = logging.getLogger("phi.job_queue")

# ── Internal job queue ────────────────────────────────────────────────────────
# maxsize=500 — prevents memory blow-up if jobs pile up faster than consumed
_job_queue: queue.Queue = queue.Queue(maxsize=500)

# ── Stats for monitoring ──────────────────────────────────────────────────────
_stats = {
    "submitted":  0,
    "completed":  0,
    "failed":     0,
    "queue_full": 0,
}


class _Worker(threading.Thread):
    """
    Single daemon worker thread that pulls jobs from the queue and runs them.
    Daemon=True means it dies automatically when the main process exits.
    """

    def __init__(self, worker_id: int):
        super().__init__(name=f"phi-worker-{worker_id}", daemon=True)
        self._running = True

    def run(self):
        logger.info(f"[JQ] Worker {self.name} started")
        while self._running:
            try:
                # Block for up to 2 seconds — allows clean shutdown check
                fn, args, kwargs, job_id = _job_queue.get(timeout=2)
            except queue.Empty:
                continue

            start = time.time()
            try:
                fn(*args, **kwargs)
                elapsed = round(time.time() - start, 2)
                _stats["completed"] += 1
                logger.info(f"[JQ] Job {job_id} completed in {elapsed}s")
            except Exception as e:
                _stats["failed"] += 1
                logger.error(
                    f"[JQ] Job {job_id} FAILED after {round(time.time()-start,2)}s: "
                    f"{type(e).__name__}: {e}"
                )
                traceback.print_exc()
            finally:
                _job_queue.task_done()

    def stop(self):
        self._running = False


# ── Worker pool ───────────────────────────────────────────────────────────────
_workers: list[_Worker] = []
_initialized = False


def init_workers(num_workers: int = 3) -> None:
    """
    Start the background worker pool.
    Call once from app.py at startup. Safe to call multiple times.

    num_workers=3 handles concurrent document processing without overloading
    a single-core VM. Increase to 5-8 on multi-core production servers.
    """
    global _initialized
    if _initialized:
        return

    for i in range(num_workers):
        w = _Worker(i + 1)
        w.start()
        _workers.append(w)

    _initialized = True
    logger.info(f"[JQ] {num_workers} background workers started")


def submit_job(fn: Callable, *args: Any, **kwargs: Any) -> str:
    """
    Submit a function to run in the background.
    Returns a job_id string for logging/tracking.
    Never raises — logs and drops the job if queue is full.

    Upgrade path: replace this function body with:
        celery_task.delay(*args, **kwargs)
    and nothing else in the codebase needs to change.
    """
    import uuid
    job_id = uuid.uuid4().hex[:8]

    try:
        _job_queue.put_nowait((fn, args, kwargs, job_id))
        _stats["submitted"] += 1
        logger.info(f"[JQ] Job {job_id} submitted: {fn.__name__}")
        return job_id
    except queue.Full:
        _stats["queue_full"] += 1
        logger.error(
            f"[JQ] Queue full — job {fn.__name__} DROPPED. "
            "Consider increasing num_workers or adding Redis/Celery."
        )
        return job_id


def queue_stats() -> dict:
    """Return current queue health metrics. Exposed via /api/v1/stats."""
    return {
        "queue_size":     _job_queue.qsize(),
        "queue_capacity": _job_queue.maxsize,
        "workers_active": len([w for w in _workers if w.is_alive()]),
        **_stats,
    }