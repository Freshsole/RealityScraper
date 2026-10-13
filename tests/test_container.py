"""Worker container helpers: DB readiness wait and heartbeat health."""
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from app import container


class WaitForDbTests(unittest.TestCase):
    def test_backoff_until_db_appears(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "monitor.sqlite"
            delays: list[float] = []

            def fake_sleep(d: float) -> None:
                delays.append(d)
                if len(delays) == 3:
                    conn = sqlite3.connect(db)
                    conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
                    conn.commit()
                    conn.close()

            container.wait_for_db(db, sleep=fake_sleep, log=lambda _m: None)
            self.assertEqual(delays, [0.5, 1.0, 2.0])

    def test_times_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                container.wait_for_db(
                    Path(tmp) / "missing.sqlite",
                    timeout_s=0.05,
                    sleep=lambda _d: time.sleep(0.03),
                    log=lambda _m: None,
                )

    def test_not_ready_while_init_lock_held(self):
        import fcntl

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "monitor.sqlite"
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
            conn.commit()
            conn.close()
            self.assertTrue(container.db_ready(db))
            with open(Path(tmp) / container.STORE_INIT_LOCK, "a+") as fh:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                self.assertFalse(container.db_ready(db))


class HeartbeatTests(unittest.TestCase):
    def test_worker_health_follows_heartbeat_age(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = container.HEARTBEAT_PATH
            container.HEARTBEAT_PATH = Path(tmp) / "hb"
            try:
                self.assertFalse(container.worker_healthy())
                container.touch_heartbeat()
                self.assertTrue(container.worker_healthy())
                stale = time.time() - container.HEARTBEAT_MAX_AGE_S - 5
                os.utime(container.HEARTBEAT_PATH, (stale, stale))
                self.assertFalse(container.worker_healthy())
            finally:
                container.HEARTBEAT_PATH = old


if __name__ == "__main__":
    unittest.main()
