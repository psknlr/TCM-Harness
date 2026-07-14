"""檢索平面（Protocol §17 corpus & retrieval plane）。

    exact     精確/布爾/異體折疊檢索（就緒：委托 classics 三層內核）
    lexical   詞彙級重排（就緒：確定性 token 重疊評分）
    fusion    多路結果融合（就緒：Reciprocal Rank Fusion）
    semantic  語義召回（規劃層——不冒充）
    graph     圖擴展召回（規劃層——不冒充）

必須避免的錯誤之一：「把全部古籍放進一個向量庫就宣布完成」。本平面
的就緒層全部是確定性、可解釋、可重放的；未就緒層顯式返回
not_implemented 而不是降級混充。
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
    "semantic": {"status": "planned",
                 "impl": "向量/蘊含召回——規劃層，不冒充"},
    "graph": {"status": "planned",
              "impl": "引文邊/關係圖擴展召回——規劃層，不冒充"},
}
