"""Detection worker PROCESS: python -m src.wildfire.console.worker <spec.json>

Detection saturates CPU/GPU; running it inside the console's server process
starved the event loop and made the whole UI unresponsive ("Failed to fetch"
while a job runs). This worker runs in its own low-priority process instead:
the console spawns it, then just polls <run_dir>/_progress.json.

The spec carries the full settings snapshot so the job uses exactly what the
UI showed when it was queued (and tests can inject settings).
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path


def _lower_priority() -> None:
    """Demote this process so the console stays responsive during inference.

    Windows gets BELOW_NORMAL_PRIORITY_CLASS from the parent at spawn time (see
    jobs.py). POSIX has no equivalent creation flag, and `preexec_fn` is unsafe
    to use from the server's threads, so on macOS/Linux the worker nices itself
    down here instead - before torch is imported and starts claiming cores.
    """
    try:
        os.nice(10)
    except (AttributeError, OSError):  # no os.nice on Windows; a hardened host may refuse
        pass


def _write_progress(path: Path, state: str, done: int = 0, total: int = 0,
                    current: str = "", error: str | None = None) -> None:
    payload = {"state": state, "done": done, "total": total,
               "current": current, "error": error}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)  # atomic-ish: the poller never sees a half-written file


def _settings_from(spec: dict):
    from ..config import ModelSource, Settings, _modelsource_keys, _scalar_keys, load_settings

    data = spec.get("settings")
    if not data:
        return load_settings()
    kwargs = {k: v for k, v in data.items() if k in _scalar_keys()}
    ms_keys = _modelsource_keys()
    kwargs["model_sources"] = [
        ModelSource(**{k: v for k, v in m.items() if k in ms_keys})
        for m in data.get("model_sources", []) if isinstance(m, dict)
    ]
    return Settings(**kwargs)


def main() -> int:
    _lower_priority()
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    run_dir = Path(spec["run_dir"])
    run_dir.mkdir(parents=True, exist_ok=True)
    progress_file = run_dir / "_progress.json"
    try:
        settings = _settings_from(spec)
        _write_progress(progress_file, "running",
                        total=len(spec["paths"]), current="loading detection models...")

        from ..detectors import build_detectors
        from ..pipeline import run_batch

        detectors = build_detectors(settings, log=print)

        def cb(cur: int, tot: int, name: str) -> None:
            _write_progress(progress_file, "running", cur, tot, name)

        batch = run_batch(spec["paths"], detectors, settings, progress=cb,
                          out_dir=run_dir, batch_label=spec.get("batch_label", run_dir.name))
        (run_dir / "batch.json").write_text(
            json.dumps(batch.to_dict(run_dir), indent=2), encoding="utf-8")
        _write_progress(progress_file, "done",
                        len(spec["paths"]), len(spec["paths"]))
        return 0
    except Exception as e:
        traceback.print_exc()
        _write_progress(progress_file, "error", error=f"{type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
