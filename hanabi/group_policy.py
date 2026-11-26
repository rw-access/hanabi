"""A deterministic two-seat interpretation of the group's written conventions."""

from collections import Counter
from dataclasses import dataclass

from .beliefs import identity_weights
from .game import COLORS, COPIES, RANKS


_INITIAL_HAND = 5
_INITIAL_DRAWS = len(COLORS) * sum(COPIES.values()) - 2 * _INITIAL_HAND


@dataclass
class Mark:
    painted: bool = False
    play: frozenset | None = None


def _chop(marks):
    # The engine stores oldest first. The group's visual convention puts it rightmost.
    return next((i for i, slot in enumerate(marks) if not slot.painted), None)


def _meaning(hint, matched, marks, heights):
    """Classify against the receiver's *pre-clue* chop, before painting anything."""
    kind, value = hint["kind"], hint["value"]
    chop = _chop(marks)
    if kind == "rank" and chop in matched:
        return chop, None
    focus = max(matched)  # Newest touched is the group's visual leftmost.
    if kind == "color":
        return focus, frozenset(
            ((value, heights[value] + 1),) if heights[value] < 5 else ()
        )
    return focus, frozenset((c, value) for c in COLORS if heights[c] + 1 == value)


def _replay_marks(history):
    """Replay public positive touches and index shifts, never identities of draws."""
    hands = [[Mark() for _ in range(_INITIAL_HAND)] for _ in range(2)]
    heights = dict.fromkeys(COLORS, 0)
    draws = _INITIAL_DRAWS
    for event in history:
        actor = event["player"]
        if event["type"] == "hint":
            receiver = hands[1 - actor]
            matched = event["matched"]
            if not matched:
                continue  # An empty clue has no conventional play or save focus.
            focus, play = _meaning(event, matched, receiver, heights)
            for i in matched:
                receiver[i].painted = True
            if play is not None:
                receiver[focus].play = play
            continue

        hands[actor].pop(event["index"])
        if draws:
            draws -= 1
            hands[actor].append(Mark())
        if event["type"] == "play" and event["success"]:
            card = event["card"]  # Already revealed in the public discard/play log.
            heights[card["color"]] = card["rank"]
    return hands


def _identity(card):
    return card["color"], card["rank"]


def _possibilities(knowledge, copies):
    return {(c, r) for c in knowledge["colors"] for r in knowledge["ranks"]
            if copies[c, r] > 0}


def _narrow(options, hint, touched):
    field = 0 if hint["kind"] == "color" else 1
    return {card for card in options if (card[field] == hint["value"]) == touched}


def _known_play(options, mark, playable):
    if not options:
        return False
    if options <= playable:
        return True
    if mark.play is None:
        return False
    intended = options & mark.play
    # Keep the rank implied when the clue was given; a later stack advance
    # must not silently translate "play red 1" into "play red 2".
    return bool(intended) and intended <= playable


def _public_copies(view):
    discard = Counter(_identity(c) for c in view["discard"])
    return {(c, r): COPIES[r] - discard[c, r] - int(r <= view["fireworks"][c])
            for c in COLORS for r in RANKS}


def partner_clue(view):
    """Find a clue worth giving before Adaptive spends its turn on its own hand."""
    hints = [a for a in view["legal"] if a["type"] == "hint"]
    if not hints or view["final_turns"] == 1:
        return None
    heights = view["fireworks"]
    playable = {(c, h + 1) for c, h in heights.items() if h < 5}
    copies = _public_copies(view)
    partner = view["partner"]
    before = [_possibilities(c["knowledge"], copies) for c in partner]
    # A convention signal isn't proof for a human who plays by literal clues.
    if any(options and options <= playable for options in before):
        return None

    marks = _replay_marks(view["history"])[1 - view["seat"]]
    chop = _chop(marks)
    at_risk = None
    if chop is not None and view["final_turns"] is None:
        c, r = _identity(partner[chop])
        if copies[c, r] == 1 and r > heights[c] and all(copies[c, n] for n in range(heights[c] + 1, r)):
            at_risk = chop

    best, best_value = None, None
    for hint in hints:
        touched = {i for i, card in enumerate(partner) if card[hint["kind"]] == hint["value"]}
        after = [_narrow(options, hint, i in touched) for i, options in enumerate(before)]
        safe = {i for i, options in enumerate(after) if options and options <= playable}
        saves = at_risk in touched and hint["kind"] == "rank" and after[at_risk] != before[at_risk]
        if not safe and not saves:
            continue
        if touched and view["lives"] == 3:
            focus, intended = _meaning(hint, touched, marks, heights)
            if intended and intended & after[focus] and _identity(partner[focus]) not in intended:
                continue  # Avoid sending a conflicting play signal on another card.
        value = (bool(safe), saves, len(safe), len(safe & touched),
                 sum(len(old) - len(new) for old, new in zip(before, after)))
        if best_value is None or value > best_value:
            best, best_value = hint, value
    return best


def choose_group(view):
    """Pick from legal actions using only the acting seat's public-view JSON."""
    legal = view["legal"]
    seat = view["seat"]
    # Reconstruct only paint and signal on live positions (not identities).
    marks = _replay_marks(view["history"])
    own_marks, partner_marks = marks[seat], marks[1 - seat]
    partner = view["partner"]
    heights = view["fireworks"]
    playable = {(c, h + 1) for c, h in heights.items() if h < 5}
    public_copies = _public_copies(view)
    visible = Counter(_identity(c) for c in partner)
    own_copies = {card: count - visible[card] for card, count in public_copies.items()}
    if "beliefs" in view:
        own_weights = [identity_weights(slot) for slot in view["beliefs"]]
    else:
        own_weights = [{card: own_copies[card] for card in _possibilities(k, own_copies)}
                       for k in view["hand"]]
    own = [set(weights) for weights in own_weights]

    def dead(card):
        c, r = card
        return r <= heights[c] or any(
            public_copies[c, v] == 0 for v in range(heights[c] + 1, r)
        )

    def last_useful_copy(card):
        return public_copies[card] == 1 and not dead(card)

    # True literal certainty remains valid independently of any signal.
    # Prefer a definite 5 (recovers a clue), otherwise oldest in that class.
    known = [a for a in legal if a["type"] == "play"
             and _known_play(own[a["index"]], own_marks[a["index"]], playable)]
    if known:
        return max(known, key=lambda a: (
            all(r == 5 for _, r in own[a["index"]]),
            own[a["index"]] <= playable,
            -a["index"],
        ))

    # The giver cannot see its own hand, so approximates partner beliefs using
    # public copies only, not the partner's true private-view copy counts.
    partner_options = [_possibilities(c["knowledge"], public_copies) for c in partner]
    partner_has_play = any(_known_play(options, mark, playable)
                           for options, mark in zip(partner_options, partner_marks))
    hints = [a for a in legal if a["type"] == "hint"]
    ranked = []
    empty = []
    for order, hint in enumerate(hints):
        touched = {i for i, card in enumerate(partner) if card[hint["kind"]] == hint["value"]}
        if not touched:
            # Estimate what this literal exclusion establishes using information
            # available to the giver, without borrowing the receiver's private view.
            narrowed = [_narrow(options, hint, False) for options in partner_options]
            new_plays = sum(bool(after) and after <= playable and not before <= playable
                            for before, after in zip(partner_options, narrowed))
            eliminated = sum(len(before) - len(after) for before, after in zip(partner_options, narrowed))
            empty.append(((new_plays, eliminated, -order), hint))
            continue
        focus, play = _meaning(hint, touched, partner_marks, heights)
        actual = _identity(partner[focus])
        new_bystanders = sum(not partner_marks[i].painted for i in touched - {focus})
        compatible = _narrow(partner_options[focus], hint, True)
        urgent_save = play is None and last_useful_copy(actual)
        immediate_play = play is not None and actual in play
        # Same board/rank ties: spend a color before an equivalent number,
        # prefer refilling a clue with a 5, and paint fewer new bystanders.
        value = (hint["kind"] == "color", actual[1] == 5, -new_bystanders, -order)
        # At the clue cap a discard is prohibited. Never knowingly give a
        # false play signal, even then. A rank-to-chop is nonactionable;
        # otherwise the implication must be honest or impossible literally.
        literal_blocks_signal = play is not None and not (compatible & play)
        safe_hint = play is None or immediate_play or literal_blocks_signal
        neutral = (play is None and not dead(actual), literal_blocks_signal,
                   immediate_play, -new_bystanders, -order)
        ranked.append((hint, urgent_save, immediate_play, value,
                       neutral if safe_hint else None))

    if view["final_turns"] != 1 and not partner_has_play:
        saves = [row for row in ranked if row[1]]
        plays = [row for row in ranked if row[2]]
        if saves and view["final_turns"] is None:
            return max(saves, key=lambda row: row[3])[0]
        if plays:
            return max(plays, key=lambda row: row[3])[0]
        if any(priority[0] for priority, _ in empty):
            return max(empty, key=lambda row: row[0])[1]

    discards = [a for a in legal if a["type"] == "discard"]
    if discards:
        chop = _chop(own_marks)
        if chop is not None:
            return next(a for a in discards if a["index"] == chop)
        # All cards are painted: there is no conventional chop. Prefer a
        # proven dead card and otherwise protect likely last usable copies.
        def discard_value(a):
            i = a["index"]
            weights = own_weights[i]
            cards = own[i]
            mass = sum(weights.values()) or 1
            return (sum(weights[c] for c in cards if dead(c)) / mass,
                    -sum(weights[c] for c in cards if last_useful_copy(c)) / mass, -i)

        return max(discards, key=discard_value)

    forced = [row for row in ranked if row[4] is not None]
    if forced:
        return max(forced, key=lambda row: row[4])[0]
    if empty:
        return max(empty, key=lambda row: row[0])[1]
    # At the clue cap with no chop, the newest already painted card has
    # at least one known attribute. Cluing its other attribute focuses
    # that card and either honestly signals play or leaves no such inference.
    raise RuntimeError("No legal group response to this observation")
