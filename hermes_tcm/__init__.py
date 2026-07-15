"""Hermes-TCM：全中醫古籍證據與研究操作系統（通用 Harness 內核）。

架構定位（Protocol v1.0）：

    Hermes-TCM =
      通用 Harness 內核
    + 古籍語料與版本基礎設施（Work/Witness/Edition/Item/Passage 身份鏈）
    + 通用證據平面（EvidenceRecord V2 / SearchCoverage / EvidencePacket）
    + 結論與引用驗證平面（Claim Graph / Conclusion Policy DSL）
    + 可發現的工具與 Skills（catalog.* / text.* / citation.* 命名空間）
    + 按需生成的專業子代理
    + 《傷寒論》等領域插件（hermes_shanghan 降級為第一個 Domain Pack）

`hermes_shanghan` 不被刪除：它是第一個高質量 Domain Pack，原有
`shanghan_*` / `classics_*` 工具經 `hermes_tcm.tools.adapters` 保持兼容。

核心不變量（沿襲並強化 hermes_shanghan 的基因）：

1. 模型輸出不能寫入 Evidence Ledger——唯一寫入口是 Capability Broker。
2. 引用必須來自本輪（或獲准繼承）的 Evidence Packet。
3. `passage_id` 存在但未返回正文，不能算證據。
4. 任何 citation failure 都不能被人工「批准為正確」。
5. 「沒有查到」必須以 SearchCoverage 限定表達，禁止裸負結論。
6. 語料文本一律視為不可信數據（DATA_ONLY / NON_EXECUTABLE）。
7. 失敗默認關閉（fail-closed）。
"""
from __future__ import annotations

# 版本源說明（消除漂移）：本倉庫是單一分發 `hermes-shanghan`
# （版本源 hermes_shanghan._version）。hermes_tcm 內核有自己的語義
# 版本號 __version__（隨內核演進），並在環境指紋中與分發版本並列記錄。
__version__ = "2.0.0a1"

try:
    from hermes_shanghan._version import __version__ as DISTRIBUTION_VERSION
except Exception:       # pragma: no cover
    DISTRIBUTION_VERSION = "unknown"

TCM_PROTOCOL_VERSION = "1.0"
