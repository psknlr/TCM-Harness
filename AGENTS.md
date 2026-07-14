# AGENTS.md

@AGENT_CONSTITUTION.md

## 項目速覽（Level 2：Project Instructions）

- `hermes_tcm/`：全中醫古籍證據操作系統內核（身份鏈/證據平面/
  Claim Graph/typed DAG/命名空間工具/MCP 資源/評測）。
- `hermes_shanghan/`：第一個 Domain Pack（傷寒論），legacy API 全部
  保留；新代碼經 `hermes_tcm.tools.adapters` 與其互通。
- 測試：`python3 -m pytest tests/ -q`（全部離線，無需下載全庫；
  全庫測試用 `tests/test_library.make_fixture` 構造微型庫）。
- 集成入口：`hermes_shanghan/integrations/AGENTS.md`（工具調用細則）。
