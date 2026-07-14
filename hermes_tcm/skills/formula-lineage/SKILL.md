---
name: formula-lineage
description: >
  方劑異名、組成、劑量與源流譜系研究。僅在用戶詢問
  方劑源流、加減演化、劑量演變時使用。
task_types:
  - formula_lineage
---

# 方劑源流操作流程

1. formula.resolve：方名消歧 + 方證規則。
2. formula.trace_lineage：家族劑量演化邊 + 全庫時間有序載錄。
3. formula.compare_composition / formula.compare_dosage：組成與劑量對比。
4. citation.counter_search：更早同名/近名方的反證探針。
5. 譜系主張須時間有序證據（require_time_ordered）。

## 禁止事項

- 把「同名方」直接當「同一方」（組成不同的同名方須拆分陳述）。
- 劑量折算結論不帶學派假設聲明。
- 面向 patient_education 目的輸出任何劑量換算。
