"""Optional NumPy recurrent policy for two-seat, on-policy self-play."""

from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np

from .game import COLORS, COPIES, RANKS


FORMAT = "hanabi-recurrent-reinforce-v1"
ACTIONS = 20
HIDDEN = 32
# Board and resources, discards by identity, five own and five partner slots,
# and two public events since this seat's last decision.
FEATURES = 9 + 25 + 5 * 10 + 5 * 20 + 2 * 37
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


def encode(view):
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


class Learner:
    """Shared recurrent actor. The caller owns and resets both seat memories."""

    def __init__(self, seed):
        rng = np.random.default_rng(seed)
        self.weights = {
            "input": rng.normal(0, FEATURES ** -0.5, (HIDDEN, FEATURES)),
            "recurrent": rng.normal(0, 0.65 * HIDDEN ** -0.5, (HIDDEN, HIDDEN)),
            "hidden_bias": np.zeros(HIDDEN),
            "output": rng.normal(0, 0.1 * HIDDEN ** -0.5, (ACTIONS, HIDDEN)),
            "output_bias": np.zeros(ACTIONS),
        }
        self.first = {k: np.zeros_like(v) for k, v in self.weights.items()}
        self.second = {k: np.zeros_like(v) for k, v in self.weights.items()}
        self.updates = 0
        self.baseline = 0.0

    @staticmethod
    def config():
        return {
            "format": FORMAT, "observation": "public-seat-v1", "actions": "slot-rank-color-v1",
            "features": FEATURES, "hidden": HIDDEN, "activation": "tanh",
            "initialization": "normal-fanin-v1", "algorithm": "episodic-reinforce-bptt",
            "discount": 1.0, "baseline": "prior-batch-return-to-go-ema-0.9",
            "optimizer": "adam", "learning_rate": RATE, "betas": [BETA1, BETA2],
            "gradient_norm_clip": CLIP, "inference": "sample-legal-softmax",
        }

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
        observation = encode(view)
        state, probabilities = self._forward(observation, hidden, ids)
        choice = int(rng.choice(ACTIONS, p=probabilities))
        return legal[ids.index(choice)], state, Decision(observation, hidden, state, probabilities, choice)

    def _gradients(self, episodes):
        records, outputs, units = [], [], []
        sum_returns = 0.0
        for episode in episodes:
            future = [self.initial_state(), self.initial_state()]
            team_return = 0.0
            for seat, decision, reward in reversed(episode):
                team_return += reward
                sum_returns += team_return
                advantage = team_return - self.baseline
                # Gradient ascent on advantage * log pi(a | player's history).
                dlogit = -decision.probabilities * advantage
                dlogit[decision.choice] += advantage
                dh = self.weights["output"].T @ dlogit + future[seat]
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
        data = {"format": FORMAT, "baseline": self.baseline, "updates": self.updates}
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
        model = cls(0)
        with np.load(path, allow_pickle=False) as data:
            if str(data["format"].item()) != FORMAT:
                raise ValueError("Unknown learner checkpoint format")
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
