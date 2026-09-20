"""Pure ERP target contracts. No refresh, permission lookup or external writes."""

from .contracts import FrozenTarget, TargetAccess, TargetEvidence, TargetSelectionError
from .freeze import freeze_target

__all__ = ["FrozenTarget", "TargetAccess", "TargetEvidence", "TargetSelectionError", "freeze_target"]
