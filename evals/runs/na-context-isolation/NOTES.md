# Claude Code context isolation control

One paired decision, not a strategy or performance evaluation. Claude Code 2.1.300,
model `claude-opus-5-5`, source base `e67cea795c564b434f70d6ccb4095e738394d8be`.
The fixture is `orchestrator/tests/fixtures/thinker_base_production.json`.
`baseline.json` and `isolated.json` pin identical system and payload bytes by SHA256.
Both answers parse and select the legal action `a2`.

Total input is **input + cache creation + cache read**, not just the input field.
Baseline: 26,380; isolated: 4,417 (83.3% lower). This does not establish a 2,400-token
prompt-only bound, absence of all provider overhead, or a fleet savings rate.
The integrated Orchestrator control uses its ordinary serialized WorldView and validates
orders through the full decision loop: `tier=llm`, `degraded=false`, 4,580 total input.
Its serialization is distinct from the raw paired fixture; do not pool the samples.

The isolated invocation retains the exact NA `--system-prompt`, with `--safe-mode`,
`--setting-sources ""`, `--tools ""`, `--strict-mcp-config`, an empty `--mcp-config`,
`--disable-slash-commands`, and `--no-session-persistence`, in a new empty directory.
Authentication and managed policy remain in force. No credential file is copied and
no permission bypass is used. This relies on the documented CLI contract, not an
independent dump of hidden context. The API lane is unchanged.

[Official CLI reference](https://code.claude.com/docs/en/cli-reference) documents safe mode
and explicit system prompts. Use the recorded CLI version when reproducing; flags and
provider overhead may change. A new call costs model usage and is deliberately opt-in.
