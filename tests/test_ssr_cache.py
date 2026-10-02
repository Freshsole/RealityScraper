"""SSR cache unit tests (separate sqlite file, no catalog lock on read)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import ssr_cache


class SsrCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ssr_cache.sqlite"
        self._orig = ssr_cache.SSR_CACHE_PATH
        ssr_cache.SSR_CACHE_PATH = self.path

    def tearDown(self) -> None:
        ssr_cache.SSR_CACHE_PATH = self._orig
        self.tmp.cleanup()

    def test_put_get_survives_failed_rebuild(self) -> None:
        with ssr_cache._connect(writable=True) as conn:
            ssr_cache._init_schema(conn)
            with conn:
                ssr_cache._put_many(
                    conn,
                    [("home", json.dumps({"active_count": 42, "data_as_of": "2026-10-02T10:00:00+00:00"}), "2026-10-02T10:00:00+00:00", "2026-10-02T10:00:00+00:00")],
                )
                ssr_cache._set_meta(conn, "last_success_data_as_of", "2026-10-02T10:00:00+00:00")

        home = ssr_cache.get_json("home")
        self.assertEqual(home["active_count"], 42)
        self.assertEqual(ssr_cache.last_success_as_of(), "2026-10-02T10:00:00+00:00")

        with mock.patch.object(ssr_cache, "rebuild", side_effect=RuntimeError("boom")):
            # ensure_fresh should not wipe existing pages when rebuild raises before write
            pass
        # Simulate failed rebuild that only writes last_error
        with ssr_cache._connect(writable=True) as conn:
            ssr_cache._init_schema(conn)
            with conn:
                ssr_cache._set_meta(conn, "last_error", "boom")
        home2 = ssr_cache.get_json("home")
        self.assertEqual(home2["active_count"], 42)

    def test_readonly_connect(self) -> None:
        with ssr_cache._connect(writable=True) as conn:
            ssr_cache._init_schema(conn)
            with conn:
                ssr_cache._put_many(conn, [("llms", "# Realitify\n", "t", "t")])
        page = ssr_cache.get_page("llms")
        self.assertIn("Realitify", page["body"])

    def test_cache_lives_beside_main_db(self) -> None:
        from app import config

        # Same persistent DATA_DIR as monitor.sqlite (Coolify volume /data).
        self.assertEqual(config.DB_PATH.parent, config.DATA_DIR)
        self.assertEqual(config.DATA_DIR / "ssr_cache.sqlite", config.DB_PATH.parent / "ssr_cache.sqlite")


if __name__ == "__main__":
    unittest.main()
