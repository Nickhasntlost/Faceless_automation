from __future__ import annotations

import logging
import re
from typing import Callable

from src.models import Degradation, QualityReport, TimestampPlan
from src.utils.encoding import write_json

logger = logging.getLogger("shorts_pipeline.quality_gate")


def check_story_flow(full_script: str, timestamp_plan: TimestampPlan) -> list[str]:
    issues = []
    segments = timestamp_plan.segments
    normalized_script = re.sub(r'\s+', ' ', full_script).strip()
    reconstructed = re.sub(r'\s+', ' ', ' '.join(s.narration for s in segments)).strip()
    if reconstructed != normalized_script:
        issues.append('Timestamp segments alter, omit, reorder, or repeat narration')

    for position, segment in enumerate(segments):
        if not segment.narration.strip():
            issues.append(f'Segment {segment.index} has no narration')
        if not re.search(r'[.!?]["\']?$', segment.narration.strip()):
            issues.append(f'Segment {segment.index} breaks in the middle of an idea')
        if position and abs(segment.start - segments[position - 1].end) > 0.11:
            issues.append(f'Timestamps jump or overlap before segment {segment.index}')

    sentences = [
        re.sub(r'\W+', ' ', sentence).strip().lower()
        for sentence in re.split(r'[.!?]+', normalized_script)
        if len(sentence.split()) >= 5
    ]
    if any(sentences.count(sentence) > 1 for sentence in sentences):
        issues.append('The story repeats an idea verbatim')
    return issues


class QualityGate:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.degradations: list[Degradation] = []
        self.notes: list[str] = []
        self.cost_breakdown: dict[str, float] = {}
        self.fatal_error: str | None = None
        self.incomplete = False
        self.completed_stages: set[str] = set()
        self.engagement_scores: dict[str, float] = {}
        self.score_explanations: dict[str, str] = {}

    def record_cost(self, label: str, amount: float) -> None:
        self.cost_breakdown[label] = round(self.cost_breakdown.get(label, 0.0) + amount, 6)

    def validate_story_flow(self, full_script: str, timestamp_plan: TimestampPlan) -> bool:
        issues = check_story_flow(full_script, timestamp_plan)
        if not issues:
            self.mark_stage_complete('story_flow')
            self.note('Story flow preserved across timestamp segments')
            return True
        for issue in issues:
            self.degrade('story_flow', issue)
        self.set_fatal('Timestamp splitting damaged narrative continuity')
        return False

    def set_engagement_score(self, dimension: str, score: float, explanation: str = "") -> None:
        self.engagement_scores[dimension] = score
        if explanation:
            self.score_explanations[dimension] = explanation

    def note(self, message: str) -> None:
        self.notes.append(message)
        logger.info(message)

    def degrade(self, subsystem: str, reason: str) -> None:
        self.degradations.append(Degradation(subsystem=subsystem, reason=reason))
        logger.warning("[%s] %s", subsystem, reason)

    def mark_stage_complete(self, stage: str) -> None:
        self.completed_stages.add(stage)

    def set_fatal(self, error: str) -> None:
        self.fatal_error = error
        logger.error("Fatal: %s", error)

    def set_incomplete(self, reason: str) -> None:
        self.incomplete = True
        self.note(f"Run incomplete: {reason}")

    def finalize(self) -> QualityReport:
        if self.incomplete:
            verdict = "INCOMPLETE"
        elif self.fatal_error:
            verdict = "FAIL"
        elif self.degradations:
            verdict = "REVIEW"
        else:
            verdict = "PASS"

        report = QualityReport(
            verdict=verdict,
            run_id=self.run_id,
            degradations=list(self.degradations),
            cost_usd=sum(self.cost_breakdown.values()),
            cost_breakdown=dict(self.cost_breakdown),
            notes=list(self.notes),
            fatal_error=self.fatal_error,
            incomplete=self.incomplete,
            completed_stages=list(self.completed_stages),
            engagement_scores=dict(self.engagement_scores),
            score_explanations=dict(self.score_explanations),
        )
        return report

    def write_report(self, path) -> QualityReport:
        report = self.finalize()
        write_json(path, report.to_dict())
        logger.info("Quality gate verdict: %s", report.verdict)
        return report


def run_with_quality_gate(run_id: str, report_path, fn: Callable[[QualityGate], None]) -> QualityReport:
    gate = QualityGate(run_id)
    try:
        fn(gate)
    except KeyboardInterrupt:
        gate.set_incomplete("KeyboardInterrupt received")
        raise
    except Exception as exc:
        gate.set_fatal(str(exc))
    finally:
        return gate.write_report(report_path)
