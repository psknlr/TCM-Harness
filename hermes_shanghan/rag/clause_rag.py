"""ClassicalTextRAGAgent — retrieval over original clauses.

Supports the protocol's retrieval modes:
  * 條文號 (clause number / clause_id) direct lookup;
  * 方名 / 症狀 / 脈象 / 治法 / 禁忌 structured field filters;
  * BM25 lexical search (char n-grams) with structured-field boosting;
  * clause-relation graph expansion (related clauses appended).

Every hit returns the verbatim clause with book/chapter metadata so answers
can always cite their source (無條文編號，不成證據).
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from .. import config, lexicon
from ..schemas import ClauseRelation, ShanghanClause, read_jsonl
from ..textutil import normalize_query
from .bm25 import BM25Index

RE_CLAUSE_NUM_QUERY = re.compile(r"第?(\d{1,3})[條条]")


class ClauseRAG:
    def __init__(self, clauses: List[ShanghanClause],
                 relations: Optional[List[ClauseRelation]] = None):
        self.clauses = clauses
        self.by_id: Dict[str, ShanghanClause] = {c.clause_id: c for c in clauses}
        self.by_number: Dict[int, ShanghanClause] = {
            c.clause_number: c for c in clauses
            if c.text_type == "original_clause" and c.clause_number}
        self.relations = relations or []
        self._rel_by_src: Dict[str, List[ClauseRelation]] = {}
        for r in self.relations:
            self._rel_by_src.setdefault(r.source_clause_id, []).append(r)
            self._rel_by_src.setdefault(r.target_clause_id, []).append(r)
        self.index = BM25Index()
        for c in clauses:
            blocks = "\n".join(fb.raw_text for fb in c.formula_blocks)
            self.index.add(c.clause_id, c.clean_text + "\n" + blocks)
        self.index.finalize()

    @classmethod
    def load(cls) -> "ClauseRAG":
        clause_dicts = read_jsonl(config.CLAUSE_DIR / "clauses.jsonl")
        clauses = [ShanghanClause.from_dict(d) for d in clause_dicts]
        rel_dicts = read_jsonl(config.RELATION_DIR / "clause_relations.jsonl")
        relations = [ClauseRelation.from_dict(d) for d in rel_dicts]
        return cls(clauses, relations)

    # ------------------------------------------------------------------
    def get_clause(self, ref) -> Optional[ShanghanClause]:
        if isinstance(ref, int):
            return self.by_number.get(ref)
        ref = str(ref)
        if ref.isdigit():
            return self.by_number.get(int(ref))
        return self.by_id.get(ref)

    def related(self, clause_id: str, limit: int = 8) -> List[Dict]:
        out = []
        for r in self._rel_by_src.get(clause_id, [])[:limit * 2]:
            other = r.target_clause_id if r.source_clause_id == clause_id else r.source_clause_id
            out.append({"relation_type": r.relation_type, "clause_id": other,
                        "description": r.description, "confidence": r.confidence})
            if len(out) >= limit:
                break
        return out

    # ------------------------------------------------------------------
    def _query_entities(self, query: str):
        """Extract symptom/pulse terms from the query for coverage scoring."""
        if not hasattr(self, "_extractor"):
            from ..extract.entities import EntityExtractor
            self._extractor = EntityExtractor()
        found = self._extractor.extract(query)
        return found.symptoms, found.pulse

    def search(self, query: str, top_k: int = 8,
               six_channel: Optional[str] = None,
               formula: Optional[str] = None,
               field: Optional[str] = None,
               expand_relations: bool = False,
               min_score: float = 0.0) -> List[Dict]:
        query = normalize_query(query)

        # direct clause-number reference
        m = RE_CLAUSE_NUM_QUERY.search(query)
        if m:
            c = self.by_number.get(int(m.group(1)))
            if c:
                return [self._hit(c, 99.0, "clause_number")]

        # structured filter candidates
        def passes(c: ShanghanClause) -> bool:
            if six_channel and c.six_channel != six_channel:
                return False
            if formula:
                f = lexicon.canonical_formula(normalize_query(formula))
                if f not in c.formula_names:
                    return False
            if field:
                fields = {
                    "symptom": c.symptoms, "pulse": c.pulse,
                    "therapy": c.therapy_terms,
                    "contraindication": c.contraindication_terms,
                    "mistreatment": c.mistreatment_terms,
                    "formula": c.formula_names,
                    "disease": c.disease_patterns,
                }
                vals = fields.get(field, [])
                if not any(query.strip() in v or v in query for v in vals):
                    return False
            return True

        q_syms, q_pulse = self._query_entities(query)
        scored = self.index.search(query, top_k=top_k * 5)
        bm_max = scored[0][1] if scored else 1.0
        results = []
        for cid, bm in scored:
            c = self.by_id[cid]
            if not passes(c):
                continue
            # raw BM25 favours short auxiliary paragraphs; normalize it so the
            # structured signals below can actually reorder the pool
            score = 10.0 * bm / (bm_max or 1.0)
            if c.text_type != "original_clause":
                score *= 0.7            # auxiliary chapters rank below 正文
            fq = lexicon.canonical_formula(query)
            if fq in c.formula_names:
                score += 3.0
            # symptom/pulse *coverage* of the query (組合覆蓋，而非單點命中):
            # a clause matching all queried findings outranks one matching a
            # fragment, so 惡寒+發熱+無汗+身疼痛 lands on the 麻黃湯 clause,
            # not an auxiliary clause sharing two of the four terms
            if q_syms:
                matched = sum(1 for s in q_syms
                              if any(s == cs or s in cs or cs in s
                                     for cs in c.symptoms))
                cov = matched / len(q_syms)
                if cov == 1.0:
                    score += 3.0
                elif cov >= 0.6:
                    score += 1.5
                elif cov > 0:
                    score += 0.5
            if q_pulse:
                pm = sum(1 for p in q_pulse
                         if any(p in cp or cp in p for cp in c.pulse))
                score += 1.0 * pm / len(q_pulse)
            if c.formula_names:
                score += 0.5            # 方證條文優先於無方敘述
            results.append(self._hit(c, score, "bm25"))
        # score the whole candidate pool BEFORE cutting to top_k — a clause
        # ranked low by raw BM25 but with full finding coverage must survive
        results.sort(key=lambda h: (-h["score"], h["clause_id"]))
        if min_score > 0:
            results = [h for h in results if h["score"] >= min_score]
        results = results[:top_k]

        if expand_relations and results:
            seen = {h["clause_id"] for h in results}
            for h in list(results[:3]):
                for rel in self.related(h["clause_id"], limit=3):
                    rid = rel["clause_id"]
                    if rid in seen or rid not in self.by_id:
                        continue
                    seen.add(rid)
                    extra = self._hit(self.by_id[rid], 0.1, f"relation:{rel['relation_type']}")
                    results.append(extra)
        return results

    def _hit(self, c: ShanghanClause, score: float, source: str) -> Dict:
        return {
            "clause_id": c.clause_id,
            "clause_number": c.clause_number,
            "book": c.book_title,
            "chapter": c.chapter,
            "six_channel": c.six_channel,
            "text": c.clean_text,
            "text_type": c.text_type,
            "layer": c.layer,
            "layer_label": config.LAYER_LABEL.get(c.layer, ""),
            "formulas": c.formula_names,
            "score": round(score, 3),
            "match_source": source,
        }
