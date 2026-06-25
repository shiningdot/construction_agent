"""HFACS-CS 技能模块。"""

from construction_agent.versions.v3.skills.base import HFACSSkill
from construction_agent.versions.v3.skills.hfacs_levels import (
    OrganizationalSkill,     # Level 1
    SupervisionSkill,        # Level 2
    PreconditionSkill,       # Level 3
    UnsafeActsSkill,         # Level 4
    SiteConditionSkill,      # Level 5
    PhysicalEnvSkill,        # Level 6
)

ALL_SKILLS = [
    OrganizationalSkill,
    SupervisionSkill,
    PreconditionSkill,
    UnsafeActsSkill,
    SiteConditionSkill,
    PhysicalEnvSkill,
]

__all__ = [
    "HFACSSkill", "ALL_SKILLS",
    "OrganizationalSkill", "SupervisionSkill", "PreconditionSkill",
    "UnsafeActsSkill", "SiteConditionSkill", "PhysicalEnvSkill",
]
