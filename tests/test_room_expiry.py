"""Exercise delayed/early alarms without waiting a day in a Workers runtime."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from hanabi.rooms import Room


def load_worker():
    path = Path(__file__).resolve().parents[1] / "worker" / "entry.py"
    spec = importlib.util.spec_from_file_location("room_expiry_worker", path)
    worker = importlib.util.module_from_spec(spec)
    response = SimpleNamespace(from_json=lambda data, **options: (data, options))
    sdk = SimpleNamespace(DurableObject=object, WorkerEntrypoint=object, Request=object, Response=response)
    with patch.dict("sys.modules", {"workers": sdk, "js": SimpleNamespace(WebSocketPair=object)}):
        spec.loader.exec_module(worker)
    return worker


class RoomExpiry(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.worker = load_worker()
        self.socket = Mock()
        self.token = "host-credential-for-test"
        self.room = Room.create("ABCD2345", "online", self.token)
        self.kv = {"room": json.dumps({"expires": 120_000, "room": self.room.dump()})}
        self.storage = SimpleNamespace(
            kv=SimpleNamespace(get=self.kv.get, put=self.kv.__setitem__),
            setAlarm=AsyncMock(), deleteAll=AsyncMock(side_effect=self.kv.clear),
        )
        self.instance = self.worker.GameRoom()
        self.instance.ctx = SimpleNamespace(storage=self.storage, getWebSockets=lambda: [self.socket])
        self.clock = self.enterContext(patch.object(self.worker.time, "time", return_value=100.0))

    async def view(self, credential):
        return await self.instance.fetch(SimpleNamespace(
            method="GET", url="https://example.org/play/hanabi/api/rooms/ABCD2345",
            headers={"Cookie": "hanabi_seat=" + credential},
        ))

    async def test_authenticated_read_extends_idle_expiry_and_early_alarm_reschedules(self):
        _, options = await self.view(self.token)
        expires = 100_000 + self.worker.TTL_SECONDS * 1000
        self.assertEqual(options["status"], 200)
        self.assertEqual(json.loads(self.kv["room"])["expires"], expires)
        self.storage.setAlarm.assert_awaited_once_with(expires)
        self.clock.return_value = 121
        self.storage.setAlarm.reset_mock()
        await self.instance.alarm()
        self.storage.setAlarm.assert_awaited_once_with(expires)
        self.socket.close.assert_not_called()
        self.storage.deleteAll.assert_not_awaited()

    async def test_unauthorized_read_cannot_extend_lifetime_and_expiry_precedes_cleanup(self):
        _, options = await self.view("unknown-credential-for-test")
        self.assertEqual(options["status"], 403)
        self.storage.setAlarm.assert_not_awaited()
        self.clock.return_value = 120
        payload, options = await self.view(self.token)
        self.assertEqual(options["status"], 404)
        self.assertEqual(set(payload), {"error"})
        self.storage.setAlarm.assert_not_awaited()
        await self.instance.alarm()
        self.socket.close.assert_called_once_with(1000, "Room expired")
        self.assertFalse(self.kv)
