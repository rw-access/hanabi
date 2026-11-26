# First recurrent self-play pilot

The CPU pilot trains two seats with one set of weights. Each seat keeps its own
32-unit recurrent state, reset when a deal starts. The inputs encode public
resources, clue constraints on the seat's own cards, the visible partner hand
and the last two public events. A softmax samples among legal actions during
both training and evaluation.

The learner uses episodic REINFORCE with full backpropagation through each
seat's history. An action earns the change in official team score. Losing the
last fuse reverses all points accumulated in that game. Adam updates the weights
after 16 complete games, using a moving baseline from previous updates. The
step cap counts every legal action, including the final partial deal. Only
complete games contribute gradients. [Experiment plan](../plans/selfplay-pilot.md)

## Learned self-play

Three independent initializations consumed **589,824 environment steps** across
18,818 complete games and three partial games. The partial games accounted for
87 steps. All three candidates played the same 128 validation deals. Seed 11
scored 0.594, seed 23 scored 0.484 and seed 37 scored 0.508. Seed 11's final
weights and its initialization were saved together for the locked comparison.
[Training manifests](selfplay-v1/training.json) · [Validation outcomes](selfplay-v1/validation.json) · [Checkpoint selection](selfplay-v1/selection.json)

| Frozen policy | Mean /25 | Perfect games | Fuse-outs |
|---|---:|---:|---:|
| Group | 19.77 | 20 | 0 |
| Spark | 16.04 | 1 | 0 |
| Lantern | 15.83 | 0 | 0 |
| Trained recurrent policy | 0.55 | 0 | 274 |
| Same policy before training | 0.00 | 0 | 1,024 |

On 1,024 fresh matched deals, training gained **0.55 points** over initialization
(95% paired interval +0.50 to +0.60). It finished **19.22 points below Group**
(paired difference −19.39 to −19.04). The trained seats played a card on
2,394 of 74,988 actions (3.2%). They spent nearly all their turns giving clues
or discarding. Training reduced the fuse-out rate to 26.8%, but rarely built a
stack. The intervals describe this selected checkpoint on these deals. A
follow-up could test whether a warm start from Group demonstrations produces
more successful plays at a matched self-play budget. [Held-out deals](selfplay-v1/test.json) · [Paired improvement over initialization](selfplay-v1/improvement.json)

## Playing with unfamiliar partners

The opt-in `adaptive` bot follows Group while the team has all three fuses.
After either player misplays, it uses Lantern's literal clue policy for the
rest of the deal. Both seats observe the fuse count. The bot does not receive
its partner's name.

| Partner | Games per policy | Group /25 | Adaptive /25 | Always Lantern /25 | Paired gain over Group (95% CI) | Fuse-outs, Group → Adaptive |
|---|---:|---:|---:|---:|---:|---:|
| Copy of the same policy | 128 | 19.79 | 19.79 | 15.85 | 0.00 [0.00, 0.00] | 0 → 0 |
| Lantern | 256 | 6.40 | 15.30 | 15.85 | +8.90 [8.01, 9.76] | 130 → 0 |
| Spark | 256 | 6.60 | 15.27 | 16.10 | +8.67 [7.86, 9.50] | 129 → 0 |

The adaptive and Group policies had identical self-play scores on all 128
deals, including three perfect games each. Switching after a misplay recovered
most of the score lost to incompatible conventions. Playing Lantern from the
start still scored slightly higher with both literal partners. The mixed teams
played both seat orders on each deck. Intervals resample whole decks. Human
play remains unmeasured. [Per-deal scores and seeds](selfplay-v1/partners.json)

## Reproduction

Use Python with NumPy for the recurrent experiment. The recorded run used
Python 3.12.14 and NumPy 2.3.5. One low-priority worker handled the seeds
sequentially with single-threaded BLAS. Scheduling changes how long
the run takes. The three training seeds each receive 196,608 game actions.

```sh
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
for seed in 11 23 37; do
  python3 -m hanabi.train --steps 196608 --batch-games 16 --seed "$seed" \
    --output "reports/selfplay-v1/seed-$seed" || exit 1
done
python3 -m hanabi.evaluate --deals 128 --seed 20261125 --split validation \
  --checkpoint seed11=reports/selfplay-v1/seed-11/final.npz \
  --checkpoint seed23=reports/selfplay-v1/seed-23/final.npz \
  --checkpoint seed37=reports/selfplay-v1/seed-37/final.npz \
  --policies --output reports/validation.json
python3 -m hanabi.evaluate --deals 1024 --seed 20261125 --split test \
  --checkpoint initial=docs/selfplay-v1/seed-11-initial.npz \
  --checkpoint trained=docs/selfplay-v1/seed-11-final.npz \
  --policies group lantern spark --output reports/test.json
python3 -m hanabi.partner_probe --deals 128 --seed 20261126 > reports/partners.json
```

The trainer creates a new output directory per run. Each contains the initial
and final checkpoints, per-game deck and action seeds, counts after each update,
and a completion report with source and checkpoint hashes. The evaluator
takes a new output filename and records every paired deal. Training, validation
and test deck seeds occupy separate numeric ranges. Validation selects by mean
score, then lower fuse-out rate, then lower training seed.
