---
name: compare-witnesses
description: >
  同一著作不同傳本的原文對照與異文清單。
  僅在用戶詢問傳本差異、版本比較、校勘時使用。
task_types:
  - witness_comparison
---

# 傳本校勘操作流程

1. catalog.resolve_work：解析著作身份；needs_human_adjudication=true 時停下請用戶指定。
2. catalog.list_witnesses：列出全部傳本（區分古代傳本與現代整理本 source_type）。
3. collation.align_witnesses：探針詞對照 + 兩兩相似度。
4. collation.list_variants：異文成對清單。
5. 需要標準格式時 collation.export_tei_apparatus（TEI P5 app/lem/rdg）。
6. 每條異文主張綁定兩側 passage 證據。

## 禁止事項

- 同名異書自動歸併（author/dynasty 衝突必須人工裁決）。
- 把現代點校本的文字當作古代傳本異文。
- lemma（底本讀法）由工具探針序決定時，發布前須聲明底本選擇未經專家審批。
