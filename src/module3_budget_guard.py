from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from src.models import PricingConfig
from src.utils.api_client import BudgetExceededError
from src.utils.encoding import read_json, write_json

logger = logging.getLogger("shorts_pipeline.budget")


class BudgetGuard:
    def __init__(
        self,
        store_path: Path,
        threshold_usd: float,
        total_credit_usd: float,
        pricing: PricingConfig,
    ) -> None:
        self.store_path = store_path
        self.threshold_usd = threshold_usd
        self.total_credit_usd = total_credit_usd
        self.pricing = pricing
        self._ensure_store()

    def _ensure_store(self) -> None:
        if not self.store_path.exists():
            write_json(
                self.store_path,
                {
                    "cumulative_spend_usd": 0.0,
                    "entries": [],
                    "threshold_usd": self.threshold_usd,
                    "total_credit_usd": self.total_credit_usd,
                },
            )

    def _load(self) -> dict:
        return read_json(self.store_path)

    def _save(self, data: dict) -> None:
        write_json(self.store_path, data)

    @property
    def cumulative_spend_usd(self) -> float:
        return float(self._load()["cumulative_spend_usd"])

    def remaining_before_threshold(self) -> float:
        return max(0.0, self.threshold_usd - self.cumulative_spend_usd)

    def assert_can_spend(self, projected_usd: float, label: str) -> None:
        current = self.cumulative_spend_usd
        projected_total = current + projected_usd
        if projected_total > self.threshold_usd:
            message = (
                f"Budget guard blocked {label}: projected spend ${projected_total:.4f} "
                f"exceeds threshold ${self.threshold_usd:.2f} "
                f"(current ${current:.4f}, call ${projected_usd:.4f})."
            )
            logger.error(message)
            raise BudgetExceededError(message)
        if projected_total > self.total_credit_usd:
            message = (
                f"Budget guard blocked {label}: projected spend ${projected_total:.4f} "
                f"exceeds total credit ${self.total_credit_usd:.2f}."
            )
            logger.error(message)
            raise BudgetExceededError(message)

    def record_spend(self, amount_usd: float, label: str, metadata: dict | None = None) -> None:
        data = self._load()
        data["cumulative_spend_usd"] = round(float(data["cumulative_spend_usd"]) + amount_usd, 6)
        entry = {"amount_usd": round(amount_usd, 6), "label": label}
        if metadata:
            entry["metadata"] = metadata
        data["entries"].append(entry)
        self._save(data)
        logger.info(
            "Budget recorded %s: $%.4f (cumulative $%.4f / threshold $%.2f)",
            label,
            amount_usd,
            data["cumulative_spend_usd"],
            self.threshold_usd,
        )

    def guarded_call(
        self,
        label: str,
        projected_usd: float,
        func: Callable[[], float],
        metadata: dict | None = None,
    ):
        self.assert_can_spend(projected_usd, label)
        result = func()
        self.record_spend(projected_usd, label, metadata)
        return result

    def estimate_script_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            (input_tokens / 1_000_000) * self.pricing.script_input_usd_per_1m_tokens
            + (output_tokens / 1_000_000) * self.pricing.script_output_usd_per_1m_tokens
        )

    def estimate_tts_cost(self, character_count: int) -> float:
        return (character_count / 1_000_000) * self.pricing.tts_usd_per_1m_characters

    def estimate_elevenlabs_cost(self, character_count: int) -> float:
        # ElevenLabs standard rate is ~$0.30 per 1000 characters
        return (character_count / 1_000) * 0.30

    def estimate_veo_cost(self, duration_seconds: int, resolution: str) -> float:
        rate = self.pricing.veo_usd_per_second.get(resolution)
        if rate is None:
            rate = self.pricing.veo_usd_per_second["720p"]
        return duration_seconds * rate

    def estimate_thumbnail_cost(self) -> float:
        return self.pricing.thumbnail_usd_per_1k_image / 1000.0

    def cross_check_billing(self) -> str | None:
        try:
            from google.cloud import billing_v1
        except ImportError:
            return "google-cloud-billing not installed; skipped billing cross-check"
        try:
            client = billing_v1.CloudBillingClient()
            _ = client
            return "Billing API client initialized; manual dashboard cross-check still required"
        except Exception as exc:
            return f"Billing API cross-check unavailable: {exc}"
