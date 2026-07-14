"""命名空間工具面 + Capability Broker（Protocol §9，P0-7）。

    catalog.*   書目/身份解析
    text.*      段落檢索/閱讀
    collation.* 傳本對照/校勘
    citation.*  引文溯源/反證/轉引
    concept.*   術語/概念
    formula.*   方劑
    herb.*      藥物
    case.*      醫案
    evidence.*  證據包
    claim.*     主張編譯/核驗
    research.*  研究導出
    domain.*    領域插件投影（如 domain.shanghan.*）

不再向模型平鋪暴露全部工具：`ToolNamespaceRegistry.discover()` 支持
按命名空間/任務描述檢索工具（tool search），只把真正使用的工具定義
放入上下文。
"""
from .contracts import ToolContractV2  # noqa: F401
from .registry import ToolNamespaceRegistry, get_tcm_registry  # noqa: F401
from .broker import CapabilityBroker  # noqa: F401
from .adapters import LEGACY_TOOL_MAP, resolve_legacy_tool  # noqa: F401
