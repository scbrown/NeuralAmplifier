# Claude Code guidance

Read and follow [AGENTS.md](AGENTS.md); it is the authoritative repository instruction file.

Before editing, query existing context in this order:

1. Quipu and Bobbin MCP tools first (`quipu_search`/`quipu_query`/`quipu_ask`, then Bobbin
   `search`/`grep`/`find_refs`). Use `quipu_episode` for structured graph writes.
2. Bobbin CLI second when MCP is unavailable. The `quipu` CLI is local-store-only today; do not
   treat it as a query of the deployed graph.
3. The HTTP interfaces documented in AGENTS.md only as fallback, with file/stdin payloads and
   the provisioned bearer for writes and `/shapes`.

For a live Neural Amplifier row, verify the intended brain through `/health` and require
`llm_decisions > 0` from `/coverage`; process liveness alone does not prove model play.
