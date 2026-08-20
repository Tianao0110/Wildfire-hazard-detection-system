"""CLI: build a PDF field report from a batch.json (Layer 2).

Runs the LM Studio (Qwen) analysis if the local server is reachable; otherwise the
PDF is still produced with a graceful "AI analysis unavailable" note.

Usage (from project root):
    python -m scripts.generate_report                       # outputs/batch.json -> outputs/report.pdf
    python -m scripts.generate_report outputs/batch.json --out outputs/report.pdf
    python -m scripts.generate_report --no-llm              # skip LM Studio
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from src.wildfire.config import load_settings  # noqa: E402
from src.wildfire.llm import generate_analysis, resolve_model_id  # noqa: E402
from src.wildfire.report import build_report, build_summary_text  # noqa: E402
from src.wildfire.types import BatchResult  # noqa: E402


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Build a PDF report from a batch.json.")
    ap.add_argument("batch_json", nargs="?", default=None, help="Path to batch.json (default: <output_dir>/batch.json).")
    ap.add_argument("--out", default=None, help="Output PDF path (default: <output_dir>/report.pdf).")
    ap.add_argument("--no-llm", action="store_true", help="Skip LM Studio analysis.")
    return ap.parse_args()


def get_ai_text(settings, summary: str) -> str | None:
    """Return Qwen analysis text, or None (with a printed note) if unavailable."""
    model, err = resolve_model_id(settings.lmstudio_url, settings.lmstudio_model)
    if not model:
        print(f"  LM Studio: {err}")
        return None
    print(f"  LM Studio model: {model} — generating analysis...")
    text, aerr = generate_analysis(summary, settings.lmstudio_url, model)
    if text is None:
        print(f"  LM Studio: {aerr}")
    return text


def main() -> int:
    args = _parse_args()
    settings = load_settings()
    batch_path = Path(args.batch_json) if args.batch_json else settings.output_path / "batch.json"
    if not batch_path.exists():
        print(f"batch.json not found: {batch_path}. Run scripts.run_detection first.", file=sys.stderr)
        return 2

    batch = BatchResult.from_dict(json.loads(batch_path.read_text(encoding="utf-8")),
                                 batch_path.parent)
    out_pdf = Path(args.out) if args.out else (batch_path.parent / "report.pdf")

    ai_text = None
    if not args.no_llm:
        ai_text = get_ai_text(settings, build_summary_text(batch))

    print("Building PDF...")
    path = build_report(batch, out_pdf, ai_text=ai_text)
    print(f"Wrote {path}  ({path.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
