"""Exercise a running Hanabi Worker: python3 -m tests.online_runtime GAME_URL."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection, HTTPSConnection
from http.cookies import SimpleCookie
import json
import threading
import unittest
from urllib.parse import urlsplit

from hanabi.game import COLORS, COPIES, Game


class OnlineRuntimeTests(unittest.TestCase):
    def request(self, path, body=None, cookie=None):
        url = self.server_url
        connection_type = HTTPSConnection if url.scheme == "https" else HTTPConnection
        connection = connection_type(url.hostname, url.port, timeout=20)
        headers = {"Origin": f"{url.scheme}://{url.netloc}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if cookie:
            headers["Cookie"] = cookie
        try:
            connection.request("GET" if body is None else "POST", url.path.rstrip("/") + "/api/rooms" + path,
                               None if body is None else json.dumps(body), headers)
            response = connection.getresponse()
            self.assertIn("no-store", response.getheader("Cache-Control", ""))
            data = json.loads(response.read())
            return response.status, data, response.getheader("Set-Cookie")
        finally:
            connection.close()

    def status(self, expected, *args):
        status, data, cookie = self.request(*args)
        self.assertEqual(status, expected, data.get("error"))
        if expected >= 400:
            self.assertEqual(set(data), {"error"})
        return data, cookie

    def cookie(self, header, code):
        morsel = SimpleCookie(header)["hanabi_seat"]
        self.assertEqual(morsel["path"], self.server_url.path.rstrip("/") + "/api/rooms/" + code)
        self.assertEqual(int(morsel["max-age"]), 86400)
        self.assertTrue(morsel["secure"] and morsel["httponly"])
        self.assertEqual(morsel["samesite"].lower(), "strict")
        return f"{morsel.key}={morsel.value}"

    def race(self, path, body, cookie=None):
        gate = threading.Barrier(2, timeout=10)

        def issue():
            gate.wait()
            return self.request(path, body, cookie)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: issue(), range(2)))
        self.assertEqual(sorted(result[0] for result in results), [200, 409])
        success, rejected = sorted(results, key=lambda result: result[0])
        self.assertEqual(set(rejected[1]), {"error"})
        return success[1:]

    def test_two_clients_recover_and_finish(self):
        waiting, header = self.status(201, "", {"mode": "online"})
        code = waiting["code"]
        path = "/" + code
        host = self.cookie(header, code)
        self.assertEqual(waiting, {"code": code, "mode": "online", "status": "waiting", "seat": 0, "game": None})
        self.status(403, path)
        self.status(403, path + "/join", {}, "hanabi_seat=fixture-unknown-seat")
        joined, header = self.race(path + "/join", {})
        guest = self.cookie(header, code)
        self.assertEqual(joined["seat"], 1)
        self.status(409, path + "/join", {})
        self.status(400, path + "/join", {"seat": 0}, guest)
        self.assertEqual(self.status(200, path + "/join", {}, guest)[0], joined)
        players = (host, guest)

        def read_both():
            return [self.status(200, path, None, cookie)[0] for cookie in players]

        def face(card):
            return {field: card[field] for field in ("color", "rank")}

        views = read_both()
        deal_id = views[0]["game"]["id"]
        draw_order = []
        for pair in zip(views[1]["game"]["partner"], views[0]["game"]["partner"]):
            draw_order.extend(map(face, pair))
        positions, actions = [views], []
        while not views[0]["game"]["over"]:
            turn = len(actions)
            self.assertLess(turn, 150)
            actor = turn % 2
            action = ({"type": "hint", "kind": "rank", "value": 1} if turn == 0 else
                      {"type": "discard", "index": 0} if turn == 1 else {"type": "play", "index": 0})
            body = {"id": deal_id, "turn": turn, "action": action}
            old_remaining = views[actor]["game"]["remaining"]
            if turn == 0:
                self.status(409, path + "/move", body, guest)
                self.status(422, path + "/move", {**body, "action": {"type": "play", "index": True}}, host)
                accepted, _ = self.race(path + "/move", body, host)
            else:
                accepted, _ = self.status(200, path + "/move", body, players[actor])
            views = read_both()
            self.assertEqual(accepted, views[actor])
            if turn == 0:
                self.status(409, path + "/move", body, host)
                if self.pause_for_restart:
                    input("Restart the local Worker using the same storage, then press Enter. ")
                self.assertEqual(read_both(), views)
            actions.append(action)
            positions.append(views)
            if views[actor]["game"]["remaining"] < old_remaining:
                draw_order.append(face(views[1 - actor]["game"]["partner"][-1]))

        # Both clients together saw every card actually drawn. Unused cards can
        # appear in any order without changing the observations of either seat.
        counts = Counter({(c, r): count for c in COLORS for r, count in COPIES.items()})
        counts.subtract((card["color"], card["rank"]) for card in draw_order)
        self.assertGreaterEqual(min(counts.values()), 0)
        draw_order.extend({"color": color, "rank": rank} for color, rank in counts.elements())
        game = Game.from_draw_order(draw_order, empty_clues=True)
        for turn, views in enumerate(positions):
            if turn:
                game.apply(actions[turn - 1])
            for seat, snapshot in enumerate(views):
                self.assertEqual(snapshot, {"code": code, "mode": "online", "seat": seat,
                                            "status": "finished" if game.over else "playing",
                                            "game": dict(game.view(seat), id=deal_id, recording=False)})
        self.status(409, path + "/move", {"id": deal_id, "turn": game.turn, "action": actions[0]}, host)

    def test_solo_adaptive(self):
        solo, header = self.status(201, "", {"mode": "solo"})
        path = "/" + solo["code"]
        cookie = self.cookie(header, solo["code"])
        self.assertEqual((solo["mode"], solo["status"], solo["seat"]), ("solo", "playing", 0))
        self.status(409, path + "/join", {})
        game = solo["game"]
        stated_move = {"id": game["id"], "turn": 0, "action": {"type": "hint", "kind": "color", "value": "red"}}
        result, _ = self.status(200, path + "/move", stated_move, cookie)
        self.assertEqual((result["game"]["turn"], result["game"]["current"]), (2, 0))
        self.assertEqual([event["player"] for event in result["game"]["history"]], [0, 1])
        self.assertEqual(result, self.status(200, path, None, cookie)[0])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game_url", help="Base URL of the running game, including /play/hanabi/")
    parser.add_argument("--pause-for-restart", action="store_true", help="Pause once to restart the Worker")
    args = parser.parse_args()
    OnlineRuntimeTests.server_url = urlsplit(args.game_url)
    OnlineRuntimeTests.pause_for_restart = args.pause_for_restart
    unittest.main(argv=[__file__], verbosity=2)
