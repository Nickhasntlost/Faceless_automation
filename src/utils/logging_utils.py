from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path


def setup_logging(run_dir: Path, level: int = logging.INFO) -> logging.Logger:
    run_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("shorts_pipeline")
    logger.setLevel(level)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    file_handler = logging.FileHandler(run_dir / "pipeline.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
