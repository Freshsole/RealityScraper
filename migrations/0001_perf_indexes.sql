-- Wave 1: index-only speedups. Safe to re-run.
CREATE INDEX IF NOT EXISTS idx_listings_url ON listings(url);
CREATE INDEX IF NOT EXISTS idx_catalog_lat_lon ON catalog_listings(lat, lon);
UPDATE listings SET gone = 0 WHERE gone IS NULL;
UPDATE catalog_listings SET gone = 0 WHERE gone IS NULL;
ANALYZE listings;
ANALYZE catalog_listings;
