from copy import deepcopy
from fractions import Fraction
from itertools import permutations
from random import Random
import unittest
from unittest.mock import patch

from hanabi import arena
from hanabi.conventions import PlayTally, _weighted_counts, literal_failure_probability
from hanabi.game import COLORS, COPIES, Game


def brute(copies, allowed, index, playable):
    physical = [c for c, count in copies.items() for _ in range(count)]
    bad = total = 0
    # permutations treats equal-valued entries in different positions as distinct copies.
    for hand in permutations(physical, len(allowed)):
        if all(c in slot for c, slot in zip(hand, allowed)):
            total += 1
            bad += hand[index] not in playable
    return bad, total


class LiteralRiskTests(unittest.TestCase):
    def test_other_slots_change_multiplicity_weighted_marginal(self):
        red1, blue2 = ("red", 1), ("blue", 2)
        copies = {red1: 2, blue2: 1}
        allowed, playable = [{red1, blue2}, {red1}], {red1}
        self.assertEqual(_weighted_counts(copies, allowed, 0, playable), (2, 4))
        self.assertEqual(_weighted_counts(copies, allowed, 0, playable), brute(copies, allowed, 0, playable))
        # The second slot consumes a red 1; considering only the first slot gives 1/3.
        self.assertEqual(_weighted_counts(copies, allowed[:1], 0, playable), (1, 3))

    def test_exact_counts_match_brute_force_on_small_hands(self):
        rng = Random(7702)
        cards = [("red", 1), ("yellow", 2), ("green", 3), ("white", 3)]
        for _ in range(40):
            copies = {c: rng.randint(0, 2) for c in cards}
            allowed = [{c for c in cards if rng.choice([True, False])} for _ in range(rng.randint(1, 5))]
            playable = {c for c in cards if rng.choice([True, False])}
            for index in range(len(allowed)):
                self.assertEqual(_weighted_counts(copies, allowed, index, playable),
                                 brute(copies, allowed, index, playable))

    def test_fresh_hand_and_visible_copies(self):
        view = Game(76).view(0)
        failures = len(COLORS) * (sum(COPIES.values()) - COPIES[1])
        visible_failures = sum(c["rank"] != 1 for c in view["partner"])
        expected = Fraction(failures - visible_failures, view["remaining"] + len(view["hand"]))
        for i in range(len(view["hand"])):
            self.assertEqual(literal_failure_probability(view, i), expected)

    def test_positive_and_negative_clues(self):
        g = Game(12)
        v = g.view(0)
        self.assertTrue(any(c["rank"] == 1 for c in v["partner"]))
        g.apply({"type": "hint", "kind": "rank", "value": 1})
        receiver = g.view(1)
        match = receiver["history"][-1]["matched"]
        self.assertTrue(0 < len(match) < 5)
        for i in range(len(receiver["hand"])):
            self.assertEqual(literal_failure_probability(receiver, i), 0 if i in match else 1)

    def test_risk_ignores_choice_history_and_private_state(self):
        g = Game(13)
        v = g.view(0)
        before = literal_failure_probability(v, 0)
        # With no clues, swap a hidden card and deck card; the risk must not change.
        g._deck[0], g._hands[0][0].card = g._hands[0][0].card, g._deck[0]
        unseen_changed = g.view(0)
        self.assertEqual(v, unseen_changed)
        # Partner choices, policy identity, and action ordering aren't inputs.
        changed = deepcopy(unseen_changed)
        changed["legal"].reverse()
        changed["policy"] = "a different bot"
        changed["history"].append({"type": "hint", "kind": "color", "value": "green", "matched": [1]})
        self.assertEqual(literal_failure_probability(changed, 0), before)

    def test_observed_replay_rank_one_clue_leaves_failure_risk(self):
        game = Game(1770899062782801945)  # Suite seed 0, deal 4, Spark first.
        policies = ("spark", "lantern")
        for _ in range(7):
            game.apply(arena.choose(game.view(game.current), policies[game.current]))
        game.apply(arena.choose(game.view(1), "lantern"))
        observer = game.view(0)
        self.assertEqual(observer["history"][-1],
                         {"type": "hint", "kind": "rank", "value": 1, "player": 1, "turn": 8, "matched": [4]})
        move = arena.choose(observer, "spark")
        self.assertEqual(move, {"type": "play", "index": 4})
        self.assertEqual(literal_failure_probability(observer, move["index"]), Fraction(1, 4))
        self.assertEqual(arena.choose(observer, "lantern")["type"], "hint")
        game.apply(move)
        result = game.view(0)["history"][-1]
        self.assertEqual(result["card"], {"color": "yellow", "rank": 1})
        self.assertTrue(result["success"])

    def test_tally_empty_and_pinned(self):
        tally = PlayTally(turns=4)
        self.assertIsNone(tally.report()["gap_pp"])
        self.assertIsNone(tally.report()["nonzero_risk_gap_pp"])
        with self.assertRaises(RuntimeError):
            tally.add(0, True)
        with self.assertRaises(RuntimeError):
            tally.add(1, False)
        tally.add(0, False)
        tally.add(Fraction(1, 2), False)
        self.assertEqual(tally.report()["gap_pp"], 25)
        self.assertEqual(tally.report()["nonzero_risk_gap_pp"], 50)
        self.assertEqual(tally.report()["literal_safe_share"], .5)


class AuditIntegrationTests(unittest.TestCase):
    def test_audit_does_not_change_action_sequence(self):
        def trace(**opts):
            actions = []
            original = arena.choose

            def capture(view, policy, rng):
                action = original(view, policy, rng)
                actions.append(action)
                return action

            with patch.object(arena, "choose", side_effect=capture):
                outcome = arena.play(321, ("random", "spark"), (901, 567), **opts)
            return actions, outcome

        pooled, first, second = PlayTally(), PlayTally(), PlayTally()
        expected = trace()
        self.assertEqual(trace(play_audits=((pooled, first), (pooled, second))), expected)
        self.assertEqual(pooled.turns, len(expected[0]))
        self.assertEqual(pooled.plays, sum(action["type"] == "play" for action in expected[0]))
        self.assertEqual(pooled.plays, first.plays + second.plays)

    def test_benchmark_grouping_and_default_report_unchanged(self):
        audit = arena.benchmark(["lantern", "spark"], 2, 11, convention_gap=True)
        cross_play = audit["matchups"][1].pop("convention_gap")
        self.assertEqual({(s["seat"], s["policy"]) for s in cross_play["by_seat"]},
                         {(0, "lantern"), (0, "spark"), (1, "lantern"), (1, "spark")})
        self.assertEqual(cross_play["plays"], sum(s["plays"] for s in cross_play["by_seat"]))
        self.assertEqual(cross_play["failures"], sum(s["failures"] for s in cross_play["by_seat"]))
        for row in audit["matchups"]:
            row.pop("convention_gap", None)
        self.assertEqual(audit, arena.benchmark(["lantern", "spark"], 2, 11))
