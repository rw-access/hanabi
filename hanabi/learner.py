"""Optional NumPy recurrent policy for two-seat, on-policy self-play."""

from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np

from .game import COLORS, COPIES, RANKS
from .beliefs import hand_beliefs


LEGACY_FORMAT = "hanabi-recurrent-reinforce-v1"
FORMAT = "hanabi-recurrent-belief-v2"
ACTIONS = 20
HIDDEN = 32
SLOTS, IDENTITIES = 5, 25
AUXILIARY_WEIGHT = 1.0
# Board and resources, discards by identity, five own and five partner slots,
# and two public events since this seat's last decision.
LEGACY_FEATURES = 9 + 25 + 5 * 10 + 5 * 20 + 2 * 37
FEATURES = LEGACY_FEATURES + SLOTS * IDENTITIES
RATE = 0.003
BETA1, BETA2 = 0.9, 0.999
CLIP = 5.0


def action_id(action):
    """play[0:5], discard[5:10], rank clues[10:15], color clues[15:20]."""
    kind = action["type"]
    if kind == "play":
        return action["index"]
    if kind == "discard":
        return 5 + action["index"]
    if kind == "hint":
        if action["kind"] == "rank":
            return 10 + RANKS.index(action["value"])
        if action["kind"] == "color":
            return 15 + COLORS.index(action["value"])
    raise ValueError("Unknown action")


def _knowledge(slot):
    return [c in slot["colors"] for c in COLORS] + [r in slot["ranks"] for r in RANKS]


def _prior(view):
    beliefs = view["beliefs"] if "beliefs" in view else hand_beliefs(view)
    prior = np.zeros((len(beliefs), IDENTITIES))
    for row, slot in zip(prior, beliefs):
        row[slot["possible"]] = slot["probabilities"]
    return prior


def encode(view, prior=None, *, legacy=False):
    """Encode only the acting player's view. Newer slots occur last."""
    values = [view["fireworks"][c] / 5 for c in COLORS]
    values.extend((view["clues"] / 8, view["lives"] / 3, view["remaining"] / 40,
                   (view["final_turns"] or 0) / 2))
    discards = [0] * 25
    for card in view["discard"]:
        idx = 5 * COLORS.index(card["color"]) + card["rank"] - 1
        discards[idx] += 1 / COPIES[card["rank"]]
    values.extend(discards)
    for slot in view["hand"]:
        values.extend(_knowledge(slot))
    values.extend([0] * (10 * (5 - len(view["hand"]))))
    for slot in view["partner"]:
        values.extend(c == slot["color"] for c in COLORS)
        values.extend(r == slot["rank"] for r in RANKS)
        values.extend(_knowledge(slot["knowledge"]))
    values.extend([0] * (20 * (5 - len(view["partner"]))))

    if not legacy:
        if prior is None:
            prior = _prior(view)
        values.extend(prior.ravel())
        values.extend([0] * ((SLOTS - len(prior)) * IDENTITIES))

    events = view["history"][-2:]
    values.extend([0] * (37 * (2 - len(events))))
    for event in events:
        features = [0] * 37
        features[action_id(event)] = 1
        if "card" in event:
            features[20 + COLORS.index(event["card"]["color"])] = 1
            features[25 + RANKS.index(event["card"]["rank"])] = 1
        for slot in event.get("matched", ()):
            features[30 + slot] = 1
        features[35] = event.get("success", False)
        features[36] = event["player"] != view["seat"]
        values.extend(features)
    return np.asarray(values, dtype=np.float64)


@dataclass(frozen=True, slots=True)
class Decision:
    observation: np.ndarray
    previous: np.ndarray
    hidden: np.ndarray
    probabilities: np.ndarray
    choice: int
    prior: np.ndarray | None
    prediction: np.ndarray | None


class PredictionMetrics:
    """Per-slot top-1 and log loss on exactly the same pre-action hands."""

    def __init__(self):
        self.decisions = self.slots = 0
        self.correct = np.zeros(2)
        self.loss = np.zeros(2)

    def add(self, decision, labels):
        self.decisions += 1
        self.slots += len(labels)
        for i, probs in enumerate((decision.prediction, decision.prior)):
            if probs.shape != (len(labels), IDENTITIES):
                raise ValueError("Prediction and live hand lengths differ")
            correct = probs[np.arange(len(labels)), labels]
            if i == 1 and np.any(correct <= 0):
                raise ValueError("Actual card excluded by public deductions")
            self.correct[i] += np.count_nonzero(probs.argmax(axis=1) == labels)
            self.loss[i] -= np.log(np.maximum(correct, np.finfo(float).tiny)).sum()

    def report(self):
        report = {"decisions": self.decisions, "slots": self.slots}
        for i, name in enumerate(("learned", "deduction_prior")):
            report[name] = {
                "top1_accuracy": float(self.correct[i] / self.slots) if self.slots else None,
                "cross_entropy_nats": float(self.loss[i] / self.slots) if self.slots else None,
            }
        return report


class Learner:
    """Shared recurrent actor. The caller owns and resets both seat memories."""

    format = FORMAT
    features = FEATURES

    def __init__(self, seed):
        rng = np.random.default_rng(seed)
        self.weights = {
            "input": rng.normal(0, self.features ** -0.5, (HIDDEN, self.features)),
            "recurrent": rng.normal(0, 0.65 * HIDDEN ** -0.5, (HIDDEN, HIDDEN)),
            "hidden_bias": np.zeros(HIDDEN),
            "output": rng.normal(0, 0.1 * HIDDEN ** -0.5, (ACTIONS, HIDDEN)),
            "output_bias": np.zeros(ACTIONS),
        }
        if self.format == FORMAT:
            self.weights["card_output"] = rng.normal(0, .01 * HIDDEN ** -.5, (SLOTS * IDENTITIES, HIDDEN))
            self.weights["card_bias"] = np.zeros(SLOTS * IDENTITIES)
        self.first = {k: np.zeros_like(v) for k, v in self.weights.items()}
        self.second = {k: np.zeros_like(v) for k, v in self.weights.items()}
        self.updates = 0
        self.baseline = 0.0

    @classmethod
    def config(cls):
        config = {
            "format": cls.format, "observation": "public-seat-v1", "actions": "slot-rank-color-v1",
            "features": cls.features, "hidden": HIDDEN, "activation": "tanh",
            "initialization": "normal-fanin-v1", "algorithm": "episodic-reinforce-bptt",
            "discount": 1.0, "baseline": "prior-batch-return-to-go-ema-0.9",
            "optimizer": "adam", "learning_rate": RATE, "betas": [BETA1, BETA2],
            "gradient_norm_clip": CLIP, "inference": "sample-legal-softmax",
        }
        if cls.format == FORMAT:
            config.update(observation="public-seat-with-hand-marginals-v2",
                          auxiliary="pre-action-mean-log-likelihood-per-occupied-slot",
                          auxiliary_weight=AUXILIARY_WEIGHT, card_prediction="log-prior-plus-residual-softmax")
        return config

    @staticmethod
    def initial_state():
        return np.zeros(HIDDEN)

    def _forward(self, observation, previous, ids):
        w = self.weights
        hidden = np.tanh(w["input"] @ observation + w["recurrent"] @ previous + w["hidden_bias"])
        logits = (w["output"] @ hidden + w["output_bias"])[ids]
        legal = np.exp(logits - logits.max())
        probabilities = np.zeros(ACTIONS)
        probabilities[ids] = legal / legal.sum()
        return hidden, probabilities

    def act(self, view, hidden, rng):
        legal = view["legal"]
        if not legal:
            raise ValueError("No legal actions for this seat")
        ids = [action_id(move) for move in legal]
        legacy = self.format == LEGACY_FORMAT
        prior = None if legacy else _prior(view)
        observation = encode(view, prior, legacy=legacy)
        state, probabilities = self._forward(observation, hidden, ids)
        choice = int(rng.choice(ACTIONS, p=probabilities))
        prediction = None if legacy else self._predict(state, prior)
        return legal[ids.index(choice)], state, Decision(observation, hidden, state, probabilities,
                                                         choice, prior, prediction)

    def _predict(self, hidden, prior):
        # Starting from the exact public marginal lets the head use action/history
        # signals while keeping ruled-out cards at exactly zero probability.
        residual = self.weights["card_output"] @ hidden + self.weights["card_bias"]
        logits = np.full_like(prior, -np.inf)
        possible = prior > 0
        logits[possible] = np.log(prior[possible]) + residual.reshape(SLOTS, IDENTITIES)[:len(prior)][possible]
        mass = np.exp(logits - logits.max(axis=1, keepdims=True))
        return mass / mass.sum(axis=1, keepdims=True)

    def _gradients(self, episodes):
        records, outputs, units, cards = [], [], [], []
        sum_returns = 0.0
        for episode in episodes:
            future = [self.initial_state(), self.initial_state()]
            team_return = 0.0
            for step in reversed(episode):
                seat, decision, reward = step[:3]
                team_return += reward
                sum_returns += team_return
                advantage = team_return - self.baseline
                # Gradient ascent on advantage * log pi(a | player's history).
                dlogit = -decision.probabilities * advantage
                dlogit[decision.choice] += advantage
                dh = self.weights["output"].T @ dlogit + future[seat]
                if self.format == FORMAT:
                    labels = step[3]
                    count = len(labels)
                    if decision.prediction.shape != (count, IDENTITIES) or count == 0:
                        raise ValueError("Expected pre-action labels for every occupied slot")
                    if np.any(decision.prior[np.arange(count), labels] <= 0):
                        raise ValueError("Card label inconsistent with the acting seat's view")
                    delta = np.zeros((SLOTS, IDENTITIES))
                    delta[:count] = -decision.prediction
                    delta[np.arange(count), labels] += 1
                    dcard = delta.ravel() * (AUXILIARY_WEIGHT / count)
                    dh += self.weights["card_output"].T @ dcard
                    cards.append(dcard)
                dz = dh * (1 - decision.hidden ** 2)
                future[seat] = self.weights["recurrent"].T @ dz
                records.append(decision)
                outputs.append(dlogit)
                units.append(dz)

        if not records or any(not episode for episode in episodes):
            raise ValueError("Expected a batch of nonempty complete episodes")
        out = np.stack(outputs)
        dz = np.stack(units)
        gradients = {
            "input": dz.T @ np.stack([r.observation for r in records]),
            "recurrent": dz.T @ np.stack([r.previous for r in records]),
            "hidden_bias": dz.sum(axis=0),
            "output": out.T @ np.stack([r.hidden for r in records]),
            "output_bias": out.sum(axis=0),
        }
        if cards:
            dc = np.stack(cards)
            gradients["card_output"] = dc.T @ np.stack([r.hidden for r in records])
            gradients["card_bias"] = dc.sum(axis=0)
        return {k: g / len(episodes) for k, g in gradients.items()}, sum_returns / len(records)

    def update(self, episodes):
        """Apply one update from complete games sampled with the current weights."""
        gradients, mean_return = self._gradients(episodes)
        norm = np.sqrt(sum(np.sum(g * g) for g in gradients.values()))
        scale = CLIP / norm if norm > CLIP else 1.0
        self.updates += 1
        for key, gradient in gradients.items():
            gradient *= scale
            self.first[key] *= BETA1
            self.first[key] += (1 - BETA1) * gradient
            self.second[key] *= BETA2
            self.second[key] += (1 - BETA2) * gradient ** 2
            mean = self.first[key] / (1 - BETA1 ** self.updates)
            variance = self.second[key] / (1 - BETA2 ** self.updates)
            self.weights[key] += RATE * mean / (np.sqrt(variance) + 1e-8)
        self.baseline = 0.9 * self.baseline + 0.1 * mean_return

    def save(self, path):
        """Replace the destination atomically after all arrays are written."""
        path = Path(path)
        data = {"format": self.format, "baseline": self.baseline, "updates": self.updates}
        for group, arrays in (("weights", self.weights), ("first", self.first), ("second", self.second)):
            data.update({f"{group}_{key}": value for key, value in arrays.items()})
        with NamedTemporaryFile(dir=path.parent, delete=False) as temp:
            staged = Path(temp.name)
        try:
            with staged.open("wb") as stream:
                np.savez_compressed(stream, **data)
            staged.replace(path)
        finally:
            staged.unlink(missing_ok=True)

    @classmethod
    def load(cls, path):
        with np.load(path, allow_pickle=False) as data:
            format_name = str(data["format"].item())
            if format_name not in (FORMAT, LEGACY_FORMAT):
                raise ValueError("Unknown learner checkpoint format")
            model = _LegacyLearner(0) if format_name == LEGACY_FORMAT else cls(0)
            for group, arrays in (("weights", model.weights), ("first", model.first), ("second", model.second)):
                for key, template in arrays.items():
                    array = data[f"{group}_{key}"]
                    if array.shape != template.shape or array.dtype != template.dtype or not np.isfinite(array).all():
                        raise ValueError(f"Invalid checkpoint array {group}_{key}")
                    arrays[key] = array
            model.baseline = float(data["baseline"].item())
            model.updates = int(data["updates"].item())
            if not np.isfinite(model.baseline) or model.updates < 0:
                raise ValueError("Invalid checkpoint training state")
        return model


class _LegacyLearner(Learner):
    """Read and run existing checkpoints with their original feature layout."""

    format = LEGACY_FORMAT
    features = LEGACY_FEATURES
