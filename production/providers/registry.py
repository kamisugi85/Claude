"""Maps provider ids used in production_job.json to VideoProvider instances,
and picks the first available one per shot per the job's provider_preferences
list. This is the only file that needs to change to add or remove a vendor.
"""
from __future__ import annotations

from pathlib import Path

from production.providers.base import ShotResult, VideoProvider
from production.providers.manual import ManualProvider
from production.providers.replicate_video import ReplicateVideoProvider
from production.providers.seedance import SeedanceProvider

_REGISTRY: dict[str, VideoProvider] = {
    "seedance": SeedanceProvider(),
    "replicate_video": ReplicateVideoProvider(),
    "manual": ManualProvider(),
}


def get_provider(provider_id: str) -> VideoProvider:
    try:
        return _REGISTRY[provider_id]
    except KeyError:
        raise ValueError(f"unknown provider id: {provider_id!r}; known: {list(_REGISTRY)}")


def generate_with_failover(shot, out_dir: Path, provider_preferences: list[str]) -> ShotResult:
    """Try each preferred provider in order; return the first success.
    Never raises for expected unavailability - only for programmer error
    (unknown provider id)."""
    last_result: ShotResult | None = None
    for provider_id in provider_preferences:
        provider = get_provider(provider_id)
        avail = provider.check_availability()
        if not avail.available:
            last_result = ShotResult(success=False, error=avail.reason, provider=provider_id)
            continue
        result = provider.generate_shot(shot, out_dir)
        if result.success:
            return result
        last_result = result
    return last_result or ShotResult(success=False, error="no providers configured", provider="none")
