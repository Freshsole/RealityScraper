-- Wave 4: events TTL + listing_photos cap (applied by Store.prune_perf_data).
-- Dry-run first: PRUNE_APPLY=0 (default) only counts rows.
-- Apply: PRUNE_APPLY=1 then restart, or call Store.prune_perf_data(dry_run=False).
--
-- Events older than EVENTS_TTL_DAYS (default 30):
--   DELETE FROM events WHERE created_at < cutoff;
-- Photos beyond LISTING_PHOTOS_CAP (default 8) per listing:
--   keep lowest sort_order / rowid.
SELECT 1;
