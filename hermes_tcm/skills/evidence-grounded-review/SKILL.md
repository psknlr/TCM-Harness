---
name: evidence-grounded-review
description: >
  對一段研究陳述做逐主張證據核驗。僅在用戶要求核查、
  審讀、驗證已有結論時使用。
task_types:
  - broad_consensus
  - general_search
---

# 證據接地審讀操作流程

1. 把陳述拆為原子主張（claim.compile，每主張一個 claim_type）。
2. 每主張取證（text.search_passages / citation.trace_quote）。
3. 每主張反證（claim.find_counterevidence → 逐項執行）。
4. claim.verify：attribution / quotation / semantic_support / coverage 四項。
5. 產出逐主張裁定表：verified / needs_review / failed。

## 禁止事項

- 「普遍認為」少於 3 部不同著作、3 位不同作者、2 個不同時代。
- 對 failed 主張做任何形式的「批准為正確」。
- 綜合表達新增未經核驗的事實。
