---
name: commentary-dispute
description: >
  注家爭議結構化：同一條文/問題下各注家觀點的對齊與分歧呈現。
  僅在用戶詢問注家分歧、諸家爭論、某條文各家解釋時使用。
task_types:
  - broad_consensus
---

# 注家爭議操作流程

1. domain.shanghan.divergence：注家分歧圖譜（爭點條文榜/一致度矩陣）。
2. text.search_passages（category=注釋 過濾）：全庫注釋層取證。
3. 每位注家觀點單獨成 claim（source_role=commentary，
   epistemic_status=source_assertion）。
4. cross_review 語義：呈現分歧結構，**不裁決**誰對誰錯。
5. 「多數注家認為」須過 broad_consensus 策略（≥3 著作/3 作者/2 時代）。

## 禁止事項

- 系統替古人裁決學術爭議（結構化呈現，裁決屬人工）。
- 把注文內容當條文原文引用（C 層不冒充 A 層）。
- 用單一注本概括「歷代注家」。
