"""Background detection jobs: each job runs in its own LOW-PRIORITY subprocess
(src.wildfire.console.worker), never inside the server process — heavy
inference used to starve the UI event loop and every page went "Failed to
fetch" while a job ran. The manager just spawns the worker and polls the
run folder's _progress.json.

Each job writes a normal run folder (outputs/console_<ts>/ with batch.json),
so finished jobs show up through the regular discover_scans path.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from ..config import PROJECT_ROOT, Settings


@dataclass
class Job:
    id: str  # equals the run folder name
    state: str = "queued"  # queued | running | done | error
    total: int = 0
    done: int = 0
    current: str = ""
    error: Optional[str] = None
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    finished: Optional[str] = None
    # Pace is measured from the first finished image, not from launch: the
    # worker spends 10-30 s importing torch and loading weights before it
    # touches a photo, and counting that would make every early estimate wild.
    _paced_from: Optional[tuple[float, int]] = field(default=None, repr=False)

    def record_progress(self, done: int, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        if done > 0 and self._paced_from is None:
            self._paced_from = (now, done)
        self.done = done

    def eta_seconds(self, now: Optional[float] = None) -> Optional[int]:
        """Seconds left at the pace so far; None until two images give a pace."""
        if self.state not in ("queued", "running") or self._paced_from is None:
            return None
        t0, d0 = self._paced_from
        if self.done <= d0 or not self.total:
            return None
        now = time.monotonic() if now is None else now
        per_image = (now - t0) / (self.done - d0)
        return max(0, round(per_image * (self.total - self.done)))

    def to_dict(self) -> dict:
        return {"id": self.id, "state": self.state, "total": self.total, "done": self.done,
                "current": self.current, "error": self.error, "created": self.created,
                "finished": self.finished, "eta_s": self.eta_seconds(),
                "progress": round(self.done / self.total, 3) if self.total else 0.0}


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        # Called with the Job when it ends (done or error), from the monitor
        # thread. The desktop window hooks in here to notify the operator, who
        # has usually walked away from a 20-minute flight.
        self.on_finish: list[Callable[[Job], None]] = []

    # ------------------------------------------------------------- queries
    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def active(self) -> list[Job]:
        return [j for j in self._jobs.values() if j.state in ("queued", "running")]

    def all(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.created, reverse=True)

    def _finished(self, job: Job) -> None:
        job.finished = datetime.now().isoformat(timespec="seconds")
        for callback in list(self.on_finish):
            try:
                callback(job)
            except Exception as exc:  # a failed notification must not kill the monitor
                print(f"[jobs] on_finish callback failed: {exc}")

    def reset_detectors(self) -> None:
        """No-op since workers are per-job processes; kept for API compatibility."""

    # ------------------------------------------------------------- detection
    def start_detection(self, image_paths: list[Path], settings: Settings) -> Job:
        """Spawn a worker process for these images and monitor it."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_id = f"console_{ts}"
        job = Job(id=run_id, total=len(image_paths))
        with self._lock:
            self._jobs[run_id] = job

        run_dir = settings.output_path / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        spec_path = run_dir / "_job.json"
        spec_path.write_text(json.dumps({
            "paths": [str(p) for p in image_paths],
            "run_dir": str(run_dir),
            "batch_label": run_id,
            "settings": asdict(settings),
        }), encoding="utf-8")

        # Below-normal priority keeps the console (and the operator's machine)
        # responsive while torch saturates the cores. These are Windows creation
        # flags and resolve to 0 elsewhere (POSIX rejects a non-zero value); on
        # macOS/Linux the worker nices itself down at startup instead.
        flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                 | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
        log = open(run_dir / "_worker.log", "ab")
        proc = subprocess.Popen(
            [sys.executable, "-m", "src.wildfire.console.worker", str(spec_path)],
            cwd=str(PROJECT_ROOT), creationflags=flags,
            stdout=log, stderr=subprocess.STDOUT,
        )

        def monitor() -> None:
            progress_file = run_dir / "_progress.json"
            job.state = "running"
            while True:
                try:
                    p = json.loads(progress_file.read_text(encoding="utf-8"))
                    job.record_progress(int(p.get("done", job.done)))
                    job.total = int(p.get("total", job.total)) or job.total
                    job.current = p.get("current", "")
                    if p.get("state") == "error":
                        job.error = p.get("error")
                except Exception:
                    pass  # not written yet / mid-swap
                rc = proc.poll()
                if rc is not None:
                    if job.error is None and rc != 0:
                        job.error = f"worker exited with code {rc} (see {run_dir.name}/_worker.log)"
                    job.state = "error" if job.error else "done"
                    job.current = ""
                    log.close()
                    self._finished(job)
                    return
                time.sleep(1.0)

        threading.Thread(target=monitor, name=f"monitor-{run_id}", daemon=True).start()
        return job
