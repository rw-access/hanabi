# Lantern • Hanabi

Play a two-seat game of Hanabi in your browser with **Lantern**, a local rule-based teammate. You can see Lantern’s hand, it can see yours, and you share one score. There are no accounts, AI services, model downloads or package installs.

## Play on Lupin

Requires Python 3.10+ (tested with 3.13). From the checkout:

```sh
cd ~/Development/hanabi
python3 -m hanabi.server
```

Open **http://127.0.0.1:8765/** in a browser **on Lupin**. Choose **Deal me in**. Select a color or number below Lantern’s hand to preview *all* the matching cards, then give the clue. Or select one of your own cards and play or discard it. **How to play** explains the rules. Use Ctrl-C to stop the server; use `--port 8766` if the default port is occupied.

Refresh or reopen the same browser and origin to resume. The server keeps games in memory: stopping the server, clearing the cookie, or reaching the 24-hour session limit loses the game. This is a local prototype, not an Internet service: it binds only to IPv4 loopback and has no remote-human rooms. Restart replaces the current deal rather than saving a replay.

## Rules and what Lantern knows

- Five colors, 50 cards: each color contains three 1s, two each of 2, 3 and 4, and one 5. Each person starts with five cards. Play ascending 1–5 stacks for up to 25 points.
- A clue costs one of eight tokens, identifies **all** cards of a present color or rank in the other hand, and rules it out on the rest. Repeated valid clues are allowed. A new card (rightmost) inherits no clues.
- A discard refunds one clue. You cannot discard at eight clues; completing a 5 restores a clue up to the cap.
- After the last card is drawn, each player gets **one additional turn**, including the drawer. A perfect show ends immediately. A misplay spends a fuse; on the third misplay the game ends and the score is **zero**, not the sum of the partially built stacks. Five-color base game only. [Publisher’s rules and game page](https://rnrgames.com/hanabi).

Only `Game` owns the shuffled deck and both hands. The browser receives a view fixed to the human’s seat; every policy consumes the same observation format from its own perspective. The public log records cards only when played or discarded, never when privately drawn. Seeded deals are for offline experiments; the web API neither accepts nor returns a seed. An opaque per-deal ID and turn revision prevent duplicate or stale moves. The suite tests that swapping an unknown card with the deck does not change a seat's observation.

Lantern knows **literal clues**, not a private signaling convention. It counts remaining copies, plays cards it can prove safe, values clues that identify playable cards, and tries to avoid discarding the last usable copy of a card. With a spare fuse and the final turns approaching, it may take a chance. The copy counts are a single-card approximation, not a full joint posterior over a hand. Neither the interface nor the bot suggests that merely being clued means you should immediately play.

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

This repo contains the rules, browser game and heuristic arena only. No trainer, checkpoints or Cerberus jobs are provisioned yet.

Project map: `hanabi/game.py` (rules and observations), `hanabi/bots.py` (public-view policies), `hanabi/arena.py` (evaluation), `hanabi/server.py` (local session/API), `hanabi/static/` (browser), `tests/` (rules, privacy, policies and API). No upstream or deploy configuration is required.

Background: [The Hanabi Challenge](https://research.google/pubs/the-hanabi-challenge-a-new-frontier-for-ai-research/), [“Other-Play” for Zero-Shot Coordination](https://proceedings.mlr.press/v119/hu20a.html), and [Collaborating with Humans without Human Data](https://proceedings.neurips.cc/paper/2021/hash/86e8f7ab32cfd12577bc2619bc635690-Abstract.html).
