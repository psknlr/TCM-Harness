"""服務端認證與授權（Protocol §14，P0-1/P0-3 修復）。

核心不變量：**Principal 只能來自服務端認證信息，不能來自請求體**。
請求體中的 role 只能請求**降級**（不能提權）；purpose 只能落在該
token 允許的目的集合內。

Token 記錄（HERMES_TCM_TOKENS_FILE 指向的 JSON，或 HERMES_TCM_TOKENS
內聯 JSON）：

    [{"token": "...", "subject": "user_1", "tenant_id": "tenant_a",
      "max_role": "researcher",
      "allowed_purposes": ["historical_research", "teaching"],
      "tool_scopes": ["catalog", "text", "citation"]}]

未配置任何 token 時進入**匿名開發模式**：單一 public 主體、僅
historical_research 目的、無法提權——僅供本機離線演示。生產部署
必須配置 token 並綁定認證主體/租戶/角色上限。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .principals import PURPOSES_OF_USE, ROLES, Principal

# 角色偏序（rank 越大權限越高）；提權即 rank 上升，一律拒絕
ROLE_RANK: Dict[str, int] = {r: i for i, r in enumerate(ROLES)}


class AuthError(Exception):
    """認證失敗（→ 401）。"""


class AuthzError(Exception):
    """已認證但無權（→ 403）。"""


@dataclass
class AuthenticatedPrincipal:
    """由 token 解析出的服務端身份（不可被請求體覆蓋）。"""
    subject: str
    tenant_id: str
    max_role: str
    allowed_purposes: Tuple[str, ...]
    tool_scopes: Tuple[str, ...] = ()       # 空=不限命名空間
    anonymous: bool = False

    def grantable(self, requested_role: str) -> bool:
        """requested_role 是否可授予（只能等於或低於 max_role）。"""
        if requested_role not in ROLE_RANK:
            return False
        return ROLE_RANK[requested_role] <= ROLE_RANK[self.max_role]

    def resolve(self, requested_role: str = "",
                requested_purpose: str = "") -> Principal:
        """→ 執行用 Principal。role 只降不升；purpose 必在允許集內。"""
        role = requested_role or self.max_role
        if not self.grantable(role):
            raise AuthzError(
                f"角色提權被拒：token 上限 {self.max_role}，"
                f"請求 {role}（只能降級，不能提權）")
        purpose = requested_purpose or (self.allowed_purposes[0]
                                        if self.allowed_purposes
                                        else "historical_research")
        if purpose not in self.allowed_purposes:
            raise AuthzError(
                f"目的越權：token 允許 {list(self.allowed_purposes)}，"
                f"請求 {purpose}")
        return Principal(subject=self.subject, role=role,
                         purpose_of_use=purpose, tenant_id=self.tenant_id,
                         attributes={"tool_scopes": list(self.tool_scopes),
                                     "max_role": self.max_role})


ANONYMOUS = AuthenticatedPrincipal(
    subject="anonymous", tenant_id="public", max_role="public",
    allowed_purposes=("historical_research", "teaching"),
    tool_scopes=(), anonymous=True)


def _validate_record(rec: Dict) -> Dict:
    for f in ("token", "subject", "max_role"):
        if not rec.get(f):
            raise ValueError(f"token 記錄缺少 {f}")
    if rec["max_role"] not in ROLES:
        raise ValueError(f"非法 max_role {rec['max_role']!r}")
    purposes = rec.get("allowed_purposes") or ["historical_research"]
    bad = [p for p in purposes if p not in PURPOSES_OF_USE]
    if bad:
        raise ValueError(f"非法 allowed_purposes {bad}")
    return rec


class AuthRegistry:
    """token → AuthenticatedPrincipal。token 以常數時間比對（防時序攻擊）。"""

    def __init__(self, records: Optional[List[Dict]] = None):
        self._by_token: Dict[str, AuthenticatedPrincipal] = {}
        for rec in (records or []):
            rec = _validate_record(dict(rec))
            self._by_token[rec["token"]] = AuthenticatedPrincipal(
                subject=rec["subject"],
                tenant_id=rec.get("tenant_id", "default"),
                max_role=rec["max_role"],
                allowed_purposes=tuple(rec.get("allowed_purposes")
                                       or ["historical_research"]),
                tool_scopes=tuple(rec.get("tool_scopes") or ()))

    @property
    def configured(self) -> bool:
        return bool(self._by_token)

    @classmethod
    def from_env(cls) -> "AuthRegistry":
        raw = os.environ.get("HERMES_TCM_TOKENS", "").strip()
        path = os.environ.get("HERMES_TCM_TOKENS_FILE", "").strip()
        if path:
            raw = Path(path).read_text(encoding="utf-8")
        if not raw:
            return cls([])
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"HERMES_TCM_TOKENS 非法 JSON：{exc}")
        return cls(data if isinstance(data, list) else [data])

    def authenticate(self, bearer: str) -> AuthenticatedPrincipal:
        """Bearer <token> → 認證主體。未配置 token = 匿名模式；配置後
        缺失/錯誤 token 拋 AuthError（401）。"""
        if not self.configured:
            return ANONYMOUS
        token = ""
        if bearer and bearer.startswith("Bearer "):
            token = bearer[len("Bearer "):].strip()
        if not token:
            raise AuthError("缺少 Bearer token")
        # 常數時間查找：逐條 hmac 比對，命中即返回（不因命中早晚洩露）
        matched: Optional[AuthenticatedPrincipal] = None
        for known, principal in self._by_token.items():
            if hmac.compare_digest(known, token):
                matched = principal
        if matched is None:
            raise AuthError("token 無效")
        return matched
