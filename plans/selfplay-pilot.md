# First CPU self-play pilot

Start from the frozen group benchmark at `f3c52ae`. Use one
low-priority CPU worker and one BLAS thread. Budgets count game actions and evaluation deals.

## Learned self-play

- Train shared recurrent policy weights from scratch with separate memory per
  seat. Inputs come from the acting player's observation. Sample only legal
  actions. The sum of step rewards in a completed episode equals the official
  score, including the loss of all points on a fuse-out.
- Run a deterministic smoke test, then train seeds 11, 23 and 37 for **196,608
  environment steps each**, with updates every 16 complete games. Save the
  initialized and final weights. Record any incomplete final episode.
- Compare the three final policies on **128 paired validation deals**, suite
  seed 20261125. Select by mean official score, then lower fuse-out rate, then
  lower training seed. Lock the selected checkpoint before running the test.
- On **1,024 paired test deals**, suite seed 20261125, compare the selected
  checkpoint and its initialization with frozen Group, Lantern and Spark.
  Report mean official score, perfect rate, fuse-out rate and a whole-deal
  paired confidence interval. Keep training, validation and test decks disjoint.
  This measures same-policy play. Each mixed team needs its own evaluation.

## Unfamiliar partners

Test one independently specified change to the frozen group policy. Both seats
can see a misplay. An adaptive seat starts with Group and plays Lantern after
the first public misplay by either seat. This sacrifices one fuse before it
can react, and it cannot detect quiet disagreements such as a missed save.

Use 128 fresh matched deals (arena suite seed 20261126) for each existing
partner, both seats against Lantern or Spark. Compare those teams with the
original Group teams on the same decks. On the same deals, compare two adaptive
seats with two Group seats. Publish the paired score differences and fuse-out
rates. Include a team playing Lantern from the outset as a reference for each
pairing. Keep the browser partner frozen for this experiment.
