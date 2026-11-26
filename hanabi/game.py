"""Two-seat, five-color Hanabi. Game is private; only view(seat) crosses a player boundary."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from random import Random, SystemRandom

COLORS = ("red", "yellow", "green", "blue", "white")
RANKS = (1, 2, 3, 4, 5)
COPIES = {1: 3, 2: 2, 3: 2, 4: 2, 5: 1}
MAX_CLUES = 8


@dataclass(frozen=True, slots=True)
class Card:
    color: str
    rank: int

    def visible(self):
        return {"color": self.color, "rank": self.rank}


@dataclass(slots=True)
class Slot:
    card: Card
    colors: set[str] = field(default_factory=lambda: set(COLORS))
    ranks: set[int] = field(default_factory=lambda: set(RANKS))

    def knowledge(self):
        return {"colors": [c for c in COLORS if c in self.colors],
                "ranks": [r for r in RANKS if r in self.ranks]}

    def clue(self, kind, value, matches):
        candidates = self.colors if kind == "color" else self.ranks
        if matches:
            candidates.intersection_update({value})
        else:
            candidates.discard(value)


class IllegalMove(ValueError):
    pass


class Game:
    def __init__(self, seed=None):
        deck = [Card(c, r) for c in COLORS for r in RANKS for _ in range(COPIES[r])]
        rng = SystemRandom() if seed is None else Random(seed)
        rng.shuffle(deck)
        self._deal(deck)

    @classmethod
    def from_draw_order(cls, draw_order):
        """Build an offline game from a full, first-card-first recorded deck."""
        if not isinstance(draw_order, list) or len(draw_order) != len(COLORS) * sum(COPIES.values()):
            raise ValueError("Expected a complete 50-card draw order")
        deck = []
        for value in draw_order:
            if (not isinstance(value, dict) or set(value) != {"color", "rank"}
                    or value["color"] not in COLORS or type(value["rank"]) is not int
                    or value["rank"] not in RANKS):
                raise ValueError("Invalid card in draw order")
            deck.append(Card(value["color"], value["rank"]))
        if Counter(deck) != {Card(c, r): COPIES[r] for c in COLORS for r in RANKS}:
            raise ValueError("Draw order has incorrect card multiplicities")
        game = cls.__new__(cls)
        deck.reverse()  # _deal and subsequent draws pop from the end.
        game._deal(deck)
        return game

    def initial_draw_order(self):
        """Recorder-only private setup. The first ten cards alternate seat 0, seat 1."""
        if self.turn != 0:
            raise ValueError("The initial draw order must be captured before play")
        dealt = [slot.card.visible() for pair in zip(*self._hands) for slot in pair]
        return dealt + [card.visible() for card in reversed(self._deck)]

    def _deal(self, deck):
        self._deck = deck
        self._hands: list[list[Slot]] = [[], []]
        for _ in range(5):
            for hand in self._hands:
                hand.append(Slot(self._deck.pop()))
        self.fireworks = dict.fromkeys(COLORS, 0)
        self.discard: list[Card] = []
        self.clues = MAX_CLUES
        self.lives = 3
        self.current = 0
        self.turn = 0
        self.final_turns: int | None = None
        self.end: str | None = None
        self._history: list[dict] = []

    @property
    def over(self):
        return self.end is not None

    @property
    def score(self):
        return 0 if self.end == "fuse" else sum(self.fireworks.values())

    def _legal(self):
        if self.over:
            return []
        hand = self._hands[self.current]
        partner = self._hands[1 - self.current]
        moves = [{"type": "play", "index": i} for i in range(len(hand))]
        if self.clues < MAX_CLUES:
            moves.extend({"type": "discard", "index": i} for i in range(len(hand)))
        if self.clues:
            for kind, values in (("rank", RANKS), ("color", COLORS)):
                touched = {getattr(s.card, kind) for s in partner}
                moves.extend({"type": "hint", "kind": kind, "value": v} for v in values if v in touched)
        return moves

    def view(self, seat):
        """Independent JSON values (no seed, card serial, own identity or draw order)."""
        if type(seat) is not int or seat not in (0, 1):
            raise ValueError("Invalid seat")
        # History contains only formerly revealed cards or the public result of a clue.
        return {"seat": seat, "current": self.current, "turn": self.turn, "score": self.score,
                "fireworks": dict(self.fireworks), "clues": self.clues, "lives": self.lives,
                "remaining": len(self._deck), "final_turns": self.final_turns, "over": self.over,
                "end": self.end, "hand": [s.knowledge() for s in self._hands[seat]],
                "partner": [dict(**s.card.visible(), knowledge=s.knowledge())
                            for s in self._hands[1 - seat]],
                "discard": [card.visible() for card in self.discard],
                "history": [dict(e, **({"card": dict(e["card"])} if "card" in e
                                       else {"matched": list(e["matched"])})) for e in self._history],
                "legal": self._legal() if self.current == seat else []}

    def apply(self, action):
        """Validate the complete wire shape before mutation (Python bool is not a card index)."""
        if type(action) is not dict or action not in self._legal():
            raise IllegalMove("That action is not legal now.")
        typ = action["type"]
        if typ in ("play", "discard"):
            if type(action["index"]) is not int:
                raise IllegalMove("Card index must be an integer.")
        elif action["kind"] == "rank" and type(action["value"]) is not int:
            raise IllegalMove("Rank must be an integer.")

        was_final = self.final_turns is not None
        hand = self._hands[self.current]
        event = {**action, "player": self.current, "turn": self.turn + 1}
        if typ == "hint":
            self.clues -= 1
            matched = []
            for i, slot in enumerate(self._hands[1 - self.current]):
                match = getattr(slot.card, action["kind"]) == action["value"]
                slot.clue(action["kind"], action["value"], match)
                if match:
                    matched.append(i)
            event["matched"] = matched
        else:
            card = hand.pop(action["index"]).card
            event["card"] = card.visible()
            if typ == "play":
                success = card.rank == self.fireworks[card.color] + 1
                event["success"] = success
                if success:
                    self.fireworks[card.color] = card.rank
                    if card.rank == 5:
                        self.clues = min(MAX_CLUES, self.clues + 1)
                else:
                    self.lives -= 1
                    self.discard.append(card)
            else:
                self.clues += 1
                self.discard.append(card)
            if self.lives == 0:
                self.end = "fuse"
            elif sum(self.fireworks.values()) == 25:
                self.end = "perfect"
            elif self._deck:
                hand.append(Slot(self._deck.pop()))
                if not self._deck:
                    self.final_turns = 2

        if was_final:
            self.final_turns -= 1
            if self.final_turns == 0 and self.end is None:
                self.end = "deck"
        self.turn += 1
        self.current = 1 - self.current
        self._history.append(event)
