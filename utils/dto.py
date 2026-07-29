from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
import numpy as np


@dataclass
class Detection:
    class_name: str
    confidence: float
    bbox: List[float]  # [x1, y1, x2, y2] normalized 0-1


@dataclass
class InspectionResult:
    fig_id: int
    session_id: int
    batch_id: str
    decision: str           # "Healthy" | "Aflatoxin"
    confidence: float
    detections: List[Detection]
    timestamp: datetime = field(default_factory=datetime.now)
    image_path: Optional[str] = None
    latency_ms: float = 0.0


@dataclass
class SessionStats:
    total: int = 0
    healthy: int = 0
    aflatoxin: int = 0

    @property
    def ratio(self) -> float:
        if self.total == 0:
            return 0.0
        return round(self.aflatoxin / self.total * 100, 1)
