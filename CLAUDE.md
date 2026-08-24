# Claude Code guidance

Read and follow [AGENTS.md](AGENTS.md); it is the authoritative repository instruction file.

Before editing, query existing context in this order:

1. The repository's configured semantic-context and code-search MCP tools first.
2. The corresponding project CLIs second when MCP is unavailable.
3. Documented HTTP interfaces only as a fallback. Keep environment-specific endpoints,
   credentials and deployment wiring in private operator documentation.

For a live Neural Amplifier row, verify the intended brain through `/health` and require
`llm_decisions > 0` from `/coverage`; process liveness alone does not prove model play.
