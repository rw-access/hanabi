import copy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

try:
    import numpy as np
except ImportError:
    np = None

from hanabi.game import COLORS, RANKS, Game
from hanabi.train import hand_labels

if np is not None:
    from hanabi.learner import ACTIONS, Learner, action_id, encode


@unittest.skipIf(np is None, "self-play learner requires optional NumPy")
class RecurrentPolicyTests(unittest.TestCase):
    def trajectory(self, policy):
        """Two turns per seat; returns and gradients stay nonzero on both recurrences."""
        game = Game(seed=9)
        states = [policy.initial_state(), policy.initial_state()]
        rng = np.random.default_rng(77)
        episode = []
        for reward in (0, 1, -1, 2):
            seat = game.current
            move, states[seat], record = policy.act(game.view(seat), states[seat], rng)
            labels = hand_labels(game, seat)
            game.apply(move)
            episode.append((seat, record, reward, labels))
        return episode

    def test_stable_action_ids_and_mask(self):
        actions = ([{"type": kind, "index": i} for kind in ("play", "discard") for i in range(5)]
                   + [{"type": "hint", "kind": kind, "value": v}
                      for kind, values in (("rank", RANKS), ("color", COLORS)) for v in values])
        self.assertEqual([action_id(move) for move in actions], list(range(20)))
        model = Learner(7)
        game = Game(seed=8)
        states = [model.initial_state(), model.initial_state()]
        rng = np.random.default_rng(42)
        while not game.over:
            seat = game.current
            view = game.view(seat)
            move, states[seat], record = model.act(view, states[seat], rng)
            self.assertGreater(record.probabilities[record.choice], 0)
            self.assertAlmostEqual(record.probabilities.sum(), 1)
            self.assertEqual(set(np.flatnonzero(record.probabilities)),
                             {action_id(a) for a in view["legal"]})
            self.assertIn(move, view["legal"])
            game.apply(move)
        with self.assertRaises(ValueError):
            model.act(game.view(game.current), model.initial_state(), rng)

    def test_hidden_card_invariance_and_state_reset(self):
        first = Game(seed=3)
        second = copy.deepcopy(first)
        slot = second._hands[0][0]
        swap = next(i for i, card in enumerate(second._deck) if card != slot.card)
        second._deck[swap], slot.card = slot.card, second._deck[swap]
        model = Learner(5)
        v1, v2 = first.view(0), second.view(0)
        np.testing.assert_array_equal(encode(v1), encode(v2))
        a1, new1, rec1 = model.act(v1, model.initial_state(), np.random.default_rng(81))
        a2, new2, rec2 = model.act(v2, model.initial_state(), np.random.default_rng(81))
        self.assertEqual(a1, a2)
        np.testing.assert_array_equal(new1, new2)
        np.testing.assert_array_equal(rec1.probabilities, rec2.probabilities)
        np.testing.assert_array_equal(rec1.prediction, rec2.prediction)
        self.assertNotEqual(hand_labels(first, 0), hand_labels(second, 0))
        # Advancing one seat cannot mutate the other seat's previous state.
        self.assertFalse(np.shares_memory(rec1.previous, new1))
        np.testing.assert_array_equal(rec1.previous, np.zeros_like(new1))

    def test_two_public_events_change_the_encoding(self):
        game = Game(seed=123)
        for _ in range(2):
            view = game.view(game.current)
            move = next(move for move in view["legal"] if move["type"] == "hint")
            game.apply(move)
        view = game.view(game.current)
        features = encode(view)
        self.assertEqual(features.shape, (Learner.config()["features"],))
        older = features[-74:-37]
        newer = features[-37:]
        for event, vector in zip(view["history"], (older, newer)):
            self.assertEqual(np.flatnonzero(vector[:ACTIONS]).tolist(), [action_id(event)])
            self.assertEqual(np.flatnonzero(vector[30:35]).tolist(), event["matched"])
            self.assertEqual(vector[36], event["player"] != view["seat"])

    def test_gradient_against_replayed_two_seat_objective(self):
        policy = Learner(11)
        policy.baseline = 0.75
        episode = self.trajectory(policy)
        actual, _ = policy._gradients([episode])
        returns = [sum(item[2] for item in episode[t:]) for t in range(len(episode))]

        def objective():
            states = [policy.initial_state(), policy.initial_state()]
            total = 0.0
            for (seat, record, _, labels), ret in zip(episode, returns):
                ids = np.flatnonzero(record.probabilities)
                states[seat], probs = policy._forward(record.observation, states[seat], ids)
                total += (ret - policy.baseline) * np.log(probs[record.choice])
                predictions = policy._predict(states[seat], record.prior)
                total += np.log(predictions[np.arange(len(labels)), labels]).mean()
            return total

        epsilon = 1e-5
        for key, params in policy.weights.items():
            with self.subTest(parameter=key):
                # Choose a parameter with a measurable contribution to this trajectory.
                idx = np.unravel_index(np.abs(actual[key]).argmax(), params.shape)
                self.assertGreater(abs(actual[key][idx]), 1e-4)
                original = params[idx]
                params[idx] = original + epsilon
                plus = objective()
                params[idx] = original - epsilon
                minus = objective()
                params[idx] = original
                self.assertAlmostEqual(actual[key][idx], (plus - minus) / (2 * epsilon), places=7)

    def test_checkpoint_preserves_actions_and_optimizer(self):
        model = Learner(12)
        before = model.weights["recurrent"].copy()
        model.update([self.trajectory(model)])
        self.assertFalse(np.array_equal(before, model.weights["recurrent"]))
        with TemporaryDirectory() as tmp:
            path = Path(tmp, "snapshot")
            model.save(path)
            other = Learner.load(path)
            self.assertEqual(other.baseline, model.baseline)
            self.assertEqual(other.updates, model.updates)
            original, replay = self.trajectory(model), self.trajectory(other)
            for (_, a, _, _), (_, b, _, _) in zip(original, replay):
                self.assertEqual(a.choice, b.choice)
                np.testing.assert_array_equal(a.probabilities, b.probabilities)
                np.testing.assert_array_equal(a.prediction, b.prediction)
            model.update([original])
            other.update([replay])
            for key in model.weights:
                np.testing.assert_array_equal(other.weights[key], model.weights[key])
            model.save(path)
            self.assertEqual(Learner.load(path).updates, 2)

    def test_short_hand_mask_and_auxiliary_gradient_without_score(self):
        policy = Learner(8)
        game = Game(seed=20)
        game._deck.append(game._hands[0].pop().card)
        # Exact rank on one slot gives impossible identities to mask.
        slot = game._hands[0][0]
        slot.clue("rank", slot.card.rank, True)
        _, _, decision = policy.act(game.view(0), policy.initial_state(), np.random.default_rng(5))
        targets = hand_labels(game, 0)
        self.assertEqual(decision.prediction.shape, (4, 25))
        np.testing.assert_allclose(decision.prediction.sum(axis=1), 1.)
        self.assertTrue(np.array_equal(decision.prediction == 0, decision.prior == 0))
        self.assertTrue(np.any(decision.prediction[0] == 0))
        gradients, returns = policy._gradients([[(0, decision, 0, targets)]])
        self.assertEqual(returns, 0)
        self.assertEqual(np.count_nonzero(gradients["output"]), 0)
        self.assertGreater(np.linalg.norm(gradients["input"]), 0)
        card_gradient = gradients["card_bias"].reshape(5, 25)
        self.assertEqual(np.count_nonzero(card_gradient[4]), 0)
        self.assertTrue(np.all(card_gradient[np.arange(4), targets] > 0))
