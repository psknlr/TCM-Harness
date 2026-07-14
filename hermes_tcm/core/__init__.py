"""核心層：文獻身份、正交證據維度、主體/角色、目的限制策略。"""
from .schemas import (  # noqa: F401
    SOURCE_ROLES, WITNESS_ROLES, EPISTEMIC_STATUSES, VERIFICATION_LEVELS,
    CLAIM_RISKS, LEGACY_LAYER_MAP, legacy_layer_to_roles, roles_to_legacy_layer,
)
from .identity import (  # noqa: F401
    WorkRecord, WitnessRecord, DigitalItem, IdentityResolution,
    work_urn, witness_urn, edition_urn, item_urn, passage_urn, unit_urn,
    parse_urn,
)
from .principals import Principal, ROLES, PURPOSES_OF_USE  # noqa: F401
from .policies import purpose_allows, PURPOSE_POLICY  # noqa: F401
