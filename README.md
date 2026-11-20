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

**First signal:** supporting a more literal teammate is a bigger gap than self-play implies. This is a small development benchmark, not an estimate of how well the bot plays with a human.

## Next experiment: partner lift, not Elo

Borrow the matched-opening idea from the neighboring `metatictactoe` project, but replace one teammate rather than asking who wins. The eval primitive here is a deal seed × partner × both seats. A positive result should help a new partner, not just another copy of the same policy.

1. Freeze the current policies and a second, independently authored partner family. Reserve unseen deals and at least one entire partner family for final testing. Never train on the acceptance set or report this 32-deal smoke run as held-out evidence.
2. On **Cerberus**, start with a small recurrent imitation model (e.g. GRU) distilling a stronger scripted teacher over rotating partners. Encode only the acting player’s observation, clue/action history and legal-action mask. Never include own card identities, deck, seed, simulator state or another seat’s private observations as policy input. First check determinism and legality on a tiny run; no trainer, dependencies, checkpoints or jobs are provisioned by this prototype.
3. Compare candidate and frozen Lantern with the **same partner, same unseen deal and same starting seat**. Average seat-swapped deltas within a deal, weight partner families equally, and report points gained /25, per-partner changes, weakest partner and changes in fuse-out/perfect rates. Show self-play and the random control separately, not in the promotion metric. For an acceptance run on Cerberus, use ~1,024 paired deals for meaningful partners and bootstrap by whole deal (not individual seat games); increase the sample if inconclusive. A stronger team score with worse fuse-outs or a collapsing partner warrants inspection, not promotion.
4. Before attributing a gain to human compatibility, play blinded human sessions and inspect unsolicited risky plays and whether the clues made sense. If imitation plateaus, test constrained lookahead on hidden hands sampled from public evidence; compare to the same baseline first.

Project map: `hanabi/game.py` (rules and observations), `hanabi/bots.py` (public-view policies), `hanabi/arena.py` (evaluation), `hanabi/server.py` (local session/API), `hanabi/static/` (browser), `tests/` (rules, privacy, policies and API). No upstream or deploy configuration is required.

Background: [The Hanabi Challenge](https://research.google/pubs/the-hanabi-challenge-a-new-frontier-for-ai-research/), [“Other-Play” for Zero-Shot Coordination](https://proceedings.mlr.press/v119/hu20a.html), and [Collaborating with Humans without Human Data](https://proceedings.neurips.cc/paper/2021/hash/86e8f7ab32cfd12577bc2619bc635690-Abstract.html).
