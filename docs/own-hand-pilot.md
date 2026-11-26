# Predicting your own hand

Before either seat moves, the model assigns probabilities to the 25 color–rank identities for each card it holds. The public deduction engine supplies a starting distribution based on remaining copies and the entire hand’s clues. The model can adjust the relative odds of possible identities using its observations and recurrent memory. An impossible identity always has zero probability.

Once the model has chosen its action, the trainer checks each prediction against the corresponding pre-move card. This supplies a supervised learning signal even for a deal that ends at zero. Both the card prediction loss and the official team return update the shared recurrent network. The human replay exporter supplies the same pre-move labels for future offline work.

## Pilot results

One low-priority Lupin core ran 8,192 environment actions at seed 37. The trainer applied 35 updates from 546 complete games and measured 40,960 card predictions. The last 11 actions belonged to an unfinished game.

On 128 held-out deals, the trained policy made 9,650 card predictions over 1,930 decisions. Both rows below use those exact hands. Top-one accuracy counts an exact color **and** rank match. Lower log loss indicates better probability estimates.

| Predictor | Exact identity accuracy | Log loss in nats |
| --- | ---: | ---: |
| Literal deductions and copy counts | 18.74% | 2.324 |
| Deductions plus learned adjustment | 19.23% | 2.329 |

The small increase in first-choice accuracy came with slightly worse log loss. Both the initial and trained self-play policies lost all three fuses on every held-out deal. Adaptive self-play averaged 20.02/25 on the same deals, with three perfect games and zero fuse-outs. One seed and a short training run establish that the prediction objective works end to end, while leaving improvement in play unproven.

## Reproduce

Use Python with NumPy and a fresh output directory. The reported Adaptive baseline used revision `1220a7f`, before its partner-clue priority changed. The trainer creates both checkpoints. The evaluator uses a separate test seed domain and deals the same decks to all three participants.

```sh
python -m hanabi.train --steps 8192 --seed 37 --output reports/own-hand-pilot-37
python -m hanabi.evaluate --split test --seed 37 --deals 128 --empty-clues \
  --checkpoint initial=reports/own-hand-pilot-37/initial.npz \
  --checkpoint trained=reports/own-hand-pilot-37/final.npz --policies adaptive \
  --output reports/own-hand-pilot-37/test.json
```
