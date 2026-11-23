from __future__ import annotations

import inspect
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

from ...platform.tool_registry import digest
from .base import ExecutorContext, run_domain_executor
from .documents import (
    ArchiveImageExecutor,
    BatteryExecutor,
    ChartDataExecutor,
    ChronologyExecutor,
    CitationImpactExecutor,
    ClauseExecutor,
    CommitmentExecutor,
    EvidenceMatrixExecutor,
    MilestoneExecutor,
    MonthSeriesExecutor,
    ObservationExecutor,
    PeriodComparisonExecutor,
    ProjectPortfolioExecutor,
    ResearchReadingExecutor,
    RoadmapExecutor,
    SpeechExecutor,
)
from .geospatial import (
    AgricultureExecutor,
    BuildingExecutor,
    EnvironmentalSampleExecutor,
    FloodExecutor,
    HotspotExecutor,
    IceSeriesExecutor,
    IslandTimelineExecutor,
    LandCoverExecutor,
    PanelExecutor,
    RouteExecutor,
)
from .prepare import SourcePreparationExecutor
from .profile_batch import case_signatures
from .review import IndependentReviewExecutor

ANALYZERS = {
    "B01": ObservationExecutor,
    "B02": CommitmentExecutor,
    "B03": ArchiveImageExecutor,
    "B04": SpeechExecutor,
    "B05": MonthSeriesExecutor,
    "B06": ClauseExecutor,
    "B07": PeriodComparisonExecutor,
    "B08": EvidenceMatrixExecutor,
    "B09": ChronologyExecutor,
    "B10": EvidenceMatrixExecutor,
    "T01": ResearchReadingExecutor,
    "T02": MilestoneExecutor,
    "T03": RoadmapExecutor,
    "T04": ProjectPortfolioExecutor,
    "T05": ChartDataExecutor,
    "T06": ClauseExecutor,
    "T07": SpeechExecutor,
    "T08": EvidenceMatrixExecutor,
    "T09": BatteryExecutor,
    "T10": CitationImpactExecutor,
    "G01": HotspotExecutor,
    "G02": PanelExecutor,
    "G03": IceSeriesExecutor,
    "G04": FloodExecutor,
    "G05": BuildingExecutor,
    "G06": LandCoverExecutor,
    "G07": EnvironmentalSampleExecutor,
    "G08": IslandTimelineExecutor,
    "G09": RouteExecutor,
    "G10": AgricultureExecutor,
    "B11": ObservationExecutor,
    "B12": ArchiveImageExecutor,  # 专属分析函数已实现
    "B13": MonthSeriesExecutor,
    "B14": MonthSeriesExecutor,
    "B15": ObservationExecutor,
    "B16": ChronologyExecutor,
    "B17": EvidenceMatrixExecutor,
    "T11": MilestoneExecutor,
    "T12": ChartDataExecutor,  # 专属分析函数已实现
    "T13": MilestoneExecutor,
    "T14": MilestoneExecutor,
    "T15": ArchiveImageExecutor,  # 专属分析函数已实现
    "T16": ProjectPortfolioExecutor,  # 专属分析函数已实现
    "T17": ChronologyExecutor,
    "G11": PanelExecutor,  # 专属分析函数已实现
    "G12": PanelExecutor,  # 专属分析函数已实现
    "G13": PanelExecutor,  # 专属分析函数已实现
    "G14": PanelExecutor,  # 专属分析函数已实现
    "G15": IslandTimelineExecutor,  # 专属分析函数已实现
    "G16": PanelExecutor,  # 专属分析函数已实现
}
PREPARE = SourcePreparationExecutor
REVIEW = IndependentReviewExecutor


def executor_class(stage, code):
    if stage == "prepare":
        return PREPARE
    if stage == "review":
        return REVIEW
    if stage == "analyze" and code in ANALYZERS:
        return ANALYZERS[code]
    raise ValueError("case_executor_unavailable")


_batch_signatures = ContextVar("catalog_binding_signatures", default=None)


@contextmanager
def catalog_binding_batch():
    # Warm role cache hits and uninstalled catalogs never read provider files.
    token = _batch_signatures.set(lru_cache(maxsize=1)(case_signatures))
    try:
        yield
    finally:
        _batch_signatures.reset(token)


@lru_cache(maxsize=64)
def _profile_signature(stage, code):
    from ..team import frozen_profile

    batch = _batch_signatures.get()
    if batch is None and stage == "analyze":
        return frozen_profile(code)["signature"]
    signatures = batch() if batch is not None else case_signatures()
    return signatures[code] if stage == "analyze" else digest(signatures)


@lru_cache(maxsize=64)
def _implementation(stage, code):
    cls = executor_class(stage, code)
    sources = {}
    from .. import narrative_checks

    for source in {
        inspect.getsourcefile(cls),
        inspect.getsourcefile(ExecutorContext),
        narrative_checks.__file__,
    }:
        path = Path(source).resolve()
        sources[path.name] = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return digest(
        {
            "executor": cls.executor_id,
            "version": cls.version,
            "sources": sources,
            "case_profile": _profile_signature(stage, code),
        }
    )


def binding(stage, code=None):
    cls = executor_class(stage, code)
    profile_signature = _profile_signature(stage, code)
    return {
        "local": {
            "id": "casework.local.role_stage",
            "version": 1,
            "implementation": digest({"id": "casework.local.role_stage", "case_profile": profile_signature}),
        },
        "cloud": {
            "id": cls.executor_id,
            "version": cls.version,
            "implementation": _implementation(stage, code),
        },
    }


def validate_binding(agent):
    stage = agent.get("parameters", {}).get("case_stage")
    code = agent.get("parameters", {}).get("case_scope") or agent.get("role_contract", {}).get("case_id")
    if stage == "analyze" and not code:
        raise ValueError("case_executor_binding_invalid")
    expected = binding(stage, code)
    bindings = agent.get("executor_bindings")
    if not isinstance(bindings, dict) or set(bindings) != {"local", "cloud"}:
        raise ValueError("case_executor_binding_invalid")
    if bindings != expected:
        raise ValueError("case_executor_binding_changed")
    for mode, item in bindings.items():
        if not isinstance(item, dict) or not {"id", "version", "implementation"} <= set(item):
            raise ValueError("case_executor_binding_invalid")
        if mode == "cloud" and item["id"] != executor_class(stage, code).executor_id:
            raise ValueError("case_executor_binding_changed")
        if not isinstance(item["implementation"], str) or len(item["implementation"]) != 64:
            raise ValueError("case_executor_binding_invalid")


def cloud_executor(agent):
    validate_binding(agent)
    stage = agent["parameters"]["case_stage"]
    code = agent["parameters"].get("case_scope")
    cls = executor_class(stage, code)
    return cls(), agent["executor_bindings"]["cloud"]


async def execute_cloud(context: ExecutorContext):
    executor, binding = cloud_executor(context.agent)
    if executor.executor_id != binding["id"]:
        raise ValueError("case_executor_binding_changed")
    return await run_domain_executor(executor, context, binding)
