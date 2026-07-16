"""Unit tests for SSML generation and word-timing mapping (no network / no paid APIs)."""
from __future__ import annotations

from src.module2_voice import _build_ssml, _timings_from_marks, _tokenize_words


def test_build_ssml_is_valid_and_marks_every_word():
    text = "Imagine this. Your grandmother tells a story."
    ssml, clean_words = _build_ssml(text)

    assert ssml.startswith("<speak>")
    assert ssml.endswith("</speak>")
    # one <mark> per tokenized word
    expected_words = _tokenize_words(text)
    assert clean_words == expected_words
    assert ssml.count("<mark ") == len(expected_words)
    # marks are named sequentially w0..wN
    assert '<mark name="w0"/>' in ssml
    assert f'<mark name="w{len(expected_words) - 1}"/>' in ssml


def test_build_ssml_injects_breaks_from_pause_requests():
    text = "One two three."
    # request a dramatic pause after word index 0 ("One")
    ssml, _ = _build_ssml(text, pause_requests={0: "dramatic"})
    assert '<break time="450ms"/>' in ssml


def test_build_ssml_escapes_special_characters():
    ssml, _ = _build_ssml("Tom & Jerry")
    assert "&amp;" in ssml
    assert " & " not in ssml  # raw ampersand must be escaped


def test_timings_from_marks_orders_and_bounds():
    words = ["Imagine", "this", "story"]
    marks = [("w0", 0.0), ("w1", 0.5), ("w2", 1.0)]
    timings = _timings_from_marks(words, marks, audio_duration=1.5)

    assert [t.word for t in timings] == words
    # each word's end == next word's start (except last)
    assert timings[0].end_seconds == 0.5
    assert timings[1].end_seconds == 1.0
    # last word extends to (at least) audio duration
    assert timings[-1].end_seconds >= 1.0
    # monotonic non-decreasing starts
    starts = [t.start_seconds for t in timings]
    assert starts == sorted(starts)


def test_timings_from_marks_handles_missing_mark_gracefully():
    words = ["alpha", "beta"]
    marks = [("w0", 0.0)]  # w1 missing
    timings = _timings_from_marks(words, marks, audio_duration=2.0)
    assert len(timings) == 2
    assert timings[0].word == "alpha"
