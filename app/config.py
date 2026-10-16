"""Settings, read once from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


def available_cpus() -> int:
    """CPUs this process may use, honoring container CPU limits (cgroup v2 cpu.max)."""
    count = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count() or 1
    try:
        quota, period = open("/sys/fs/cgroup/cpu.max").read().split()
        if quota != "max":
            count = min(count, max(1, round(int(quota) / int(period))))
    except (OSError, ValueError):
        pass
    return max(1, count)


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # Local OCR keeps trying harder clean-ups until every check passes or this budget is spent.
    ocr_budget_s: float = 3.0
    ocr_workers: int = 2

    # Optional AI vision reader. Off unless an API key is configured, because many
    # agency networks block outbound calls; the app must stand on local OCR alone.
    #   "fallback": consulted only when local OCR cannot confirm every check (default with a key)
    #   "always":   runs alongside OCR on every label
    #   "off":      never
    vision_mode: str = "off"
    vision_model: str = "claude-opus-5-5"
    vision_effort: str = "low"
    vision_timeout_s: float = 4.5  # how long we wait for it before answering without it (~5 s target)
    vision_max_concurrency: int = 8

    max_images: int = 6  # front, back, neck... per application
    max_upload_mb: int = 15

    @property
    def vision_enabled(self) -> bool:
        return self.vision_mode != "off"

    @classmethod
    def from_env(cls) -> Settings:
        has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
        mode = os.environ.get("VISION", "auto").lower()
        if mode == "auto":
            mode = "fallback" if has_key else "off"
        if mode not in ("fallback", "always"):
            mode = "off"
        return cls(
            ocr_budget_s=_float("OCR_BUDGET_SECONDS", cls.ocr_budget_s),
            ocr_workers=int(_float("OCR_WORKERS", available_cpus())),
            vision_mode=mode,
            vision_model=os.environ.get("VISION_MODEL", cls.vision_model),
            vision_effort=os.environ.get("VISION_EFFORT", cls.vision_effort),
            vision_timeout_s=_float("VISION_TIMEOUT_SECONDS", cls.vision_timeout_s),
            max_upload_mb=int(_float("MAX_UPLOAD_MB", cls.max_upload_mb)),
        )
