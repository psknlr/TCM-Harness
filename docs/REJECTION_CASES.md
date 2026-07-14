# 拒絕機制實證（為什麼 `rejected: 0`，以及閘門真的會開火嗎）

`stats` 顯示當前規則庫 `rejected: 0`，而 README 強調「證據回源失敗的規則
直接進入 rejected/」。二者不矛盾，但值得用數據說清楚——本文檔給出
**流水線審計實測** 與 **對抗性輸入的真實閘門輸出**（非手寫示例，全部
可用文末命令復現）。

## 一、生產流水線的審計實況（7,569 條審計記錄）

| 階段 · 結果 | 條數 | 說明 |
|---|---|---|
| schema · pass | 1,501 | 全部初始規則過模式校驗 |
| evidence · pass | 1,491 | 逐字回源通過 |
| **evidence · fail** | **10** | 回源失敗——閘門確實開火 |
| **repair · repaired** | **32** | AutoRepair 修復（刪虛構條件/降級處方強度） |
| reverify · pass | 32 | 修復後復檢全部通過 |
| semantic · warn | 66 | 語義閘警告 |
| critic · warn | 22 | 批評家警告 |
| release gold / silver / bronze | 579 / 861 / 61 | 分級放行 |
| release rejected | 0 | 見下 |

`rejected: 0` 的機制原因：**規則由確定性抽取器產生，不會憑空編造**——
evidence_span 直接取自條文本身，所以「回源失敗」只在跨條誤切等邊界情況
出現（上表 10 例），且全部屬可修復類，經 AutoRepair 修復並復檢通過。
拒絕閘門的存在意義是防禦 **LLM 增益層**（`pipeline --llm-extract`）與
未來不可信來源——對這類輸入它會立即開火，見下節實測。

## 二、對抗性輸入的真實閘門輸出

以下五個案例把蓄意破壞的規則餵給 `ReviewPipeline`（與
`tests/test_review.py` 同構），輸出逐字取自實際運行結果：

### A. 偽造證據——後世術語冒充原文（協議紅線）

輸入 `evidence_span`：「太陽中風，**營衛不和**，桂枝湯**調和營衛**主之。」
（第 12 條原文並無此句；營衛不和是後世歸納）

```
release_level: rejected   evidence_verified: False
```

**硬拒絕**。逐字回源失敗即終止，正是「無原文，不成規則」。

### B. 條文編號不存在（幻覺出處）

輸入 `clause_id: SHL_SONGBEN_9999`

```
release_level: rejected   evidence_verified: False
critic: fail   flags: ['critic:no_clause']
```

### C. 錯方歸屬——第 12 條標「大承氣湯」

```
release_level: bronze   evidence_verified: True
repairs: ['repair:dropped_formula:大承氣湯', 'repair:strength_downgraded:可與']
final formula: []        ← 虛構的方被剝除，不作為結論存活
```

證據span本身是真的（取自第 12 條），所以走**修復**而非拒絕：結論中的
大承氣湯被刪除、處方強度降級，殘餘規則降至 bronze。

### D. 虛構症狀——「潮熱、譫語」混入第 12 條

```
release_level: gold（修復後）   evidence_verified: True
repairs: ['repair:dropped_condition:symptoms:潮熱',
          'repair:dropped_condition:symptoms:譫語']
final symptoms: []       ← 虛構症狀全部剝除
```

### E. 「可與」誇大為「主之」——第 15 條處方強度膨脹

（第 15 條原文只說「**可與**桂枝湯」）

```
release_level: silver   evidence_verified: True
repairs: ['repair:strength_downgraded:可與']
final strength: 可與     ← 誇大被強制回調
```

## 三、設計取捨：為何不在 `data/shanghan/rejected/` 放演示樣例

評審建議提交 `rejected_rules_demo.jsonl`。我們選擇**文檔而非數據文件**，
理由：`data/shanghan/` 是流水線的字節級可復現產物，混入手工演示數據會
(1) 破壞「重跑逐字節一致」保證，(2) 讓下游把演示樣例誤當真實拒絕記錄。
拒絕機制的可信度由三處共同保證：本文檔的實測輸出、
`tests/test_review.py` 的 9 項對抗測試（每次 CI 都注入偽造證據並斷言
被拒），以及審計目錄裡真實的 10 例 evidence·fail 記錄。

## 四、復現命令

```bash
python3 -m unittest tests.test_review -v     # 對抗測試（注入→斷言拒絕/修復）
python3 - << 'EOF'                            # 本文檔案例逐字復現
import sys; sys.path.insert(0, "tests")
from test_review import make_rule
from hermes_shanghan.corpus import segmenter
from hermes_shanghan.extract.entities import EntityExtractor, annotate_clause
from hermes_shanghan.review.pipeline import ReviewPipeline
clauses = segmenter.segment_canonical()
ex = EntityExtractor(segmenter.harvest_formula_names(clauses))
for c in clauses: annotate_clause(c, ex)
pipe = ReviewPipeline({c.clause_id: c for c in clauses})
r = pipe.review_rule(make_rule(
    evidence_span="太陽中風，營衛不和，桂枝湯調和營衛主之。"))
print(r.autonomous_review.release_level)      # → rejected
EOF
```
