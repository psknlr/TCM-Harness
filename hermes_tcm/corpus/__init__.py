"""語料基礎設施：身份鏈註冊表、生命週期、規範化、TEI/IIIF 導出。"""
from .registry import WorkRegistry  # noqa: F401
from .lifecycle import (INGEST_STAGES, CorpusManifestV2,  # noqa: F401
                        build_manifest_v2)
