from __future__ import annotations

from pathlib import Path

from src.models import ChannelIdentity, PipelineConfig, PricingConfig
from src.utils.encoding import read_json


DEFAULT_CHANNEL_IDENTITY = ChannelIdentity(
    niche="AI and emerging technology",
    persona="Sharp, curious tech explainer for a general audience",
    tone="Confident, punchy, slightly provocative but factual",
    audience="Tech-curious viewers aged 18-35",
    content_rules=[
        "Hook in the first 2 seconds",
        "One clear insight per Short",
        "End with a loop-friendly line that connects back to the hook",
    ],
    banned_topics=[],
    default_hashtags=["#AI", "#Tech", "#Shorts"],
)


def load_pipeline_config(root: Path) -> PipelineConfig:
    data = read_json(root / "config" / "pipeline_config.json")
    return PipelineConfig(**data)


def load_pricing_config(root: Path) -> PricingConfig:
    data = read_json(root / "config" / "pricing_verified.json")
    script = data["models"]["script_model"]
    veo = data["models"]["veo_model"]
    thumb = data["models"]["thumbnail_model"]
    tts = data["models"]["tts_studio"]
    return PricingConfig(
        verified_at=data["verified_at"],
        script_model_id=script["id"],
        script_input_usd_per_1m_tokens=script["input_usd_per_1m_tokens"],
        script_output_usd_per_1m_tokens=script["output_usd_per_1m_tokens"],
        veo_model_id=veo["id"],
        veo_usd_per_second=veo["usd_per_second"],
        thumbnail_model_id=thumb["id"],
        thumbnail_usd_per_1k_image=thumb["usd_per_1k_image"],
        tts_usd_per_1m_characters=tts["usd_per_1m_characters"],
    )


def load_channel_identity(root: Path) -> ChannelIdentity:
    path = root / "config" / "channel_identity.json"
    if not path.exists():
        return DEFAULT_CHANNEL_IDENTITY
    data = read_json(path)
    return ChannelIdentity(
        niche=data.get("niche", DEFAULT_CHANNEL_IDENTITY.niche),
        persona=data.get("persona", DEFAULT_CHANNEL_IDENTITY.persona),
        tone=data.get("tone", DEFAULT_CHANNEL_IDENTITY.tone),
        audience=data.get("audience", DEFAULT_CHANNEL_IDENTITY.audience),
        content_rules=data.get("content_rules", DEFAULT_CHANNEL_IDENTITY.content_rules),
        banned_topics=data.get("banned_topics", DEFAULT_CHANNEL_IDENTITY.banned_topics),
        default_hashtags=data.get("default_hashtags", DEFAULT_CHANNEL_IDENTITY.default_hashtags),
    )
