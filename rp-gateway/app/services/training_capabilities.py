"""Authoritative activation policy for optional training interaction surfaces."""

from __future__ import annotations

from typing import Any

from app.models.schemas import WorldPackSummary
from app.services.training_artifacts import TrainingArtifactService
from app.services.training_workspace import TrainingWorkspaceService


class TrainingCapabilityPolicy:
    @classmethod
    def support(cls, worldpack: WorldPackSummary) -> dict[str, bool]:
        return {
            "interactive_links_supported": TrainingArtifactService.supports(worldpack),
            "interactive_workspace_supported": TrainingWorkspaceService.supports(worldpack),
        }

    @classmethod
    def validate(
        cls,
        *,
        scenario_type: str,
        worldpack: WorldPackSummary,
        interactive_links_enabled: Any,
        interactive_workspace_enabled: Any,
    ) -> dict[str, bool]:
        links = bool(interactive_links_enabled)
        workspace = bool(interactive_workspace_enabled)
        support = cls.support(worldpack)
        if scenario_type != "training" and (links or workspace):
            raise ValueError("interactive links and workspace are available only for training scenarios")
        if links and not support["interactive_links_supported"]:
            raise ValueError("selected WorldPack does not support interactive links")
        if workspace and not support["interactive_workspace_supported"]:
            raise ValueError("selected WorldPack does not support interactive workspace")
        if workspace and not TrainingWorkspaceService.supports_anonymous_showroom(worldpack):
            raise ValueError("selected WorldPack contains workspace resources unavailable to anonymous Showroom visitors")
        return {
            "interactive_links_enabled": links if scenario_type == "training" else False,
            "interactive_workspace_enabled": workspace if scenario_type == "training" else False,
            **support,
        }
