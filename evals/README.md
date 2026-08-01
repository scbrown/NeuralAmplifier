# Evals — behavioural questions about the brain

`just test` asserts values. These ask what a decision *does* over many runs, which is not a
value: "is the grounding read", "is the standing plan followed", "does the choice hold still".
The answer is a distribution over a model's outputs, so it cannot be an assertion — it has to be
measured, and re-measured when the prompt or the world view changes.

```bash
just eval list              # what exists, what each asked, what it found
just eval score na-373      # re-derive a finding from the committed run
just eval prompts na-373    # regenerate the inputs (needs the Thinker rulebook)
```

**`score` needs nothing.** No model, no game, no sibling checkout, no tokens — the prompts and
the answers are both committed, so any number quoted in the docs can be recomputed on a fresh
clone by whoever doubts it. `prompts` is the only command that needs `THINKER_DIR`.

## The evals

| id | Asks | Found |
| --- | --- | --- |
| `na-373` | Can we offer fewer grounding facts by ranking them? | **No.** Truncation changed the decision, and the ranking rule predicts citation worse than chance. |
| `na-61c2` | Does recent build history stop a base flip-flopping? | **Yes**, 0.30 → 1.00 continued — and still 0.10 when the case actually changed, so it is not an anchor. |

Write-ups: [quipu-integration.md](../docs/quipu-integration.md) and
[decision-inputs.md](../docs/decision-inputs.md) §2.

## How one is built

Three steps, and the middle one is deliberately not the harness's business.

1. **`prompts`** builds one real world view per arm and writes the exact bytes the brain would
   send, plus a manifest hashing each prompt.
2. **Something answers them.** A paid API run, or agents each given one `<arm>.task.txt`. Both
   runs here were answered by Haiku 4.5, one independent agent per decision — independence
   matters, since answering several in one context makes the later ones conditional on the
   earlier and destroys the distribution being measured.
3. **`score`** reads `<arm>.answers.jsonl` — one `{"choice": ..., "cited": [...]}` per line.

The manifest records `prompt_sha256` per arm. Answers sitting beside a prompt that has since
changed are not evidence about the current prompt, and without the hash nobody can tell that
they aren't.

## Two rules, both learned the hard way

**An arm that cannot fail measures nothing.** `na-61c2` runs on a *contested* decision and puts
the brain's **least**-favoured option into the history, because history that merely reinforced an
already-unanimous choice would have looked like a success no matter what it did.

**A number needs the arm that falsifies it.** `na-61c2`'s third arm exists because 1.00
continuation is equally consistent with "correctly continued" and "follows history regardless" —
and the second of those is worse than the flip-flopping it was meant to cure. Likewise `na-373`
never reports utilisation without the choice distribution beside it: utilisation is
cited ÷ offered, so it rises whenever you offer less, and optimising it directly rewards
starving the decision.

## Adding one

Add a module with `arms(links) -> {name: WorldView}` and `score(out, links)`, register it in
`EVALS` in `run.py` with its question *and its answer*, and commit the run under `runs/<id>/`.
Shared world views and prompt/answer plumbing are in `harness.py`.

Register the answer in `EVALS`, not only in a document. An eval whose result you have to go and
find is one nobody rereads before repeating the work.
