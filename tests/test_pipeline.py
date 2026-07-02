from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_read_json_utf16_fallback(tmp_path: Path):
    from src.utils.encoding import read_json

    payload = {"hello": "world"}
    path = tmp_path / "utf16.json"
    path.write_text(json.dumps(payload), encoding="utf-16")
    assert read_json(path) == payload


def test_budget_guard_blocks_threshold(tmp_path: Path):
    from src.config_loader import load_pricing_config
    from src.module3_budget_guard import BudgetGuard
    from src.utils.api_client import BudgetExceededError

    pricing = load_pricing_config(ROOT)
    store = tmp_path / "budget.json"
    guard = BudgetGuard(store, threshold_usd=1.0, total_credit_usd=300.0, pricing=pricing)
    guard.record_spend(0.95, "seed")
    with pytest.raises(BudgetExceededError):
        guard.assert_can_spend(0.10, "veo_scene_1")


def test_voice_mark_count_scales_with_words():
    from src.module2_voice import _build_ssml, _timings_from_marks, _tokenize_words

    text = "AI tools are reshaping how teams ship products every single day now"
    words = _tokenize_words(text)
    ssml = _build_ssml(words)
    assert ssml.count("<mark") == len(words)
    marks = [(f"w{i}", i * 0.3) for i in range(len(words))]
    timings = _timings_from_marks(words, marks, audio_duration=len(words) * 0.35)
    assert len(timings) == len(words)


def test_forced_timeout_report(tmp_path: Path, monkeypatch):
    from src.orchestrator import SimulationFlags, run_pipeline

    monkeypatch.setattr("src.orchestrator._check_ffmpeg", lambda: None)
    report_path = run_pipeline(ROOT, SimulationFlags(mock=True, simulate_timeout=True))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["verdict"] == "FAIL"
    assert any(d["subsystem"] == "reliability" for d in report["degradations"])


def test_forced_budget_breach_report(tmp_path: Path, monkeypatch):
    from src.orchestrator import SimulationFlags, run_pipeline

    monkeypatch.setattr("src.orchestrator._check_ffmpeg", lambda: None)
    report_path = run_pipeline(ROOT, SimulationFlags(mock=True, simulate_budget_breach=True))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["verdict"] == "FAIL"
    assert any(d["subsystem"] == "budget_guard" for d in report["degradations"])


def test_forced_interrupt_report(tmp_path: Path, monkeypatch):
    from src.orchestrator import SimulationFlags, run_pipeline

    monkeypatch.setattr("src.orchestrator._check_ffmpeg", lambda: None)
    with pytest.raises(KeyboardInterrupt):
        run_pipeline(ROOT, SimulationFlags(mock=True, simulate_interrupt=True))
    runs = sorted((ROOT / "output").glob("*/quality_report.json"))
    assert runs, "Expected a quality report after interrupt"
    report = json.loads(runs[-1].read_text(encoding="utf-8"))
    assert report["verdict"] == "INCOMPLETE"
    assert report["incomplete"] is True


def test_full_narration_issue_1():
    from src.models import ChannelIdentity
    from src.module1_script import _parse_script_payload

    payload = {
        "title": "T", "description": "D", "tags": [],
        "hook": "H", "body": "B", "loop_ending": "L",
        "scenes": [
            {"index": 1, "narration": "N1", "visual_prompt": "V1"},
            {"index": 2, "narration": "N2", "visual_prompt": "V2"}
        ]
    }
    identity = ChannelIdentity("", "", "", "", [], [], [])
    script = _parse_script_payload(payload, identity)
    
    assert script.full_narration == "N1 N2"
    assert "H" not in script.full_narration
    assert "B" not in script.full_narration
    assert "L" not in script.full_narration


def test_ssml_mark_placement_issue_3():
    from src.module2_voice import _build_ssml, _tokenize_words, _timings_from_marks

    text = "hello world"
    words = _tokenize_words(text)
    ssml = _build_ssml(words)

    # Mark must precede the word
    assert '<mark name="w0"/>hello' in ssml
    assert '<mark name="w1"/>world' in ssml

    marks = [("w0", 0.0), ("w1", 1.0)]
    timings = _timings_from_marks(words, marks, audio_duration=2.0)
    assert timings[0].start_seconds < timings[0].end_seconds
    assert timings[1].start_seconds < timings[1].end_seconds


def test_thumbnail_cost_issue_11(tmp_path: Path):
    from src.module3_budget_guard import BudgetGuard
    from src.models import PricingConfig

    pricing = PricingConfig(
        verified_at="", script_model_id="", script_input_usd_per_1m_tokens=0.0,
        script_output_usd_per_1m_tokens=0.0, veo_model_id="", veo_usd_per_second={},
        thumbnail_model_id="", thumbnail_usd_per_1k_image=0.067, tts_usd_per_1m_characters=0.0
    )
    guard = BudgetGuard(tmp_path / "dummy.json", 1.0, 1.0, pricing)
    # Should be per image, not per 1k images
    assert guard.estimate_thumbnail_cost() == 0.000067
