"""Exact literal counterweight for the offline convention-gap diagnostic."""

from collections import Counter
from dataclasses import dataclass
from fractions import Fraction

from .game import COLORS, COPIES, RANKS


def _weighted_counts(copies, allowed, index, playable):
    """Return (failed, total) assignments of distinct physical copies to all slots.

    The state mask records which hand slots are filled. On visiting a copy, either
    leave it in the deck or add it to one allowed empty slot. Iterate masks in
    descending order so a copy cannot be assigned twice. Visiting indistinguishable
    identities as distinct copies gives precisely the falling-factorial weights.
    """
    full = (1 << len(allowed)) - 1
    totals = [1] + [0] * full
    failures = [0] * (full + 1)
    played_slot = 1 << index
    for card, count in copies.items():
        slots = sum(1 << i for i, options in enumerate(allowed) if card in options)
        if not slots:
            continue
        fails = card not in playable
        for _ in range(count):
            for mask in range(full - 1, -1, -1):
                ways = totals[mask]
                if not ways:
                    continue
                empty = slots & ~mask
                while empty:
                    bit = empty & -empty
                    empty ^= bit
                    new = mask | bit
                    totals[new] += ways
                    failures[new] += ways if fails and bit == played_slot else failures[mask]
    return failures[full], totals[full]


def literal_failure_probability(view, index):
    """Exact conditional risk of playing one slot, without a model of partner intent.

    Only pre-move public counts, the visible other hand, and the acting seat's
    accumulated positive and negative clue constraints enter this calculation.
    Neither the move history nor policy identity is used to guess what a hint meant.
    """
    if type(index) is not int or not 0 <= index < len(view["hand"]):
        raise ValueError("Invalid hand slot.")
    heights = view["fireworks"]
    visible = Counter((card["color"], card["rank"]) for card in (*view["partner"], *view["discard"]))
    copies = {(c, r): COPIES[r] - visible[c, r] - int(r <= heights[c]) for c in COLORS for r in RANKS}
    if min(copies.values()) < 0:
        raise ValueError("Visible cards exceed deck multiplicities.")
    allowed = [{(c, r) for c in slot["colors"] for r in slot["ranks"]} for slot in view["hand"]]
    playable = {(c, heights[c] + 1) for c in COLORS}
    bad, total = _weighted_counts(copies, allowed, index, playable)
    if not total:
        raise ValueError("No possible hand satisfies the observed clues.")
    return Fraction(bad, total)


@dataclass
class PlayTally:
    """Play-weighted sums; a no-play group has no rate or gap."""
    turns: int = 0
    plays: int = 0
    failures: int = 0
    literal_risk_sum: float = 0.0
    literal_safe_plays: int = 0

    def add(self, risk, failed):
        if (risk == 0 and failed) or (risk == 1 and not failed):
            raise RuntimeError("Observed outcome contradicts literal support.")
        self.plays += 1
        self.failures += int(failed)
        self.literal_risk_sum += float(risk)
        self.literal_safe_plays += int(risk == 0)

    def report(self):
        risk = (self.literal_risk_sum / self.plays) if self.plays else None
        actual = (self.failures / self.plays) if self.plays else None
        risky = self.plays - self.literal_safe_plays
        return {"turns": self.turns, "plays": self.plays, "failures": self.failures,
                "play_rate": (self.plays / self.turns) if self.turns else None,
                "literal_safe_plays": self.literal_safe_plays,
                "literal_safe_share": (self.literal_safe_plays / self.plays) if self.plays else None,
                "nonzero_risk_plays": risky,
                "nonzero_risk_gap_pp": (self.literal_risk_sum - self.failures) * 100 / risky if risky else None,
                "literal_failure_rate": risk, "actual_failure_rate": actual,
                "gap_pp": (risk - actual) * 100 if self.plays else None}
