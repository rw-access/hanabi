"""Explainable teammates that only consume the acting seat's observation."""

from collections import Counter
from random import Random

from .beliefs import identity_weights
from .game import COLORS, COPIES, RANKS
from .group_policy import choose_group


POLICIES = {
    "lantern": "Counts visible copies and gives literal, actionable clues.",
    "literal": "Plays only when directly supported by clues and public stacks.",
    "spark": "Takes calculated risks when the team has fuses to spare.",
    "random": "Chooses uniformly from legal actions (evaluation control).",
    "group": "Uses the written newest-first focus and chop conventions.",
    "adaptive": "Uses Group until a fuse is lost, then plays Lantern for the rest of the deal.",
}


def _card(card):
    return card["color"], card["rank"]


def _hint_update(knowledge, hint, match):
    kind, value = hint["kind"], hint["value"]
    key = "colors" if kind == "color" else "ranks"
    return {
        **knowledge,
        key: [x for x in knowledge[key] if (x == value) == match],
    }


def _belief(knowledge, remaining):
    """Compatible single-card copies, not a joint posterior over the whole hand."""
    return {
        (c, r): remaining[c, r]
        for c in knowledge["colors"]
        for r in knowledge["ranks"]
        if remaining[c, r] > 0
    }


def _probability(belief, predicate):
    return sum(n for card, n in belief.items() if predicate(card)) / sum(belief.values()) if belief else 0


def choose(view, policy="lantern", rng=None):
    """Pick a legal move from a fresh game.view(seat), without private state."""
    if policy not in POLICIES:
        raise ValueError(f"Unknown policy: {policy}")
    legal = view["legal"]
    if not legal:
        raise ValueError("No legal moves for this player")
    if policy == "adaptive":
        policy = "group" if view["lives"] == 3 else "lantern"
    if policy == "group":
        return choose_group(view)
    if policy == "random":
        return (rng if rng is not None else Random()).choice(legal)

    heights = view["fireworks"]
    discarded = Counter(_card(c) for c in view["discard"])
    unseen = {
        (c, r): COPIES[r] - discarded[c, r] - int(r <= heights[c])
        for c in COLORS
        for r in RANKS
    }
    visible_partner = Counter(_card(c) for c in view["partner"])

    def playable(card):
        c, r = card
        return r == heights[c] + 1

    def dead(card):
        c, r = card
        return r <= heights[c] or any(
            unseen[c, v] == 0 for v in range(heights[c] + 1, r)
        )

    def critical(card):
        return not dead(card) and unseen[card] == 1

    def recoverable(card):
        return not dead(card) and unseen[card] > 1

    # We know the other hand when inferring ours. To value a clue, we
    # conservatively model the partner's knowledge without guessing our hand.
    all_types = {card: 1 for card in unseen}
    our_copies = {card: count - visible_partner[card] for card, count in unseen.items()}
    partner_copies = unseen
    if policy == "literal":
        our_copies = partner_copies = all_types
    if "beliefs" in view and policy != "literal":
        own = [identity_weights(slot) for slot in view["beliefs"]]
    else:
        own = [_belief(k, our_copies) for k in view["hand"]]
    partner = view["partner"]
    before = [_belief(c["knowledge"], partner_copies) for c in partner]
    has_partner_play = any(_probability(b, playable) == 1 for b in before)

    play_options = [a for a in legal if a["type"] == "play"]
    probability = {
        a["index"]: _probability(own[a["index"]], playable) for a in play_options
    }
    if play_options:
        best = max(
            play_options,
            key=lambda a: (
                probability[a["index"]],
                _probability(own[a["index"]], lambda c: c[1] == 5),
                -a["index"],
            ),
        )
        p = probability[best["index"]]
        if p == 1:
            return best
        lives = view["lives"]
        if policy == "spark" and lives > 1 and p >= (0.74 if lives == 3 else 0.88):
            return best
        # After the countdown begins, these are the last chances to
        # use a spare fuse. Don't play blindly when a clue could help first.
        if policy != "literal" and lives > 1 and (
            (view["final_turns"] == 1 and p > 0)
            or (view["final_turns"] == 2 and (p >= 0.5 or has_partner_play) and p > 0)
        ):
            return best

    def clue_value(hint):
        kind, value = hint["kind"], hint["value"]
        new_plays = 0
        preparation, info = 0.0, 0.0
        previously_known = 0
        for card, old in zip(partner, before):
            ident = _card(card)
            match = card[kind] == value
            k = _hint_update(card["knowledge"], hint, match)
            after = _belief(k, partner_copies)
            if old == after or not after:
                continue
            old_p = _probability(old, playable)
            new_p = _probability(after, playable)
            if old_p < 1 and new_p == 1:
                new_plays += 1
            elif playable(ident) and new_p > old_p:
                preparation += (new_p - old_p) * 4
            elif critical(ident) and match:
                preparation += 0.85
            if old_p == 1:
                previously_known += 1
            info += 1 - len(after) / len(old)
        if has_partner_play:
            # Our partner can act already: pay only for meaningful future moves.
            preparation *= 0.7
        if view["final_turns"] == 1:
            return 0.0
        return 7 * new_plays + preparation + 0.3 * info - 0.2 * previously_known

    def discard_value(action):
        idx = action["index"]
        b = own[idx]
        safe = _probability(b, dead)
        duplicate = _probability(b, lambda c: recoverable(c) and visible_partner[c] > 0)
        irreversible = _probability(b, critical)
        next_up = probability[idx]
        k = view["hand"][idx]
        fresh = len(k["colors"]) == len(COLORS) and len(k["ranks"]) == len(RANKS)
        urgency = 0.8 if view["clues"] <= 1 else 0
        return (
            1.4 + urgency + 3.2 * safe + 2.0 * duplicate
            - 2.6 * irreversible - 1.5 * next_up + 0.4 * fresh
        )

    alternatives = [a for a in legal if a["type"] in ("hint", "discard")]
    if alternatives:
        return max(
            alternatives,
            key=lambda a: clue_value(a) if a["type"] == "hint" else discard_value(a),
        )
    return best
