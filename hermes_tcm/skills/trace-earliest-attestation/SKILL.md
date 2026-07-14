---
name: trace-earliest-attestation
description: >
  查找術語或引文在定義中醫古籍語料範圍內的最早載錄。
  僅在用戶詢問首見、最早、首載、源出時使用。
  不用於一般術語解釋。
task_types:
  - earliest_attestation
  - term_genealogy
---

# 首見研究操作流程

1. Resolve the term and variants（concept.resolve_term：異體折疊形 + 已知異名）。
2. Define corpus scope（scope contract：分類/朝代/排除項 + 語料版本凍結）。
3. Run exact chronological search（citation.trace_quote / citation.trace_term，order=dynasty）。
4. Run variant and partial counter-search（citation.counter_search：截半探針）。
5. Inspect uncertain earlier candidates（部分匹配候選逐條人工核驗標記）。
6. Build SearchCoverage（掃描範圍/封頂/缺口如實記錄）。
7. Never say historical first occurrence（只說「在當前語料庫範圍內最早見於」）。
8. Bind every claim to EvidenceRecord IDs（claim.compile → claim.verify）。
9. Require review when earlier partial candidates exist（earlier_partial_candidate 觸發人工審核）。

## 禁止事項

- 存在更早部分匹配候選時發布「首見」結論。
- 用「未查到」代替「在本次定義的語料範圍內未見」。
- 把無朝代著作排進時間序（UNRANKED 不參與首現裁定）。
