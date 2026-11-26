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

## Reproduction

Use Python with NumPy for the recurrent experiment. The recorded run used
Python 3.12.14 and NumPy 2.3.5. One low-priority worker handled the seeds
sequentially on Lupin with single-threaded BLAS. Scheduling changes how long
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
python3 -m hanabi.partner_probe --deals 128 --seed 20261126 > reports/partners.json
```

The trainer creates a new output directory per run. Each contains the initial
and final checkpoints, per-game deck and action seeds, counts after each update,
and a completion report with source and checkpoint hashes. The evaluator
takes a new output filename and records every paired deal. Training, validation
and test deck seeds occupy separate numeric ranges. Validation selects by mean
score, then lower fuse-out rate, then lower training seed.
