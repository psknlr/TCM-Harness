"""安全層：不可信語料封套、注入掃描、目的限制檢查。"""
from .untrusted import (UntrustedContent, wrap_untrusted,  # noqa: F401
                        scan_injection, INJECTION_PATTERNS)
