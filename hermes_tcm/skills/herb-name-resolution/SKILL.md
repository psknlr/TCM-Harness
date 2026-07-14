---
name: herb-name-resolution
description: >
  藥物名實解析與藥名歷史演變研究。僅在用戶詢問藥名異寫、
  藥名沿革、某藥歷代載錄時使用。
task_types:
  - general_search
---

# 藥名解析操作流程

1. concept.resolve_term：異體折疊形 + 全庫出現概況。
2. herb.trace_name：藥名時間有序載錄（術語級溯源）。
3. herb.resolve：用藥檔案（頻次/配伍/劑量寫法，domain=shanghan）。
4. herb.compare_properties：多藥用藥譜對比（如異名疑似同物時）。
5. 名實判定主張綁定段落證據（claim.compile → claim.verify）。

## 禁止事項

- 憑名稱相似直接斷定「甲即乙」（名實考證需段落級證據 + 人工裁決）。
- 輸出四氣五味/歸經等藥性結論（本草 Domain Pack 未就緒，不編造）。
- 面向 patient_education 目的輸出任何用藥建議。
