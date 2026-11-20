# A clue can save the card you don’t play

Run `python3 -m hanabi.clue_probe` for the complete JSON report. No training, installs or model required.

The giver can see the **last red 3** in the receiver's oldest slot. Red is at 1, so the card matters but can't play yet. Green is at 2, and the next slot holds a playable green 3. Only one clue token remains. A **3** clue marks both 3s; a **green** clue marks the green 3 and green 4 instead. Both spend the last token and touch two cards, but they do not convey the same literal information.

| Earlier clue | Giver does | Receiver's next move | Red 3 after receiver's second turn | Red 3 at end | Team /25 |
|---|---|---|---|---|---:|
| Repeat 5 | Clue 3 | Discard yellow 2 | In hand | Played | 20 |
| Repeat 5 | Clue green | Discard red 3 | Discarded | Discarded | 14 |
| Repeat 5 | Clue 2 | Discard yellow 2 | Discarded | Discarded | 15 |
| Repeat 5 | Unforced discard | Clue yellow | In hand | Discarded | 19 |
| Red | Clue 3 | Discard yellow 2 | In hand | Played | 15 |
| Red | Clue green | Discard yellow 2 | In hand | Played | 15 |
| Red | Clue 2 | Discard yellow 2 | In hand | Played | 18 |
| Red | Unforced discard | Discard green 3 | In hand | Played | 14 |

The first and third rows have the **same next move**. The target's fate diverges one receiver turn later. In the first row the receiver never needs to treat “3” as a play command to get the save: it discards yellow 2; the giver then clues green, and the receiver plays green 3. With the earlier red clue, both 3 and green keep the red 3 and yield 15. Keeping the target is a diagnostic, not the reward: without the earlier red clue, even the unforced discard scores 19 despite later losing the red 3.

## How the comparison works

- One deliberately selected two-seat deal (seed 70696). The 12-turn setup is a **script of legal moves**, not a sample of ordinary policy choices. Repeated hints spend tokens. The paired history replaces just turn 11's repeated rank-5 clue with a red clue. This also tells the receiver that its other cards are *not* red. Neither case makes the red 3 fully identifiable to the receiver before the comparison.
- At the start of turn 13 the visible receiver hand in engine order is **red 3, green 3, yellow 2, green 4, blue 5**. Engine slot 0 is the oldest, new draws go on the right; this is the reverse of a newest-left display. The other red 3 is already in the discard pile. Stack heights, tokens, lives, card identities and remaining deck order match exactly across the histories.
- For each history, independently fork the same game before turn 13. Try every legal clue plus the giver's actual unforced Lantern move (a discard from its own slot 2). This baseline doesn't spend a token, so it is **not** a cost-matched control. Do not substitute a pass for a legal action.
- After the intervention both seats use unchanged, deterministic Lantern with a fresh legal observation on every turn. The underlying draw order is matched, not necessarily *which seat* gets each card when branches diverge. No seed, target label or branch score goes to a policy. Track the original receiver card across removals; distinguish still in hand, successfully played, discarded and misplayed. The horizon is fixed at three plies after the intervention or terminal, whichever comes first, not the next time somebody happens to discard. Roll through termination for the official score (zero for fuse-out). All 18 branches in this fixture ended on deck exhaustion.
- These are **forward** consequences conditional on one known world, history and heuristic. The table is not an estimated advantage over fresh deals, a reason to expect every saved 3 to earn six points, or evidence of human conventions. The natural Lantern giver actually chose a discard in both histories.

## What this doesn’t tell the receiver

“What happens if I give this clue?” and “Why did my teammate give it?” run in opposite directions. The latter needs a model of the **giver's choice among legal alternatives**. For example, would a giver trying to save the oldest card be more likely to choose 3 rather than green? What about one trying to set up a green play? It might have both goals, or neither.

To estimate intent from a received clue, weight candidate goals by their priors and by the likelihood of the observed clue **and its touched slots** under each goal. Average over hidden worlds consistent with the receiver's pre-clue information. The giver model must choose from *its* legal observation, not this evaluator's deck order or the rollout's realized team score. This fixture provides consequence labels for checking a forward predictor; it neither fits that giver model nor produces an intent posterior. Generalization needs whole held-out deals and other partners.
