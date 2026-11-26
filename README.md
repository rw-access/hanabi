# Lantern • Hanabi

Play a two-seat game of Hanabi in your browser with **Lantern**, a local rule-based teammate. You can see Lantern’s hand, it can see yours, and you share one score. There are no accounts, AI services, model downloads or package installs.

For invite-only online rooms, solo play with Adaptive, and Cloudflare setup, see the [online game guide](worker/README.md).

## Play on a self-hosted server

Open `/hanabi/` on your configured preview server and choose
**Deal me in**. Your teammate is **Adaptive**. Before taking its own play, it
looks for a clue that gives you a provably safe play. It can also protect a
last useful copy on your oldest untouched card. Incoming clues appear beside
your hand, with the affected cards marked. Adaptive uses the group's conventions
and switches to literal play after either player misplays. Replay saving is selected when you deal. Clear the recording
checkbox to play privately. A saved replay includes both players' moves and the
deal needed to reconstruct what each player could see at each decision, even
if you leave the game unfinished. Replays stay on the server for offline analysis.
[Preview storage and recovery](deploy/preview/README.md)

With a clue token, all five colors and all five numbers are available. If
Adaptive has no 1s, choose **No 1s** to rule out 1 for every card it holds.
Your own cards show every suit and number still possible. Select a card to
inspect the odds for each exact suit–number pair and see whether it plays safely.

## Play locally

Requires Python 3.10+ (tested with 3.13). From the checkout:

```sh
python3 -m hanabi.server
```

Open **http://127.0.0.1:8765/** in a browser on the same computer and choose **Deal me in**. The five firework stacks sit in the tray at the top. Lantern’s cards are face up below it, and your hand is face down nearest you. Under **Give a clue**, pick a color or number to preview it before sending. On a phone, tap **Give a clue** beside Lantern’s name to open the choices. To play or discard, select a card from your hand. **How to play** explains the rules. Use Ctrl-C to stop the server or `--port 8766` if the default port is occupied.

Refresh or reopen the same browser and origin to resume. Stopping the server,
clearing the cookie, or reaching the 24-hour idle limit ends access to the live
game. To enable recording for a standalone local server, run:

```sh
python3 -m hanabi.server --record-db ~/.local/state/hanabi/games.sqlite3 --revision "$(git rev-parse HEAD)"
```

Use `python3 -m hanabi.recordings summary ~/.local/state/hanabi/games.sqlite3`
for counts, or replace `summary` with `export` to stream one JSON replay per
line. Replay files contain private deal information. For learning a policy,
use the acting seat's pre-move observation as input. The recorded action labels
the move and the separate `own_card_targets` list labels the cards in that
seat's hand at that moment. Those targets are available in the offline export.

## Rules and what Lantern knows

- Five colors, 50 cards: each color contains three 1s, two each of 2, 3 and 4, and one 5. Each person starts with five cards. Play ascending 1–5 stacks for up to 25 points.
- A clue costs one of eight tokens, identifies **all** cards of the named color or rank in the other hand, and rules it out on the rest. Browser deals allow zero matches. The original classic benchmark requires at least one match. A new card (rightmost) inherits no clues. [Hanab Live’s empty-clue option](https://github.com/Hanabi-Live/hanabi-live/blob/main/docs/features.md#empty-clues)
- A discard refunds one clue. You cannot discard at eight clues; completing a 5 restores a clue up to the cap.
- After the last card is drawn, each player gets **one additional turn**, including the drawer. A perfect show ends immediately. A misplay spends a fuse; on the third misplay the game ends and the score is **zero**, not the sum of the partially built stacks. Five-color base game only. [Publisher’s rules and game page](https://rnrgames.com/hanabi).

The browser receives a view fixed to the human’s seat, and each policy consumes the same observation format from its own perspective. The public log reveals cards when played or discarded. A recorded deal has a private server-generated seed for offline reconstruction. The web API neither accepts nor returns a seed. An opaque per-deal ID and turn revision prevent duplicate or stale moves. The suite tests that swapping an unknown card with the deck does not change a seat's observation.

The browser and its teammate calculate own-card odds from the same information available to each seat. They count the physical deals consistent with the whole hand’s clues after subtracting played cards, discards, and visible partner cards. This accounts for cases where another slot needs the last copy. The probabilities assume a shuffled deck and literal clue information. Adaptive considers its partner’s conventions separately when choosing a move.

## Test and evaluate

```sh
python3 -m unittest discover -v
python3 -m hanabi.arena --games 32 --seed 0
python3 -m hanabi.arena --games 8 --seed 7 --policies lantern literal spark --json
```

The optional UI smoke test is `node tests/browser.cjs` against a running server. It needs an existing Node/Playwright and Chromium installation; these are **not** runtime game dependencies. Use `NODE_PATH` for a preinstalled Playwright, `CHROMIUM_EXECUTABLE` for an existing Chromium binary and `HANABI_URL` to override the default local URL. It tests a 1280×800 desktop and a 390px touch viewport with disposable browser contexts.

`--games` sets the number of matched deals (1–256) **per pairing**, not the total number of games. The same deals and seat-specific action randomness are used across all pairings. Distinct partners play both seat orders; identical partners play once per deck. Frozen heuristics are counting (**Lantern**), conservative (**Literal**), more adventurous (**Spark**), and a uniform-legal random **negative control**. These are related heuristic variants, not independent human conventions. Scores include zero for fuse-outs; always inspect perfect and fuse-out rates alongside the mean.

[First baseline](docs/baseline-v1.json), suite seed 0: 32 shared decks, 512 games across 10 pairings.

| Lantern’s partner | Games | Mean / 25 | Perfect | Fused out |
|---|---:|---:|---:|---:|
| Lantern | 32 | 16.41 | 0% | 0% |
| Literal | 64 | 13.56 | 0% | 0% |
| Spark | 64 | 16.66 | 0% | 0% |
| Random control | 64 | 0.14 | 0% | 98.4% |

**Starting line:** Lantern + Lantern scored 16.41/25 on the development suite. Spark + Spark scored 16.91/25. Neither pairing fused out or scored a perfect game. These results establish a baseline for improving a team of two copies, not a claim about optimal play or human compatibility.

Optional: `python3 -m hanabi.arena --games 32 --seed 0 --convention-gap` compares actual misplays with exact literal risk from the acting player's view. See the [diagnostic, baseline and one replay](docs/convention-gap.md). This is analysis only, not a training objective.

For the written group conventions, use the opt-in `group` arena policy. See the [policy, limits and matched benchmark](docs/group-policy.md). The local browser defaults to Lantern. Start it with `--teammate adaptive` to use the same policy as the self-hosted preview.

For a matched experiment rather than a retrospective statistic, run `python3 -m hanabi.clue_probe`. [One legal replay](docs/clue-probe.md) shows two clues with identical immediate responses but different consequences for the last red 3 and the final team score.

## Research question: what should a good Hanabi policy do?

There are two useful tests, and a policy can excel at one and fail the other. Keep both measurements rather than committing to one permanent objective.

- **Track A — same-policy discovery.** When two seats use the **same frozen checkpoint**, how high can their shared score get, and what conventions do they invent? Mean official team score out of 25 on held-out deals is the primary readout for the first training experiment. Also report perfect-game and fuse-out rates. “Best found” means best measured under this ruleset and test suite, not a proof of global optimality.
- **Track B — unfamiliar teammates.** Does the same candidate help different partners, including humans? Track lift over frozen heuristics with the same partner on the same deals in both seat assignments. Report the weakest pairing and fuse-outs, and use blinded human sessions before making claims about compatibility. Keep this score separate from Track A.

Start with Track A for a clean learning signal; use Track B to expose brittle conventions. Results from both can inform which objective to pursue next.

### First proposed Cerberus experiment

1. **Start from scratch.** Use shared-weights recurrent RL (for example, a small GRU with legal-action-masked PPO), with a separate memory state for each seat, reset each game. Train both seats toward the same official team score. Any incremental rewards must sum to that score, including resetting to zero on a fuse-out. Both actor and critic consume only a seat's own legal observation and public action/clue history. No access to own card identities, the seed, hidden draws, the other seat's observation, or the other seat's memory.
2. **Make the run explainable.** Begin with a CPU smoke run for legality, determinism, hidden-state reset, and reward accounting before spending GPU time. For the first controlled run, pin the move encoding and game version; log config, checkpoint, deck and action-stream seed manifests, training seeds, and budget. Train several independent seeds at equal environment-step budgets. Compare to frozen Lantern + Lantern and Spark + Spark. An imitation-warm-start followed by identical RL is an optional comparator, not the default, to see whether copying heuristics helps learning or limits discovery.
3. **Evaluate on matched fresh deals.** Use disjoint training and validation pools, select checkpoints only on validation, then lock them before running a fresh, untouched test suite (about 1,024 deals as a starting point). Reuse each test deal across teams; publish per-deal scores, mean/25, perfect and fuse-out rates, and a paired confidence interval for the mean lift over the strongest frozen heuristic. Version the suite, including deck seeds, seat assignments, and stochastic action streams. Two identical checkpoints already occupy both seats; swapping their labels adds no distinct game. For any mixed-team diagnostic, run both seat assignments on each deck before averaging and bootstrap by whole deal, not seat-game. Keep same-policy and mixed results separate.
4. **Look at what they learned.** After scoring is locked, inspect sample replays for consistent clue meanings and try mixed checkpoints from independent training seeds to see which conventions transfer. Evaluate promising checkpoints on the same Track B diagnostic suite as well; don't infer people can use a convention just because the agents agree on it.

The current self-play trainer uses NumPy. Its recurrent policy learns from the
team score and from predicting each occupied slot's color and number before
every move. Each run uses an exact action budget and writes its checkpoints
and work counts to a fresh directory. Evaluation reports team score, card
prediction accuracy, and log loss against the exact deduction baseline.
[Own-hand pilot](docs/own-hand-pilot.md) · [First score-only pilot](docs/selfplay-pilot.md)

```sh
python3 -m hanabi.train --steps 2048 --seed 7 --output reports/my-smoke
python3 -m hanabi.evaluate --split validation --seed 7 --deals 16 \
  --checkpoint smoke=reports/my-smoke/final.npz --output reports/my-smoke/validation.json
```

Project map: `hanabi/game.py` (rules and observations), `hanabi/bots.py` (public-view policies), `hanabi/arena.py` (evaluation), `hanabi/server.py` (local session/API), `hanabi/static/` (browser), `tests/` (rules, privacy, policies and API). No upstream or deploy configuration is required.

Background: [The Hanabi Challenge](https://research.google/pubs/the-hanabi-challenge-a-new-frontier-for-ai-research/), [“Other-Play” for Zero-Shot Coordination](https://proceedings.mlr.press/v119/hu20a.html), and [Collaborating with Humans without Human Data](https://proceedings.neurips.cc/paper/2021/hash/86e8f7ab32cfd12577bc2619bc635690-Abstract.html).
