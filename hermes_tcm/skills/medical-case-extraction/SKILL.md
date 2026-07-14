---
name: medical-case-extraction
description: >
  醫案結構化抽取：呈現（症/脈）→治法→方劑的診療片段。
  僅在用戶要求分析醫案、比較醫案用方、抽取診療過程時使用。
task_types:
  - case_study
---

# 醫案抽取操作流程

1. case.search：按方劑/關鍵詞定位醫案。
2. case.extract_treatment_episode：結構化診療片段
   （presentation → treatment，錨定 A 層條文）。
3. case.compare_outcomes：跨方劑呈現譜對比（**非**療效比較）。
4. 每個片段主張綁定醫案來源與條文錨點。

## 禁止事項

- 把呈現譜對比表述為療效/結局比較（結局結構化屬規劃層）。
- 從單案推廣一般規律（案例證據 claim_risk 不低於 descriptive）。
- 面向 patient_education 目的輸出「照此案服藥」類內容。
