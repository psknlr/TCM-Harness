---
name: terminology-genealogy
description: >
  術語跨朝代載錄譜系與分佈研究。僅在用戶詢問術語演變、
  沿革、譜系、概念漂移時使用。
task_types:
  - term_genealogy
---

# 術語譜系操作流程

1. concept.resolve_term：異體折疊 + 出現概況。
2. citation.trace_term：術語+異名合併時間線。
3. concept.drift：按朝代分桶的頻次分佈。
4. text.read_context：關鍵載錄的前後文核驗（語義判讀證據）。
5. claim.compile（semantic_drift 類主張需段落級證據，頻次分佈不足）。

## 禁止事項

- 用詞頻變化直接推斷概念或病機發生了語義變化（必須避免的錯誤之四）。
- 忽略 scan_capped：分佈是「已掃描部分」的分佈。
