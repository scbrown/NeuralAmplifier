# na-xmd — does the model change the answer on byte-identical prompts?

**State: run once, 2026-10-15 (aegis-5xi).** Two models: `claude-haiku-4-5` (na-htm's
committed answers) and `claude-opus-5-5` (this run).

## Why it exists

Every measured eval and game before this one was answered by Haiku 4.5, or by the claude-code
lane, which recorded `model: claude-code` and never said which model ran. "The previous models
hit a wall" could not be tested: no eval had put two models in front of the same input.

## The design

- **Input fixed by construction.** The eight na-htm arms (four real decisions, an all-facts arm
  and a ranked arm each) are re-scored from na-htm's committed `task.txt` files. Each one is
  pinned by full sha256 in `manifest.json`. `check` and `score` both refuse if the bytes move.
- **No prompts of its own.** Regenerating would compare models on prompts the earlier model
  never saw. na-htm's prompts already predate the current `_SYSTEM`, and
  `just eval check na-htm` names that drift. It does not matter here, because both models
  answered the same bytes.
- **Same n and same parse.** n = 10 per arm per model, one fresh process per answer. The exact
  invocation is recorded under `models` in the manifest. Opus ran with no tools, no MCP and no
  host settings, so the host's own context could not condition the answer.

## Result

```text
python evals/cross_model.py evals/runs/na-xmd
```

| pool | Haiku 4.5 | Opus 5.5 | Fisher p |
| --- | --- | --- | --- |
| modal stability, tech + social engineering | 22/40 | 37/40 | 0.0002 |
| modal stability, production | 37/40 | 32/40 | 0.19 |
| legal choice, all arms | 80/80 | 80/80 | — |
| invented citations | 0 | 0 | — |

Opus 5.5 holds its choice where Haiku split most. It is not better on the 48-option production
decision, where it divided between two units that Haiku rarely separated. It also cites
differently. It cited none of the tech facts, which carry only names, and the prompt asks that a
fact which made no difference not be cited. It cited 2.6–3.7 facts per run on social
engineering, where the facts discriminate. Nobody judged whether those citations were relevant.

**Steadier is not stronger.** On tech, Opus settled on `tech:48` where Haiku leaned to
`tech:39`. Which pick is better needs an outcome, and this eval measures none. Spend: $0.81 for
80 Opus answers at list price.

## Adding a model

Answer each pinned `evals/runs/na-htm/<arm>.task.txt` ten times with the new model. Write the
replies to `na-xmd/<model>/<arm>.answers.jsonl` and add the model to `manifest.json`. Then run
`just eval check na-xmd` and `just eval score na-xmd`.
