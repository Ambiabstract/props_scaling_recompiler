"""Discover, plan, generate, validate, and commit orchestration."""

from .discovery import (
    InspectedMap,
    MapDiscovery,
    PipelineDiagnostic,
    VmfEntityRequest,
    discover_vmf_requests,
    inspect_map_sources,
)
from .planning import (
    ColoredSkinRequirement,
    GeneratedModelRequirement,
    MapUsagePlan,
    OperationPlan,
    WHITE,
    build_operation_plan,
)
from .materials import (
    ColoredMaterialOperationPlan,
    ColoredMaterialPlan,
    ColoredSkinMaterialPlan,
    MaterialInspection,
    build_colored_material_plan,
    inspect_colored_material_sources,
)
from .skin_layout import (
    EntitySkinAssignment,
    ModelSkinLayoutPlan,
    SkinLayoutOperationPlan,
    build_skin_layout_plan,
    commit_skin_layout_plan,
    source_skin_families_fingerprint,
)
from .qc import (
    QCOperationPlan,
    ReferenceQCArtifactPlan,
    ScaledQCArtifactPlan,
    build_qc_operation_plan,
)
from .staging import (
    StagedFile,
    StagedSourceModel,
    StagingError,
    StagingWorkspace,
    stage_qc_operation,
    stage_source_model,
)
from .generation import (
    GenerationError,
    GenerationResult,
    ValidatedMaterialArtifact,
    ValidatedModelArtifact,
    generate_and_validate,
)

__all__ = [
    "ColoredSkinRequirement",
    "ColoredMaterialOperationPlan",
    "ColoredMaterialPlan",
    "ColoredSkinMaterialPlan",
    "EntitySkinAssignment",
    "GeneratedModelRequirement",
    "GenerationError",
    "GenerationResult",
    "InspectedMap",
    "MapDiscovery",
    "MapUsagePlan",
    "MaterialInspection",
    "ModelSkinLayoutPlan",
    "OperationPlan",
    "PipelineDiagnostic",
    "QCOperationPlan",
    "ReferenceQCArtifactPlan",
    "ScaledQCArtifactPlan",
    "SkinLayoutOperationPlan",
    "StagedFile",
    "StagedSourceModel",
    "StagingError",
    "StagingWorkspace",
    "VmfEntityRequest",
    "ValidatedMaterialArtifact",
    "ValidatedModelArtifact",
    "WHITE",
    "build_operation_plan",
    "build_qc_operation_plan",
    "build_colored_material_plan",
    "build_skin_layout_plan",
    "commit_skin_layout_plan",
    "discover_vmf_requests",
    "inspect_colored_material_sources",
    "inspect_map_sources",
    "generate_and_validate",
    "source_skin_families_fingerprint",
    "stage_qc_operation",
    "stage_source_model",
]
