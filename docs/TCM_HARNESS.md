# Hermes-TCM：全中醫古籍證據與研究操作系統

> Protocol v1.0 落地文檔。`hermes_shanghan`（傷寒論）降級為第一個
> 高質量 Domain Pack；`hermes_tcm` 是通用內核。

```text
Hermes-TCM =
  通用 Harness 內核            hermes_tcm/harness
+ 古籍語料與版本基礎設施       hermes_tcm/corpus + core/identity
+ 通用證據平面                 hermes_tcm/evidence
+ 結論與引用驗證平面           hermes_tcm/claims
+ 可發現的工具與 Skills        hermes_tcm/tools + skills
+ 按需生成的專業子代理         hermes_tcm/agents
+ 《傷寒論》等領域插件         hermes_tcm/domains（→ hermes_shanghan）
```

## 一、身份鏈（Protocol §5，P0-2）

`core/identity.py` + `corpus/registry.py`：

```text
Work（urn:tcm:work:<sha256-slug>）
└─ Witness（urn:tcm:witness:…，含 recension/source_type）
   └─ Edition / DigitalItem
      └─ TextUnit → Passage（psg_<12hex>，沿用 classics 穩定 id）
```

硬規則：書名相同≠同一著作；author/dynasty 衝突的同名單元**不自動
歸併**（拆分 + `needs_review`）；帶傳本後綴（宋本/明刊本）的單元刊刻
朝代不同是常態，不作同名異書信號；每次歸組輸出 `IdentityResolution`
（匹配依據/衝突字段/置信度/裁決標記）。不讓 LLM 判定同名異書。

三層文本（§5.2）：`corpus/normalization.py` 顯式建模
RAW—DIPLOMATIC—NORMALIZED 與 1:1 座標映射（fold_variants 恆等映射
構造期驗證，未來非 1:1 規則換區間映射表、契約不變）。

標準導出：TEI P5 apparatus（`corpus/tei.py`：app/lem/rdg + listWit）、
IIIF Presentation（`corpus/iiif.py`：Manifest/Canvas/Annotation；
無影像對齊時 locator 字段如實留空）、PROV-O（`evidence/provenance.py`）、
15 階段接入流水線與 Corpus Manifest V2（`corpus/lifecycle.py`）。

## 二、證據平面（Protocol §6，P0-1/3/4）

* **EvidenceRecord V2**（`evidence/records.py`）：完整身份鏈 + 三層
  文本 + locator + 五個正交維度（source_role / witness_role /
  epistemic_status / verification_level V0–V4 / claim_risk）+ 質量 +
  檢索上下文。構造期強不變量：V1+ 必須逐字可重驗；hash 失配拒絕。
  A/B/C/D/E/P 保留為兼容視圖（`core/schemas.py` 雙向映射）。
* **TypedEvidenceLedger**（`evidence/ledger.py`）：強類型台賬，寫入
  需要 Broker 鑄造令牌——模塊外直接 append 拋 `LedgerWriteViolation`。
  只有 `primary_text_returned` 記錄進入發布允許集。
* **SearchCoverage**（`evidence/coverage.py`）：每次檢索必須產生覆蓋
  記錄；負結論措辭由覆蓋狀態強制決定（§7.1 表格逐行落地）；
  覆蓋聲明自相矛盾（capped+exhaustive）構造期拒絕。
* **EvidencePacket**（`evidence/packets.py`）：可獨立重驗、可跨代理
  傳遞（專家隔離的載體）。

## 三、結論平面（Protocol §8，P0-5）

* **ClaimRecord**（`claims/records.py`）：10 種 claim_type × 5 種
  risk；draft → verified / needs_review / failed。
* **Conclusion Policy DSL**（`claims/policy_dsl.py`）：策略是數據
  （可 JSON 加載/導出/指紋），版本化 `conclusion-policy-2026.07.1`。
  首見需時間有序檢索+反證；「普遍認為」需 ≥3 著作/3 作者/2 時代；
  semantic_drift 禁止純頻次證據；臨床建議僅 clinician + 強制人工審核；
  負結論必須綁定覆蓋。
* **反證義務**（`claims/counterevidence.py`）：按 claim_type 生成
  義務清單（≥8 字引文截半探針；短術語走異體變形時間線）。
* **ClaimVerifier**（`claims/verifier.py`）：attribution / quotation /
  semantic_support / coverage 四項；台賬外證據=偽造→failed；
  每主張按 scope_id 綁定自己的覆蓋（反證覆蓋與主檢索覆蓋不混用）。

## 四、Typed Run Graph（Protocol §10，P0-6）

`harness/graph.py` 把 execute 拆為 15 個帶完整契約的節點：

```text
intake → task_classify → scope_contract → plan_compile
→ catalog_resolution → retrieval_fanout
→ identity_and_attribution_check → counterevidence_search
→ claim_compile → claim_verify → synthesis → citation_bind
→ safety_and_policy → human_review → release
```

* **RunSpecV2**（`harness/run_spec.py`）：principal + purpose_of_use +
  corpus_scope + completeness + counterevidence_policy + model_policy +
  五維預算 + 六件套環境指紋（語料/工具/策略/技能/代碼/模型）。
* **Durable execution**（`harness/checkpoint.py`）：SQLite WAL；
  runs（CAS 狀態版本）/node_attempts（幂等鍵）/events（事件溯源）/
  tool_calls/evidence/claims/coverage/approvals/leases（租約）。
* **審批類型學**（`harness/approvals.py`）：adjudication 可批；
  `citation_failure` 永不可批（補證據後重跑）。
* **發布閘門 V2**（`harness/release.py`）：五態 + claim 級 + 覆蓋級 +
  purpose 級裁定；強制限定語丟失檢測。
* **Replay**（`harness/replay.py`）：strict / evidence / policy 三模式。

## 五、工具面與 MCP（Protocol §9，P0-7/10）

* 13 個命名空間、46 個工具（catalog/text/collation/citation/concept/
  formula/herb/case/graph/evidence/claim/research/annotation），
  `ToolNamespaceRegistry.discover()` 按需取定義（不平鋪）。
* **ToolContractV2**：use_when / do_not_use_when / side_effect /
  approval / evidence_contract / failure_modes；非只讀工具必須聲明
  審批等級（構造期強制）。
* **CapabilityBroker**（`tools/broker.py`）：角色→目的→參數→審批→
  預算→緩存→超時→輸出契約→證據轉換登記→覆蓋登記→審計 十段管道；
  台賬唯一寫入口。
* **兼容適配**（`tools/adapters.py`）：`classics_trace_citation →
  citation.trace_quote` 等；legacy ToolRegistry 原樣保留（36 工具數、
  tool_specs.json、id 格式都是測試釘死的兼容面）。
* **MCP 資源**（`integrations/mcp.py`）：tcm://works|witnesses|
  passages|evidence|packets|runs|claims|policies|skills；服務器說明
  前 512 字符自包含。

## 六、子代理與 Skills（Protocol §11/§12）

* 9 個按研究操作設定的專家角色（不做「每本書一個 Agent」）；
  每專家接收**獨立** EvidencePacket，不讀彼此結論；匿名交叉審查；
  Independent Verifier 有最終權威；Synthesizer 不新增事實。
  並行安全表顯式聲明四類必須串行的操作。
* Skills（`skills/*/SKILL.md`）：YAML front-matter + 操作步驟；
  頂層只暴露名稱/描述，選中才加載全文（progressive disclosure）。
* 指令分層：`AGENT_CONSTITUTION.md`（Level 0 單一主源）←
  `CLAUDE.md` / `AGENTS.md` 導入。

## 七、安全（Protocol §14）

* 語料一律 DATA_ONLY / NON_EXECUTABLE / UNTRUSTED_CONTENT
  （`security/untrusted.py`；注入樣式掃描僅為審計信號）。
* 角色 × purpose_of_use 雙維授權：patient_education 禁止劑量換算/
  方劑推薦（Broker 調用層 + 發布層雙兜底）。
* 默認只讀；寫操作分級審批；刪語料/覆蓋原始文件 forbidden。
* 三類記憶分離（`memory.py`）：永久知識僅 V2+ 且綁定證據；
  模型生成內容寫入即拒。

## 八、評測（Protocol §16，P0-8/9）

* 六層評測（`evals/layers.py`）：語料身份/檢索/證據/Claim/軌跡/安全；
  依賴不可用時如實 skip；「沒測≠通過」。
* 五類金標準（`evals/goldset.py`）：首見/異文/轉引/同名異書/OCR 噪聲；
  分層因素 + Cohen's κ。
* 八項 P0 硬門檻（`evals/p0_gates.py`）：任何一項不過即不可發布。

## 九、服務、SDK 與保存層

* **HTTP 服務**（`server.py`）：/readyz、/api/tcm/tools（按需
  discover）、/api/tcm/resource（tcm://）、/api/tcm/research（返回
  AnswerEnvelope，禁止裸文本）、/api/tcm/tool、/api/tcm/resume。
  非法角色收斂 public、非法目的收斂 patient_education（fail-closed
  不提權）；Bearer 鑒權 + 256KB 體積上限。
* **Python SDK**（`integrations/sdk.py`）：TCMClient——research/
  call_tool/read_resource/resume 與 HTTP/MCP 同一語義（Phase 3
  退出條件）。規格導出（`integrations/specs.py`）：OpenAI/Anthropic/
  MCP 三格式同源 + 內容指紋。
* **OCFL 保存層**（`corpus/preservation.py`）：NAMASTE + inventory
  （sha256 manifest + sidecar）+ 版本目錄；內容尋址去重、RAW 永不
  覆蓋、fixity 重驗、路徑穿越拒絕——接入流水線 05 raw_object_freeze
  的存儲後端。
* **檢索平面**（`retrieval/`）：exact（就緒，委托三層內核）/
  lexical（就緒，確定性 bigram 重排）/ fusion（就緒，RRF）/
  semantic、graph（規劃層，顯式 not_implemented 不冒充）。
* **OTel 導出**（`harness/otel.py`）：run 事件溯源 → OTLP JSON
  resourceSpans（確定性 trace/span id，可重放）。**DLQ**
  （`checkpoint.dead_letters/requeue_node`）：重試耗盡節點的
  死信清單與 CAS 重投。
* **批注層**（`tools/annotation_tools.py`）：W3C Web Annotation
  Body—Target 私人批注；唯一寫工具（annotate + prompt 審批），
  批注目標必須是庫中真實段落。
* **種子金標準**（`evals/seed_goldset.py`）：五類 P0 樣本
  （首見/異文/轉引/同名異書/OCR 噪聲）針對 fixture 微型庫，
  CI 離線可評；真實全庫按同 schema 由標註閉環擴充。

## 十、數據源與安全加固（審查修復）

### 數據源：笈成全庫（jicheng）

hermes_tcm 的檢索數據源就是 jicheng 全庫——`config.LIBRARY_URL =
https://jicheng.tw/files/jcw/book-20180111.7z`（sha256 釘定）。
`library fetch` 下載→校驗→審查→原子切換到 `data/library/books/`；
全部 `catalog.*/text.*/citation.*` 工具經 `tools/_shared.searcher()`
讀取同一個 `config.LIBRARY_DIR`。run 記錄的 `corpus_version` 指向
**答案實際取自的庫版本**（`jicheng@<archive_sha256前12>`，
`corpus/fingerprint.py`），非伤寒論規則庫 manifest——證據回庫核驗與
replay 以此為準。`hermes-tcm corpus status|fetch` 查看/下載。

### P0/P1 加固（`tests/test_tcm_hardening.py`）

* **認證/授權**（`core/auth.py`）：Principal 只來自服務端 token
  （subject/tenant/max_role/allowed_purposes）；請求體 role 只能降級，
  提權/越目的一律 403；未配置 token = 匿名 public 開發模式。
* **審批不可偽造**（`harness/approvals.py`）：審核人角色/租戶 +
  action_digest + 有效期 + 單次使用逐項核驗；citation_failure 永不可批。
* **租戶隔離**（`checkpoint.py`）：runs 帶 owner_subject/tenant_id；
  跨租戶/非屬主訪問 403；資源解析器字段投影不回完整內部態。
* **ScopeContract**（`harness/scope.py`）：不可變範圍合同，Broker 為
  每次檢索注入約束 + 後置過濾越界命中 + 覆蓋回寫 scope_hash——聲明的
  scope 與實際入賬證據一致，不靠 Agent 記得填參數。
* **回源核驗**（`evidence/packets.py`）：區分 integrity_self_check（內部
  自洽）與 source_reverified（版本鎖定庫回源切片對照）；首見/轉引/臨床/
  方劑源流等高風險主張缺回源核驗即降級 needs_review。
* **節點執行上下文**（`broker.for_node`）：工具白名單 = 節點 tool_scope、
  節點預算、截止協作式取消。
* **超時熔斷**（`broker._run_with_timeout`）：只讀工具超時線程登記並在
  滯留過多時熔斷；寫工具同步執行不孤立。
* **前置分診**（`_n_intake`）：患者教育/public 的臨床請求在任何工具
  執行前攔截（0 工具消耗），不留到 release 才擋。
* **能力標籤**（ToolContract `capabilities`）：目的限制以契約聲明為據，
  不再靠工具名前綴猜測。

### 真正的 MCP Server（`integrations/mcp_server.py`）

JSON-RPC 2.0 over stdio：initialize（能力協商）/tools.list/tools.call
（經 Broker）/resources.list/resources.read/ping/cancel。啟動
`hermes-tcm serve-mcp --transport stdio`。此前 `integrations/mcp.py`
只是 MCP-compatible schemas + 本地 ResourceResolver。

### hermes-tcm CLI（`cli.py`）

`research / tool / discover / resource / corpus / serve / serve-mcp /
eval / replay`——新內核的第一等產品入口。

## 十一、成熟度邊界（如實標注）

* TEI/IIIF/OCFL 為 **prototype / compatible subset**：無 XSD 校驗、無
  真實影像 Image Service、無官方 validator——不宣稱 fully conformant。
* HTTP 服務為**開發/演示**服務器（全局鎖串行、無異步任務/事件流/限流）；
  生產層路線為 ASGI + PostgreSQL + worker pool + Object Storage + OTel。
* semantic/graph 檢索、Streamable-HTTP MCP 傳輸為**規劃層**，顯式
  not_implemented，不以確定性路徑降級混充。

## 十二、測試

`tests/test_tcm_*.py` 226 項（fixture 微型全庫含同名異書、多傳本、
跨朝代術語鏈、注入文本書；含 P0/P1 加固對抗回歸），全部離線確定性。
全倉 734 項。
