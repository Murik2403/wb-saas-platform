## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

## Strix (AI pentesting)

Security scanning runs through [Strix](https://github.com/usestrix/strix); nine
skills for it live in `.agents/skills/` (symlinked into `.claude/skills/`).

- Local scan: `.\scripts\run_strix_scan.ps1` (source tree) or
  `.\scripts\run_strix_scan.ps1 -Live` (the app running on :5177 / :8001).
  Needs Docker running plus `STRIX_LLM` and `LLM_API_KEY` in `.env`.
- Pull requests: `.github/workflows/strix.yml` runs a quick scan when the
  matching repository secrets exist, and self-skips when they do not.
- Runs land in `strix_runs/` (gitignored — they hold working exploits and can
  contain real credentials). Browse one with `strix view`.
- Strix bills the LLM key per run. `--scan-mode deep` is the expensive one;
  keep it deliberate rather than habitual.
