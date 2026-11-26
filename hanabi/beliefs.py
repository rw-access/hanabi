"""Exact own-hand deductions from literal clues and the observing seat's visible cards."""

from functools import lru_cache
from math import perm

from .game import COLORS, COPIES, RANKS


_COLOR_OFFSET = {color: i * len(RANKS) for i, color in enumerate(COLORS)}
_DECK_COUNTS = tuple(COPIES[rank] for _ in COLORS for rank in RANKS)


def identity_weights(belief):
    """Map a slot's compact belief to the (color, rank) keys used by policies."""
    weights = {}
    for identity, probability in zip(belief["possible"], belief["probabilities"]):
        color, rank = divmod(identity, len(RANKS))
        weights[COLORS[color], RANKS[rank]] = probability
    return weights


@lru_cache(maxsize=32)
def _subsets(mask):
    """All nonempty subsets of a mask of up to five hand positions."""
    subsets = []
    subset = mask
    while subset:
        subsets.append(subset)
        subset = (subset - 1) & mask
    return tuple(subsets)


@lru_cache(maxsize=256)
def _marginal_weights(counts, allowed_slots, hand_size):
    """Count distinct assignments of physical copies, keeping hand positions ordered."""
    full = (1 << hand_size) - 1
    groups = {}
    for count, slots in zip(counts, allowed_slots):
        if slots and count:
            groups[slots] = groups.get(slots, 0) + count

    # Before any distinguishing clue, the slots are exchangeable.
    if len(groups) == 1 and groups.get(full, 0) >= hand_size:
        weights = tuple(count if slots else 0 for count, slots in zip(counts, allowed_slots))
        return (weights,) * hand_size

    # ways[S] counts assignments to exactly the positions in S. For each group
    # of mutually interchangeable cards, assign k distinct copies to k positions
    # in order. Descending masks keep the smaller source counts from the prior group.
    ways = [0] * (full + 1)
    ways[0] = 1
    for slots, count in groups.items():
        ordered = [perm(count, k) for k in range(hand_size + 1)]
        for filled in range(full, 0, -1):
            for subset in _subsets(filled & slots):
                ways[filled] += ordered[subset.bit_count()] * ways[filled ^ subset]
    if not ways[full]:
        raise ValueError("No hand is consistent with these visible cards and clues")

    # Remove one particular copy. Each assignment either omits this copy or
    # uses it in exactly one eligible position. With a copy reserved for a
    # given slot, the other slots may take any assignment in 'without'.
    completions = {}
    for slots in groups:
        without = ways.copy()
        for filled in range(1, full):
            eligible = filled & slots
            while eligible:
                bit = eligible & -eligible
                without[filled] -= without[filled ^ bit]
                eligible ^= bit
        completions[slots] = [without[full ^ (1 << i)] for i in range(hand_size)]

    return tuple(tuple(count * completions[slots][i] if count and slots & (1 << i) else 0
                       for count, slots in zip(counts, allowed_slots))
                 for i in range(hand_size))


def hand_beliefs(view):
    """Possible identities and their copy-weighted probabilities for each own slot.

    An identity is its index in color-major, rank-minor order (red 1 = 0).
    The distribution is uniform over physical deals consistent with the public
    board, partner faces, and literal clues. The choice of a clue or convention
    may communicate more information than these constraints alone.
    """
    hand = view["hand"]
    if not hand:
        return []
    counts = list(_DECK_COUNTS)
    for color, height in view["fireworks"].items():
        for rank in range(1, height + 1):
            counts[_COLOR_OFFSET[color] + rank - 1] -= 1
    for visible in (view["partner"], view["discard"]):
        for card in visible:
            counts[_COLOR_OFFSET[card["color"]] + card["rank"] - 1] -= 1
    if min(counts) < 0:
        raise ValueError("Visible cards exceed the deck's copy counts")

    allowed_slots = [0] * len(counts)
    for i, knowledge in enumerate(hand):
        for color in knowledge["colors"]:
            for rank in knowledge["ranks"]:
                allowed_slots[_COLOR_OFFSET[color] + rank - 1] |= 1 << i

    result = []
    for weights in _marginal_weights(tuple(counts), tuple(allowed_slots), len(hand)):
        total = sum(weights)
        possible = [identity for identity, weight in enumerate(weights) if weight]
        result.append({"possible": possible, "probabilities": [weights[j] / total for j in possible]})
    return result
