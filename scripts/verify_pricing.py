"""Verify pricing constants against documented Google sources."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRICING_PATH = ROOT / "config" / "pricing_verified.json"

EXPECTED = {
    "script_model_id": "gemini-3.5-flash",
    "veo_model_id": "veo-3.1-lite-generate-preview",
    "veo_720p": 0.05,
    "veo_1080p": 0.08,
    "thumbnail_model_id": "gemini-3.1-flash-image",
    "tts_studio_per_1m": 160.0,
}


def main() -> int:
    data = json.loads(PRICING_PATH.read_text(encoding="utf-8"))
    models = data["models"]
    checks = [
        models["script_model"]["id"] == EXPECTED["script_model_id"],
        models["veo_model"]["id"] == EXPECTED["veo_model_id"],
        models["veo_model"]["usd_per_second"]["720p"] == EXPECTED["veo_720p"],
        models["veo_model"]["usd_per_second"]["1080p"] == EXPECTED["veo_1080p"],
        models["thumbnail_model"]["id"] == EXPECTED["thumbnail_model_id"],
        models["tts_studio"]["usd_per_1m_characters"] == EXPECTED["tts_studio_per_1m"],
    ]
    if all(checks):
        print("pricing_verified.json matches documented build-time constants.")
        print(f"Last verified_at: {data.get('verified_at')}")
        print("Sources:")
        for source in data.get("sources", []):
            print(f"  - {source}")
        return 0
    print("Pricing verification failed. Update config/pricing_verified.json from live docs.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
