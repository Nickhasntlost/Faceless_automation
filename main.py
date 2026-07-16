#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger("shorts_pipeline.main")


def load_secrets() -> None:
    """Load secrets from Cloud Run environment or local .env file."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    token_json = os.environ.get("YOUTUBE_TOKEN_JSON")
    logger.debug("YOUTUBE_TOKEN_JSON is %s in environment.", "present" if token_json else "missing")

    if token_json:
        creds_dir = ROOT / "credentials"
        creds_dir.mkdir(parents=True, exist_ok=True)
        token_path = creds_dir / "token.json"
        token_path.write_text(token_json, encoding="utf-8")
        logger.debug("Wrote token.json to %s", token_path)
    else:
        logger.debug("YOUTUBE_TOKEN_JSON environment variable not found.")


load_secrets()

from src.orchestrator import SimulationFlags, run_pipeline
from src.utils.encoding import read_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 1 local YouTube Shorts pipeline (Veo 3.1 Lite)."
    )
    parser.add_argument(
        "--topic",
        type=str,
        default=None,
        help="Specific topic for the video. If empty, the AI will pick one.",
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["manual", "trend", "hybrid"],
        default="trend",
        help="Topic selection mode.",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run without paid API calls; uses ffmpeg placeholders.",
    )
    parser.add_argument(
        "--skip-upload",
        action="store_true",
        help="Skip YouTube upload even when credentials exist.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run creative agents but skip expensive media generation APIs (TTS/Veo).",
    )
    parser.add_argument(
        "--deterministic",
        action="store_true",
        help="Use deterministic settings (temperature 0) for LLM generations.",
    )
    parser.add_argument(
        "--simulate-timeout",
        action="store_true",
        help="Force an API timeout path for quality-gate testing.",
    )
    parser.add_argument(
        "--simulate-budget-breach",
        action="store_true",
        help="Force a budget-cap breach report for quality-gate testing.",
    )
    parser.add_argument(
        "--simulate-interrupt",
        action="store_true",
        help="Raise KeyboardInterrupt mid-run for INCOMPLETE testing.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    
    # Automatically switch mode to manual or hybrid if topic is provided
    # and mode is still the default "trend". If user explicitly requested "hybrid", keep it.
    mode = args.mode
    if args.topic and mode == "trend":
        mode = "manual"
        
    simulation = SimulationFlags(
        mock=args.mock,
        skip_upload=args.skip_upload,
        simulate_timeout=args.simulate_timeout,
        simulate_budget_breach=args.simulate_budget_breach,
        simulate_interrupt=args.simulate_interrupt,
        dry_run=args.dry_run,
        deterministic=args.deterministic,
    )

    try:
        report_path = run_pipeline(ROOT, simulation, topic=args.topic, mode=mode)

    except KeyboardInterrupt:
        print("Interrupted. Quality report should be marked INCOMPLETE if written.")
        return 130

    report = read_json(report_path)
    print(f"Quality verdict: {report['verdict']}")
    print(f"Report: {report_path}")
    if report.get("degradations"):
        for item in report["degradations"]:
            print(f"  - [{item['subsystem']}] {item['reason']}")
    if report.get("fatal_error"):
        print(f"Fatal: {report['fatal_error']}")
    return 0 if report["verdict"] in {"PASS", "REVIEW"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
