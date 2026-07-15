"""檢索平面（Protocol §17 corpus & retrieval plane）。

    exact     精確/布爾/異體折疊檢索（就緒：委托 classics 三層內核）
    lexical   詞彙級重排（就緒：確定性 token 重疊評分）
    fusion    多路結果融合（就緒：Reciprocal Rank Fusion）
    semantic  近似語義召回（就緒：形式擴展 + bigram OR 召回 + RRF +
              逐字蘊含核驗；非向量庫，確定性可重放）
    graph     圖擴展召回（就緒：條文關係圖 BFS + 引文網絡鄰域）

必須避免的錯誤之一：「把全部古籍放進一個向量庫就宣布完成」。本平面
全部是確定性、可解釋、可重放的；semantic/graph 的召回命中只是信號
——只有通過逐字蘊含核驗的片段才是證據（passage_evidence），
lexical_support / 圖擴展節點如實標注 recall_signal，不入台賬。
"""
from .exact import search_exact  # noqa: F401
from .lexical import rerank_lexical  # noqa: F401
from .fusion import fuse_rrf  # noqa: F401
from .semantic import search_semantic  # noqa: F401
from .graph import expand_graph  # noqa: F401

RETRIEVAL_MODES = {
    "exact": {"status": "ready", "impl": "classics L0/L1/L2 三層檢索"},
    "lexical": {"status": "ready", "impl": "確定性 token 重疊重排"},
    "fusion": {"status": "ready", "impl": "Reciprocal Rank Fusion"},
    "semantic": {"status": "ready",
                 "impl": "形式擴展 + bigram OR 召回 + RRF 融合 + 逐字"
                         "蘊含核驗（召回信號≠證據；非向量庫，確定性）"},
    "graph": {"status": "ready",
              "impl": "條文關係圖 BFS + 引文網絡鄰域擴召"
                      "（召回信號≠證據）"},
}
