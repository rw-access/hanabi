# Written group conventions benchmark

`group` is a deterministic two-seat heuristic, not a trained policy or a complete model of the group. It is opt-in in the offline arena only; the browser teammate stays Lantern.

## What it does

- First take a known play. A card can be known from literal evidence or from a compatible convention signal. A known play whose rank is literally a 5 takes priority, then literal certainty, then the oldest slot.
- Treat newest as leftmost and oldest as rightmost, regardless of the arena's oldest-first storage order. Discard the oldest unpainted slot, the chop. A positive touch paints a slot; a negative clue alone does not. Track the slot through removals and draws using public events.
- A rank hitting the receiver's pre-clue chop means save. Other touched cards are protected from routine discard but receive no new play signal. They retain any prior clue and literal knowledge. Otherwise the newest touched card is the sole play focus. The clue's play inference is tied to the stacks at clue time, not retroactively advanced when a stack grows.
- When the partner has no predicted play, save an exposed last useful copy before giving a new play clue. For play, prefer a color pointing at a card actually playable now. Use a rank only if no such color clue exists, its focus is also actually playable, and it misses chop. Rank hints that would target the wrong focus are not used as play clues.
- Avoid an extra clue if the partner already has a predicted action or no turn left. Ties within a clue type favor a playable 5, fewer newly painted bystanders, then the arena's fixed clue order. At eight clues a discard is illegal: use a rank save or another clue that cannot convey an incorrect play instruction. If all cards are painted and a discard cannot be avoided, prefer a likely dead card and avoid likely last useful copies.
- Estimate partner knowledge using only public counts and exact visible partner identities. The giver does not know the card identities the partner sees in the giver's hand. No policy reads its own hidden identities, the shuffle seed, draw order or the other seat's observation.

Those tie-breaks, urgent-save definition, conservative now-only play signal and all-painted fallback make the policy executable; they are not claims about unwritten group rules. Rank save does not imply Always 2 Save. Finesse, bluff, reverse, priority and promise finesse, chop moves, chop focus, and proposed or undefined refinements are not implemented. The game is two-seat only, so there is no intervening third player.

## Reproduce

```sh
python3 -m unittest discover -v
python3 -m hanabi.arena --games 256 --seed 20261122 --policies lantern spark group --json
```

Use identical fresh deck and per-seat action streams across all matchups. A self-play matchup has 256 games, one per deal. A mixed matchup has 512 games, both seat assignments per deal. The official score is zero on fuse-out. Mixed play measures compatibility with a literal bot that does not speak this convention, not compatibility with the human group.

## Result

| Partners | Games | Mean / 25 | Perfect | Fuse-outs |
|---|---:|---:|---:|---:|
| Group + Group | 256 | 20.07 | 10 (3.9%) | 0 |
| Lantern + Lantern | 256 | 15.89 | 0 | 0 |
| Spark + Spark | 256 | 16.01 | 0 | 0 |
| Lantern + Spark | 512 | 16.02 | 0 | 0 |
| Group + Lantern | 512 | 7.70 | 0 | 204 (39.8%) |
| Group + Spark | 512 | 7.76 | 0 | 205 (40.0%) |

Group self-play averaged **4.18 points more** than Lantern self-play on the same decks. The much lower mixed scores show the cost of pairing it with the existing literal-style bots. They do not tell us how the group would score or which misunderstanding caused each lost game. See the [machine-readable result](group-policy-v1.json).
