from dataclasses import dataclass, field
from typing import Optional

from .json_serialization import *
from .domain import *


class MatchAnalysisDTO(JsonDomainBase):
    name: str
    score: Annotated[HardSoftScore, ScoreSerializer]
    justification: Optional[str] = None


class ConstraintAnalysisDTO(JsonDomainBase):
    name: str
    weight: Annotated[HardSoftScore, ScoreSerializer]
    matches: list[MatchAnalysisDTO]
    score: Annotated[HardSoftScore, ScoreSerializer]
