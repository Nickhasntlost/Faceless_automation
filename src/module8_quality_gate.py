from __future__ import annotations

import logging
from typing import Callable

from src.models import Degradation, QualityReport
from src.utils.encoding import write_json

logger = logging.getLogger("shorts_pipeline.quality_gate")


class QualityGate:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.degradations: list[Degradation] = []
        self.notes: list[str] = []
        self.cost_breakdown: dict[str, float] = {}
        self.fatal_error: str | None = None
        self.incomplete = False
        self.completed_stages: set[str] = set()

    def record_cost(self, label: str, amount: float) -> None:
        self.cost_breakdown[label] = round(self.cost_breakdown.get(label, 0.0) + amount, 6)

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
