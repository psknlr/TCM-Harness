# 首見結論策略參考（conclusion-policy）

本技能綁定的結論策略是 `tcm://policies/earliest_attestation`
（`hermes_tcm.claims.policy_dsl.DEFAULT_POLICIES` 的版本化條目）：

| 條款 | 要求 |
|---|---|
| minimum_tools | citation.trace_quote 或 citation.trace_term；且 citation.counter_search 或 citation.trace_term（短術語的反證由異體變形時間線承擔） |
| minimum_evidence | ≥1 部著作、V2 級核驗（身份鏈完整） |
| coverage | 時間有序（dynasty_ordered）+ 反證已執行 + 無更早部分匹配候選 + **exhaustive_within_scope**（抽樣/封頂覆蓋不支持首見） |
| output | 強制限定語「在當前語料庫範圍內」 |
| human_review_when | earlier_partial_candidate / low_ocr_confidence / uncertain_work_date |

## 表述換算表（與 SearchCoverage 狀態對應）

| 覆蓋狀態 | 允許的負結論表述 | 首見結論 |
|---|---|---|
| 全範圍、未封頂、版本清楚 | 在本次定義的語料範圍內未見 | 允許（帶限定語） |
| 掃描封頂 | 在已掃描部分未見 | 禁止 |
| 僅抽樣 | 抽樣結果未見 | 禁止 |
| OCR 質量不足 | 自動檢索未見，尚需影像人工核查 | 禁止 |
| 存在更早部分匹配 | —— | 禁止（人工核驗） |

策略版本與指紋見 `tcm://policies/current`。
