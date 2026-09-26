#!/bin/sh
# Wave 5A concurrency matrix vs scrape_baseline_v2.json. Sequential; do not edit app/ while this runs.
set -eu
cd "$(dirname "$0")/../.."
PY="${PY:-.venv/bin/python}"
export SCRAPE_METRICS_DETAIL=1
COMPARE=scripts/perf/scrape_baseline_v2.json
COMMON="$PY -m scripts.perf.scrape_bench --minutes 5 --compare $COMPARE --skip-headers"

echo "=== g16 SCRAPE_GLOBAL_CONCURRENCY=16 ==="
SCRAPE_GLOBAL_CONCURRENCY=16 $COMMON --out scripts/perf/scrape_wave5_g16.json

echo "=== g24 SCRAPE_GLOBAL_CONCURRENCY=24 (v2-equivalent) ==="
SCRAPE_GLOBAL_CONCURRENCY=24 $COMMON --out scripts/perf/scrape_wave5_g24.json

echo "=== uncap SCRAPE_GLOBAL_CONCURRENCY=128 ==="
SCRAPE_GLOBAL_CONCURRENCY=128 $COMMON --out scripts/perf/scrape_wave5_uncap.json

echo "=== overrides HTML 2-4, sreality 16, global 24 ==="
SCRAPE_GLOBAL_CONCURRENCY=24 \
SCRAPE_CONCURRENCY_OVERRIDES='{"sreality":16,"idnes":4,"bazos":4,"bezrealitky":4,"remax":3,"annonce":3,"ceskereality":3,"mmreality":2,"realitycz":2,"ulovdomov":2}' \
$COMMON --out scripts/perf/scrape_wave5_overrides.json

echo "=== wave5A matrix done ==="
