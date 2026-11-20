# Convention Gap: first diagnostic

**Reproduce:** `python3 -m hanabi.arena --games 32 --seed 0 --convention-gap`. Add `--json` to include the complete per-pairing and per-seat report in machine-readable form. The original arena remains unchanged when the option is off.

For each **actual play**, use the actor's pre-action observation to count all hands consistent with its literal clues, including exclusions from hints that didn't touch a slot. Start with the deck multiplicities minus the visible partner hand, discards (including failed plays), and cards already on the fireworks. Assign physical copies to slots without replacement; an identity with two available copies contributes twice as many assignments as one with just one (subject to the other slots' constraints). The literal risk is the fraction of assignments in which the selected slot can't be played. Only the environment's revealed success or failure supplies the outcome, **after** the estimate. This follows [Fukushima et al., *The Convention Gap*, §3.2](https://arxiv.org/html/2609.11489v4#S3.SS2).

The gap is mean literal risk minus actual failure rate, pooled over plays and reported in percentage points. It ignores why the sender chose a hint, or why any other move was made. Plays at zero literal risk cannot miss and pull the gap toward zero. The report therefore also includes play rate among turns, the share of plays at zero literal risk, and the gap among plays at nonzero risk (not defined when that subset is empty). The gap is a descriptive calibration check, **not** information in bits, proof of a convention, a causal effect, a measure of partner quality, or a reward to maximize. A zero gap can be pinned at zero or mask opposing residuals. The random policy is a stress case, not a mathematical null: even its list of legal clues can depend on hidden cards.

## Seed 0 development suite

[Full report with policy and seat breakdowns](convention-gap-v1.json). Same 32 deals and 512 games as the [starting baseline](baseline-v1.json); **not a new held-out evaluation**. Different policies play both seat assignments. Team scores and ending rates match the unaudited baseline exactly. Rows shown below are descriptive; later policy comparisons should use fresh deals and cluster uncertainty by paired deal, not by turn.

| Team | Games | Score /25 | Plays | Literal risk | Miss rate | Gap (pp) | Zero-risk share |
|---|---:|---:|---:|---:|---:|---:|---:|
| Lantern + Lantern | 32 | 16.41 | 540 | 2.06% | 2.78% | −0.72 | 95.74% |
| Lantern + Literal | 64 | 13.56 | 877 | 1.12% | 1.03% | +0.10 | 97.83% |
| Lantern + Spark | 64 | 16.66 | 1,106 | 2.79% | 3.62% | −0.82 | 90.60% |
| Literal + Literal | 32 | 11.91 | 381 | 0.00% | 0.00% | 0.00 | 100.00% |
| Spark + Spark | 32 | 16.91 | 562 | 3.08% | 3.74% | −0.66 | 87.90% |

Lantern self-play was literally safe on 517 of 540 plays. Its −0.72-point overall gap is not evidence of a shared convention. On the 23 plays where there was literal risk, its gap was −16.96 points; that's a small, selected sample and includes its endgame gambles. Literal self-play provides a clean example of a pinned zero.

## One actual play: “it's a 1” doesn't mean “it plays”

On deal 4 of that suite (deck seed `1770899062782801945`, Spark in seat 0, Lantern in seat 1), Red and White already had a 1 on their stacks. On turn 8 Lantern could see that Spark's rightmost card was Yellow 1. It gave the number **1** clue, which marked only that slot; a **Yellow** clue would have marked both Yellow 1 and Yellow 2. Literally, Spark now knew the rightmost card was the only 1 in its hand, but not its color. The full-hand reference assigned **25% failure risk**. A receiver might read the sender's choice as “this 1 plays,” but the literal constraints do not establish that. Spark played the card on turn 9, revealed Yellow 1, and scored. At the same decision point, asking the Lantern policy to act from Spark's observation returned a clue, not the play.

This was a selected illustration, not a typical replay or an estimate of the effect of either clue. Spark is a risk-taking heuristic with a spare-fuse threshold, not an intent interpreter. Success is consistent with the rule for taking a calculated risk. It does not show that Spark interpreted the clue as a prompt or that the net game would be better under a different clue.

The regression test in `tests/test_conventions.py` replays this fragment, asserts the observed clue/slot and reveal, and verifies the literal risk before the play.
