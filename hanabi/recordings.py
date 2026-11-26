"""Private recordings of human games and a local, read-only analysis CLI."""

import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import sqlite3
import stat

from .game import Game, IllegalMove


APPLICATION_ID = int.from_bytes(b"HNBR", "big")
SCHEMA = 2
FORMAT = "hanabi-human-replay-v1"
RULES = "two-seat-five-color-v1"
EMPTY_CLUE_RULES = "two-seat-five-color-empty-clues-v2"
SCHEMA_GAMES = """
    CREATE TABLE games (
        deal_id TEXT PRIMARY KEY NOT NULL, seed_hex TEXT NOT NULL, draw_order TEXT NOT NULL,
        teammate TEXT NOT NULL,
        revision TEXT NOT NULL, python_version TEXT NOT NULL, rules TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('partial', 'complete', 'replaced')),
        turns INTEGER NOT NULL, score INTEGER NOT NULL, ending TEXT
    )"""
SCHEMA_MOVES = """
    CREATE TABLE moves (
        deal_id TEXT NOT NULL REFERENCES games(deal_id), turn INTEGER NOT NULL,
        recorded_at TEXT NOT NULL, event TEXT NOT NULL, PRIMARY KEY(deal_id, turn)
    )"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _connect(path, mode):
    db = sqlite3.connect(Path(path).absolute().as_uri() + "?mode=" + mode, uri=True)
    db.row_factory = sqlite3.Row
    return db


def _validate_db(db):
    if (db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
            or db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA):
        raise sqlite3.DatabaseError("Unsupported Hanabi recording database")


def _action(event):
    if event["type"] == "hint":
        return {field: event[field] for field in ("type", "kind", "value")}
    return {field: event[field] for field in ("type", "index")}


class RecordingStore:
    """One atomic commit per new deal or accepted human/bot move batch."""

    def __init__(self, path, *, revision="unversioned"):
        self.path = Path(path).absolute()
        self.revision = revision
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Follow a systemd StateDirectory symlink, but require the directory
        # holding the DB and its rollback journal to prevent filename swaps.
        if self.path.parent.stat().st_mode & 0o022:
            raise PermissionError("Recording directory must not be writable by group or others")
        # SQLite journals inherit the database's mode. Never open an existing
        # symlink or quietly repurpose/change permissions on someone else's file.
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
        fd = os.open(self.path, flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise PermissionError("Recording database must be a private regular file (0600)")
        finally:
            os.close(fd)
        with closing(_connect(self.path, "rw")) as db, db:
            db.execute("BEGIN IMMEDIATE")
            app = db.execute("PRAGMA application_id").fetchone()[0]
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if app == 0 and version == 0 and not db.execute("SELECT 1 FROM sqlite_master").fetchone():
                db.execute(SCHEMA_GAMES)
                db.execute(SCHEMA_MOVES)
                db.execute(f"PRAGMA application_id = {APPLICATION_ID}")
                db.execute(f"PRAGMA user_version = {SCHEMA}")
            _validate_db(db)

    @contextmanager
    def _transaction(self):
        with closing(_connect(self.path, "rw")) as db, db:
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("BEGIN IMMEDIATE")
            _validate_db(db)
            yield db

    @staticmethod
    def _replace(db, deal_id, now):
        db.execute("UPDATE games SET status='replaced', updated_at=? WHERE deal_id=? AND status='partial'",
                   (now, deal_id))

    def create(self, deal_id, seed_hex, teammate, *, replaced_id=None, game=None):
        if game is None:
            game = Game(seed=int(seed_hex, 16))
        order = json.dumps(game.initial_draw_order(), separators=(",", ":"))
        now = _now()
        with self._transaction() as db:
            if replaced_id is not None:
                self._replace(db, replaced_id, now)
            db.execute("""
                INSERT INTO games (deal_id, seed_hex, draw_order, teammate, revision, python_version, rules,
                                   created_at, updated_at, status, turns, score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'partial', 0, 0)
            """, (deal_id, seed_hex, order, teammate, self.revision, platform.python_version(),
                  EMPTY_CLUE_RULES if game.empty_clues else RULES, now, now))

    def mark_replaced(self, deal_id):
        with self._transaction() as db:
            self._replace(db, deal_id, _now())

    def append(self, deal_id, expected_turn, moves, game):
        """Store accepted public events, including exactly which cards clues marked."""
        events = game.view(0)["history"][expected_turn:]
        if not moves or game.turn != expected_turn + len(moves) or len(events) != len(moves):
            raise sqlite3.IntegrityError("Move batch does not match the game revision")
        now = _now()
        rows = []
        for (seat, action), event in zip(moves, events):
            if event["player"] != seat or _action(event) != action:
                raise sqlite3.IntegrityError("Move batch differs from accepted actions")
            rows.append((deal_id, event["turn"], now, json.dumps(event, separators=(",", ":"))))
        with self._transaction() as db:
            result = db.execute("""
                UPDATE games SET turns=?, updated_at=?, score=?, ending=?, status=?
                WHERE deal_id=? AND turns=? AND status='partial'
            """, (game.turn, now, game.score, game.end, "complete" if game.over else "partial",
                  deal_id, expected_turn))
            if result.rowcount != 1:
                raise sqlite3.IntegrityError("Recording has a different revision or is closed")
            db.executemany("INSERT INTO moves(deal_id, turn, recorded_at, event) VALUES (?, ?, ?, ?)", rows)


def summary(path):
    with closing(_connect(path, "ro")) as db:
        _validate_db(db)
        rows = db.execute("SELECT status, count(*) AS n, sum(turns) AS turns FROM games GROUP BY status")
        counts = dict.fromkeys(("partial", "complete", "replaced"), 0)
        moves = 0
        for row in rows:
            counts[row["status"]] = row["n"]
            moves += row["turns"]
        return {"games": sum(counts.values()), "moves": moves, "status_counts": counts}


def _replay(db, row):
    if row["rules"] not in (RULES, EMPTY_CLUE_RULES):
        raise ValueError("Unsupported recorded rules version")
    draw_order = json.loads(row["draw_order"])
    game = Game.from_draw_order(draw_order, empty_clues=row["rules"] == EMPTY_CLUE_RULES)
    moves = []
    records = db.execute("SELECT turn, recorded_at, event FROM moves WHERE deal_id=? ORDER BY turn",
                         (row["deal_id"],))
    for record in records:
        event = json.loads(record["event"])
        if record["turn"] != game.turn + 1 or event["player"] != game.current:
            raise ValueError("Recorded moves are out of order")
        seat = game.current
        observation = game.view(seat)
        targets = game.training_hand(seat)
        action = _action(event)
        game.apply(action)
        if game.view(seat)["history"][-1] != event:
            raise ValueError("Recorded public outcome differs from replay")
        moves.append({"turn": record["turn"], "seat": seat, "actor": "human" if seat == 0 else "bot",
                      "recorded_at": record["recorded_at"], "observation": observation,
                      "own_card_targets": targets,
                      "action": action, "event": event})
    if (game.turn != row["turns"] or game.score != row["score"] or game.end != row["ending"]
            or game.over != (row["status"] == "complete")):
        raise ValueError("Recorded result differs from replay")
    return {"format": FORMAT, "rules": row["rules"], "revision": row["revision"],
            "python_version": row["python_version"], "deal_id": row["deal_id"],
            "deal": {"seed_hex": row["seed_hex"], "draw_order": draw_order},
            "teammate": row["teammate"], "human_seat": 0,
            "created_at": row["created_at"], "updated_at": row["updated_at"], "status": row["status"],
            "turns": game.turn, "score": game.score, "ending": game.end, "moves": moves}


def _read_game(db, row):
    try:
        return _replay(db, row)
    except (IllegalMove, KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Cannot reconstruct deal {row['deal_id']}: {error}") from error


def export(path, deal_id=None):
    if deal_id is not None:
        with closing(_connect(path, "ro")) as source:
            source.execute("BEGIN")
            _validate_db(source)
            row = source.execute("SELECT * FROM games WHERE deal_id=?", (deal_id,)).fetchone()
            game = _read_game(source, row) if row is not None else None
        if game is not None:
            yield game
        return
    # A slow consumer does not hold a read lock on the live game's database.
    # SQLite owns and cleans up this private temporary snapshot.
    with closing(sqlite3.connect("")) as snapshot:
        snapshot.row_factory = sqlite3.Row
        with closing(_connect(path, "ro")) as source:
            _validate_db(source)
            source.backup(snapshot, pages=128)
        for row in snapshot.execute("SELECT * FROM games ORDER BY created_at, deal_id"):
            yield _read_game(snapshot, row)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("summary", help="count stored games and moves").add_argument("database", type=Path)
    dump = commands.add_parser("export", help="write private replay JSONL to stdout")
    dump.add_argument("database", type=Path)
    dump.add_argument("--deal-id", help="export just one game")
    args = parser.parse_args()
    try:
        if args.command == "summary":
            print(json.dumps(summary(args.database)))
        else:
            for game in export(args.database, args.deal_id):
                print(json.dumps(game, separators=(",", ":"), allow_nan=False))
    except (sqlite3.Error, OSError, ValueError) as error:
        parser.exit(1, f"Recording error: {error}\n")


if __name__ == "__main__":
    main()
