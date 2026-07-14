"""Domain Packs：可插拔領域插件（Protocol 總體結論）。

《傷寒論》從系統本體降為第一個高質量 Domain Pack；本草、方書、
醫案、內經、溫病、針灸等按同一接口接入。
"""
from .registry import (DomainPack, DOMAIN_PACKS,  # noqa: F401
                       get_domain_pack, list_domain_packs)
