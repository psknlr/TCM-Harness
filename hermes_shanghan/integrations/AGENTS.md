# Hermes-Shanghanlun — Agent Integration Guide

This project exposes a grounded, citation-checked toolset over the *Shanghan
Lun* (《傷寒論》). Any agent harness (Claude Code, Codex CLI, OpenCode/openclaw,
or a custom function-calling loop) can drive it. Every tool result carries
`clause_id` references; every agent answer is verified against clause text
before it is returned.

## Capability surface (19 read-only tools + 1 agent tool)

| Tool | Purpose |
|---|---|
| `shanghan_search` | BM25 + structured + relation-expanded clause retrieval |
| `shanghan_get_clause` | clause N / clause_id →原文 + 實體 + 規則 + 關係 |
| `shanghan_match_formula` | symptoms/pulse → candidate 方證 (assistive) |
| `shanghan_differential` | 2–3 formulas → multi-axis contrast table |
| `shanghan_six_channel` | channel → 提綱/亞型/主方/欲解時/禁忌 |
| `shanghan_formula_rule` | formula → 核心證/組成/加減/禁忌 + clauses |
| `shanghan_mistreatment` | 誤治→變證→救治方 paths |
| `shanghan_list_formulas` | enumerate formulas in the rule base |
| `shanghan_divergence_atlas` | 9-commentator alignment coverage / divergence / agreement |
| `shanghan_dose` | 銖-equivalent dose ratios, 3-school conversions, family evolution |
| `shanghan_corpus_stats` | whole-base quantitative statistics |
| `shanghan_eval_metrics` | cloze / case-replay / grounding benchmark metrics |
| `shanghan_variants` | B-layer version variants for a clause |
| `shanghan_relations` | clause relation-graph traversal (multi-hop) |
| `shanghan_therapy` | therapy-method rules (汗吐下和溫補…禁例) |
| `shanghan_contraindication_check` | formula + presentation → conflicts / bans |
| `shanghan_dose_convert` | deterministic 漢制 dose calculator |
| `shanghan_case_search` | 經方實驗錄 case records + canonical anchors |
| `shanghan_library` | full jicheng.tw library (800+ books): catalog / full-text / read |
| `shanghan_ask` (agent) | full agent: auto-retrieve, cite, safety-govern |

The full library behind `shanghan_library` is fetched on demand
(`python3 -m hermes_shanghan library fetch`, 69MB, sha256-pinned) and is a
literature side-evidence layer — excerpts carry 書·章節 locators but never
enter the canonical evidence gates.

## 1. Claude Code (MCP)

```bash
# register the MCP server (stdio)
claude mcp add shanghan -- python3 -m hermes_shanghan serve-mcp
# then in Claude Code, the shanghan_* tools are available to the model
```

Or run a skill: the compiled skills under `data/skills/shanghanlun/` are
plain `SKILL.md` + `rules.jsonl` and can be loaded directly.

## 2. Codex CLI / OpenCode / openclaw (OpenAI-compatible tools)

```bash
# emit a portable spec bundle (OpenAI + Anthropic tool formats)
python3 -m hermes_shanghan export-tools --out tools.json
# call a tool directly (harness dispatch target)
python3 -m hermes_shanghan tool-call shanghan_search --args '{"query":"往來寒熱 胸脅苦滿"}'
```

In a Python function-calling loop:

```python
from hermes_shanghan.integrations import openai_tool_specs, dispatch
tools = openai_tool_specs()                 # pass as tools=[...]
result = dispatch("shanghan_differential", {"formulas": ["桂枝湯", "麻黃湯"]})
```

## 3. Any LiteLLM-backed agent

```python
from hermes_shanghan.agent import ShanghanAgent
print(ShanghanAgent().ask("少陰病寒化與熱化怎麼區分？", role="student")["answer"])
```

Configure the model via env: `HERMES_LLM_MODEL=anthropic/claude-opus-4-8`,
`ANTHROPIC_API_KEY=…` (or any LiteLLM provider). With no key/litellm, the
agent runs the deterministic `local` backend — still fully grounded.

## Guarantees (identical across harnesses)

- **Evidence leash**: answers cite `clause_id`; the citation guard flags any
  clause id or quote it cannot verify against the corpus.
- **Layer labels**: A 原文 / B 異文 / C 注釋 / D 後世歸納 / E 模型推理.
- **Patient safety**: diagnosis / prescription / dosage requests are refused
  upstream; dosage text is redacted; formula recommendations are stripped.
