"""Unit tests for script validation rules (no network / no paid APIs)."""
from __future__ import annotations

from src.models import Scene
from src.agents.script_writer import _validate_script, estimate_spoken_duration


def _scene(index, narration, hook_type="story", performance_note="calm", visual="a b c") -> Scene:
    return Scene(
        index=index,
        narration=narration,
        visual_prompt=visual,
        emotional_beat="",
        hook_type=hook_type,
        performance_note=performance_note,
    )


def _valid_payload() -> dict:
    return {
        "comment_trigger": "Will you remember?",
        "loop_type": "question",
        "psychology_hook": "availability heuristic",
    }


def _config() -> dict:
    return {"scene_count_min": 4, "scene_count_max": 5}


def test_valid_four_scene_script_has_no_errors():
    scenes = [
        _scene(1, "Is AI erasing your memories?", hook_type="primary"),
        _scene(2, "Imagine grandma tells a warm story and nobody ever records it."),
        _scene(
            3,
            "Slowly you forget the details, then her voice. Psychologists call it the availability heuristic.",
            hook_type="proof",
        ),
        _scene(
            4,
            "One day the last person who remembers is gone. Will you recall her face or just the answer?",
            hook_type="payoff",
        ),
    ]
    errors, _ = _validate_script(scenes, _valid_payload(), _config())
    assert errors == [], errors


def test_too_many_scenes_is_error():
    scenes = [_scene(i, "short line here") for i in range(1, 7)]  # 6 scenes
    errors, _ = _validate_script(scenes, _valid_payload(), _config())
    assert any("Too many scenes" in e for e in errors)


def test_too_few_scenes_is_error():
    scenes = [_scene(1, "one"), _scene(2, "two"), _scene(3, "three")]  # 3 scenes
    errors, _ = _validate_script(scenes, _valid_payload(), _config())
    assert any("Too few scenes" in e for e in errors)


def test_hook_over_word_limit_is_error():
    scenes = [
        _scene(1, "This hook is way too long to ever pass the limit", hook_type="primary"),
        _scene(2, "Imagine grandma tells a story."),
        _scene(3, "You forget it slowly.", hook_type="proof"),
        _scene(4, "Will you remember?", hook_type="payoff"),
    ]
    errors, _ = _validate_script(scenes, _valid_payload(), _config())
    assert any("hook" in e.lower() for e in errors)


def test_missing_required_fields_is_error():
    scenes = [
        _scene(1, "Is AI erasing memory?", hook_type="primary"),
        _scene(2, "Imagine grandma tells a story."),
        _scene(3, "You forget it.", hook_type="proof"),
        _scene(4, "Will you remember?", hook_type="payoff"),
    ]
    errors, _ = _validate_script(scenes, {}, _config())  # empty payload
    assert any("comment_trigger" in e for e in errors)
    assert any("loop_type" in e for e in errors)
    assert any("psychology_hook" in e for e in errors)


def test_estimate_spoken_duration_counts_words():
    # 5 words at 2.5 words/sec == 2.0s
    assert estimate_spoken_duration("one two three four five") == 2.0
