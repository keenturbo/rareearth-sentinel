from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class FamilyDefinition:
    family: str
    target_chain: str
    enabled: bool
    reviewer: str
    runtime_tier: str
    description: str
    baseline_name: str
    allowed_config_keys: list[str] = field(default_factory=list)
    metrics_profile: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DatasetDefinition:
    dataset_id: str
    family: str
    enabled: bool
    source_kind: str
    source_path: str
    description: str
    owner: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExperimentDefinition:
    experiment_id: str
    family: str
    target_chain: str
    candidate_name: str
    baseline_name: str
    dataset_id: str
    config_patch: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    reviewer: str = "admin"
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    experiment_id: str
    family: str
    baseline_run_id: str = ""
    status: str = "pending"
    decision: dict[str, Any] = field(default_factory=dict)
    baseline_summary: dict[str, Any] = field(default_factory=dict)
    candidate_summary: dict[str, Any] = field(default_factory=dict)
    case_results: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)
    errors: list[Any] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeedbackLinkRecord:
    feedback_id: str
    experiment_id: str
    run_id: str = ""
    root_cause: str = ""
    fix_type: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
