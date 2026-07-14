"""專業子代理平面（Protocol §11）：按研究操作設定角色，不做「每本書
一個 Agent」。"""
from .specialists import (SPECIALIST_ROLES, SpecialistAgent,  # noqa: F401
                          dispatch_specialists, PARALLEL_SAFETY)
from .orchestrator import ResearchOrchestrator  # noqa: F401
from .verifier import IndependentVerifier  # noqa: F401
from .synthesizer import Synthesizer  # noqa: F401
