"""Detector construction and the demo-mode decision.

Kept out of :mod:`app.domain.detector` so that class stays purely about running a model. The
policy question — what to do when no model loads — belongs here.
"""

from __future__ import annotations

from app.config import Settings
from app.core.logging import get_logger
from app.domain.candidates import CandidateParams
from app.domain.demo import DemoDetector
from app.domain.detector import Detector, DetectorProtocol, ModelUnavailableError

log = get_logger(__name__)


def build_detector(settings: Settings) -> DetectorProtocol | None:
    """Load the configured model.

    Returns ``None`` when no model is available but demo mode is permitted, signalling that
    each connection should build its own :class:`DemoDetector` — demo state is per-connection
    because its RNG is per-connection.

    Raises :class:`ModelUnavailableError` when no model is available and demo mode is not
    permitted. The desktop app degraded silently here (inference_engine.py:102); a server
    that quietly starts returning simulated aflatoxin results is worse than one that refuses
    to boot.
    """
    try:
        detector = Detector(
            model_path=settings.model.path,
            input_size=settings.model.input_size,
            candidate_params=CandidateParams.from_settings(settings.vision),
        )
    except ModelUnavailableError:
        if not settings.model.allow_demo:
            log.error("model_unavailable", path=settings.model.path, allow_demo=False)
            raise
        log.warning("demo_mode_enabled", path=settings.model.path)
        return None

    log.info("detector_ready", backend=detector.backend)
    return detector


def pipeline_detector(shared: DetectorProtocol | None, seed: int = 42) -> DetectorProtocol:
    """Pick the detector for one connection: the shared model, or a fresh demo generator."""
    return shared if shared is not None else DemoDetector(seed=seed)
