# 外部審計回應（P0/P1 逐項裁定與修復記錄）

本文件記錄一次外部審計提出的 P0/P1 問題的**逐項裁定**：採納了什麼、
在哪些點上改進了原建議、什麼被如實暫緩。全部修復有回歸測試釘死
（`tests/test_audit_fixes.py`）。

## 總裁定：審計的核心事實成立

「已實現全部古籍智能體」的說法不成立——代碼自己標注只有 shanghan
Domain Pack 就緒（`hermes_tcm/domains/registry.py`），其餘七個領域為
planned。「能在全庫檢索某本書」≠「該書的專屬智能體已建立」。本輪
修復不改變這一誠實聲明，只修復**已就緒部分**的真實缺陷。

## P0 逐項

### P0-1 舊傷寒證據不進 V2 台賬 —— 採納，已修復

審計屬實：Broker 只識別 `passage_evidence`，而 shanghan 工具以
`evidence_excerpts` / `supporting_clauses` / `canonical_support` 攜帶
條文證據，導致「契約聲明 returns_primary_text、台賬 evidence_count=0」。

修復（統一證據適配層）：

* `hermes_tcm/domains/shanghan.py::normalize_evidence`：條文錨點統一
  按 id 掃描 → 回 clause store 取全文 → 構造期 verbatim/quote_hash
  互驗 → EvidenceRecord V2（verification_level=V2，身份鏈綁定
  傷寒論/宋本傳本）。正文不可得的 id 提及不入賬。
* Domain Pack 接口新增 `evidence_normalizer` 接縫
  （`domains/registry.py::normalize_domain_evidence` 統一分發）——
  即審計建議的 `DomainEvidenceAdapter`，但落位在 DomainPack 上：
  後續 jingui/neijing 包按同一接縫接入，內核不再逐領域散改 Broker。
* 契約守衛：`returns_primary_text` 工具成功調用後台賬證據為零
  → `evidence_contract_unfulfilled` guardrail 事件，不再靜默。

對原建議的改進：審計要求守衛**硬拒絕**（`evidence_contract_violation`
直接報錯）。未採納硬錯，理由：誠實的零命中（如無匹配醫案）是合法
成功結果，硬錯會把「如實沒有」變成「調用失敗」；fail-closed 語義由
下游承擔——主張核驗與發布閘門只認台賬，零證據結果不可能被引用。
守衛事件保證問題**可觀察**，閘門保證問題**不可放行**。

### P0-2 通用 Controller 不路由領域工具 —— 採納，已修復

審計屬實：「桂枝湯的核心方證是什麼」被歸 general_search，只走
`text.search_passages`，實測 paused + 零證據。

修復（Task Type × Domain 正交路由，`harness/router.py`）：

* 確定性實體鏈接（方名 117 seed + 別名詞表，最長匹配 + 重疊抑制，
  DomainPack `entity_linker` 接縫）；
* general_search 兜底前細分：formula_pattern / herb_profile /
  case_study（既有 `_TASK_RULES` 命中優先，測試釘死不被遮蔽）；
* 檢索策略 `domain_first_then_library`：`formula.resolve` 先取領域
  規則證據，`text.search_passages` 補全庫時間有序旁證；
* 只路由到 status=ready 的 Domain Pack——未就緒領域退回全庫檢索。

修復後同一問題：completed / release=pass / 台賬 13 條 V2 條文證據。

對原建議的裁剪：未新建 IntentRouter / EntityLinker / CapabilityPlanner
四個獨立類——當前規則量一個模塊足夠，接口（route() 的返回結構）與
審計建議的 JSON 形狀一致，膨脹到四個類屬過度設計。

### P0-3 兩套 Harness 並行 —— 採納，依賴倒置已落地（第二批）

審計對依賴方向的觀察屬實（hermes_tcm → hermes_shanghan 26 處，
反向 0 處）。第二批修復完成依賴倒置：

* **平台服務網關** `hermes_tcm/platform.py`：classics 全庫檢索內核、
  編目庫、字符歸一、朝代序、版本指紋等平台層能力的唯一接口——
  內核 18 個模塊的散落 import 全部收斂到網關函數；
* **DomainPack 完整接口** `hermes_tcm/domains/base.py`（審計建議的
  Protocol 全集：metadata / health / register_tools / detect_intent /
  extract_entities / build_plan / normalize_evidence / claim_policies /
  specialists / evaluation_suites），`ShanghanDomainPack` 是第一個
  標準實現；領域計劃（build_plan）成為 controller 領域任務的單一
  主源，pack.health() 接入 /readyz；
* **AST 級守衛** `tests/test_dependency_inversion.py`：內核觸達
  legacy 包的縫隙只允許 `platform.py` 與 `domains/shanghan.py` 兩個
  模塊（含 import_module 字符串形式），並反向鉗制 legacy 包零依賴
  新內核——依賴方向從此測試強制，不靠自覺。

對原建議的保留項：舊 CLI/Web 入口仍走 legacy HarnessRunner（其語義
被測試釘死），入口統一屬獨立遷移；但「內核依賴具體舊領域實現」的
方向問題已消除——內核只依賴接口與網關。

### P0-4 全庫未隨項目就緒 —— 事實確認，以誠實信號處理

屬實：`data/library/` 不隨倉庫分發，需 `library fetch`。這是設計
決策（69MB 語料不入 git）而非缺陷；缺陷在於就緒信號撒謊（見 P0-6）。
工具側已有 `corpus_unavailable` 誠實返回；本輪把部署探針也改為誠實。

### P0-5 超時不能真正停止工具 —— 採納（在標準庫約束內），已加固

審計屬實：`thread.join(timeout)` 之後線程繼續運行。CPython 線程
不可強制終止，這是實現約束不是選項。本輪落地：

* 滯留線程進程級登記 + 熔斷（MAX_ZOMBIE_THREADS，達上限拒絕新調用
  `circuit_open`——與 legacy ToolRegistry 同口徑，此前 V2 Broker 缺失）；
* 非只讀工具超時 → `timeout_side_effect_risk` guardrail 事件（副作用
  可能在超時後發生，重試前必須確認冪等性）；
* 超時結果不入台賬、不入緩存（原有管道保證，現有測試釘死）。

對原建議的裁剪：worker 進程池 / 可終止進程屬正確方向，但要求工具
函數可 pickle——與 legacy 綁定方法工具面直接衝突，且違背「純標準庫
+ 零部署複雜度」約束的收益比不成立。文檔如實聲明「超時=不再等待且
結果不採納，不等於工具已停止」，不冒充已有能力。

### P0-6 /readyz 假就緒 —— 採納，已修復

屬實且是最直接的運維風險。修復：核心依賴（語料/工具註冊/run 存儲）
任一缺失 → HTTP 503 + `ok:false` + `missing` 組件清單 + Domain Pack
狀態表；新增 `/livez` 存活探針（進程活着即 200，不觸數據）。
與 hermes_shanghan 服務端既有的 livez/readyz 分離口徑對齊。

## P1 逐項

### P1-1 語義/圖擴展檢索空實現 —— 採納，已實裝（第二批）

按審計建議的檢索棧方向落地，且**照單全收其核心約束**（「向量命中
只能作召回信號，不能直接作證據」）：

* `retrieval/semantic.py`：查詢形式擴展（原式+異體折疊+領域實體
  規範名）→ 字符 bigram OR 召回（近失段落進候選池）→ RRF 融合 +
  詞彙重排 → **逐字蘊含核驗閘**：verbatim 蘊含（1:1 折疊座標，
  可重驗）才構造 passage_evidence 入台賬；lexical_support 命中如實
  標注 `evidence_role=recall_signal`，不入台賬，引用它們過不了
  Claim Verifier。覆蓋記錄聲明 semantic 檢索模式。
* `retrieval/graph.py`：多跳鄰域擴召——條文 id 走條文關係圖 BFS，
  段落/文句走引文傳播網絡；輸出顯式 `recall_signal`，擴展節點正文
  必須經取證工具重新取得。
* 新 V2 工具 `text.search_semantic` / `graph.expand_neighborhood`；
  retrieval_fanout 增加確定性零命中回退（精確檢索零證據 → 一輪
  語義召回；仍零證據則負結論照常沿確定性路徑成立）。
* 誠實邊界：這是**確定性近似語義棧**（純標準庫、可重放），不是
  dense embedding；引入向量模型時沿用同一蘊含核驗閘。

RETRIEVAL_MODES 全部轉 ready，實現描述如實（不冒充向量庫）。

### P1-2 多智能體編排器不在主路徑 —— 採納，已接入

* `RunSpecV2.execution_mode`（single | council，非法值拒絕）；
* `TCMClient.research(query, execution_mode="council")` 與 HTTP
  `/api/tcm/research` 的 `execution_mode` 字段（非法值 fail-closed
  到 single）；
* council 結果經**同一** Release Gate（`evaluate_release`）裁定，
  run/證據/主張/工具調用/覆蓋全部落入**同一** RunStore；
* 誠實邊界：council run 的審批續跑（resume approve）尚未接入合議
  重跑——controller.resume 對 council run 如實拒絕重跑（防止 single
  DAG 覆蓋合議結果），信封 limitations 註明。deep_research 模式、
  模型異構專家等仍屬規劃層。

### P1-3 MCP 仍主要暴露舊系統 —— 採納，統一 V2 Server 已落地（第二批）

`hermes_tcm/integrations/mcp_server.py`（stdio JSON-RPC，純標準庫）
直接建立在 V2 主棧上，逐條回應審計問題：

* **工具面**：tools/list 暴露 V2 契約（含 annotations），tools/call
  經 TCMClient → CapabilityBroker 全管道（角色/目的/深度校驗/證據
  台賬），證據與 guardrail 事件隨結果返回；`tcm__research` 合成工具
  同步跑 typed DAG 全程。
* **版本協商**：支持列表新在前（2025-11-25 為首）；客戶端版本不被
  支持時回應**最新**支持版本——修復 legacy「未知版本回退最舊」。
* **durable tasks**：tasks/* 建立在 RunStore（SQLite）上——服務
  重啟後 status/result/cancel 仍可用（回歸測試模擬重啟驗證）；
  cancel 走 `request_cancel`，在節點邊界**真正停止** run（durable
  取消旗標），不是只丟棄結果。
* **資源**：tcm:// 統一資源面（policies/skills/runs/passages/…）。

legacy stdio server（shanghan:// + 舊 ToolRegistry）保留服務舊接入方
（其行為被測試釘死）；新接入一律指向統一 server：
`python3 -m hermes_tcm.integrations.mcp_server`。

### P1-4 領域註冊表重複 —— 採納（防漂移鉗制），合併暫緩

兩表服務不同入口（V2 主源 vs legacy 插件表），本輪不強行合併，但：

* `legacy_consistency_problems()`：交集領域狀態一致性檢查，狀態詞彙
  經顯式等價表（ready↔active），legacy `classics` 顯式豁免為平台層
  插件——測試釘死為空清單，漂移即紅；
* `unified_domain_view()`：兩表合併只讀視圖（單一可觀察面）。

### P1-5 參數驗證過淺 —— 採納，已修復

`_validate_args` 升級為遞歸 JSON Schema 子集校驗（純標準庫）：
嵌套 object（required / additionalProperties）、array items /
minItems / maxItems、minLength / maxLength / pattern、數值邊界，
且 boolean 不再被 integer/number 誤接收（`isinstance(True, int)`）。
未引入 Pydantic / jsonschema——「零第三方運行時依賴」是憲法約束
（AGENT_CONSTITUTION §五-18），審計此點建議與倉庫硬約束衝突，
以標準庫實現達到同等校驗深度。

## 驗證

* 第一批回歸：`tests/test_audit_fixes.py`（29 項）；
* 第二批回歸：`tests/test_audit_fixes2.py`（18 項：語義/圖檢索 +
  統一 MCP Server）+ `tests/test_dependency_inversion.py`（9 項：
  AST 級依賴方向守衛 + DomainPack 接口契約）；
* 全量：755 項測試通過（含 legacy 兼容面 36 工具、id 格式、
  文檔計數守衛）；
* 審計實測案例復現：`TCMClient().research("桂枝湯的核心方證是什麼？")`
  由 paused/零證據 → completed/pass/13 條 V2 台賬證據。
