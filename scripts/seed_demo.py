"""Fill the local catalog with believable sample listings (development only).

Local:   docker compose exec -T web python - < scripts/seed_demo.py
Direct:  python scripts/seed_demo.py [path/to/monitor.sqlite]

Idempotent: rows use ids from 900000 up and are replaced on re-run.
"""

from __future__ import annotations

import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent)) if "__file__" in globals() else None
sys.path.insert(0, "/app")

from app import config  # noqa: E402
from app.store import Store  # noqa: E402

# (locality label, lat, lon, city, Kč/m² centre)
PLACES = [
    ("Praha 1 - Staré Město", 50.087, 14.421, "Praha", 640),
    ("Praha 2 - Vinohrady", 50.075, 14.438, "Praha", 560),
    ("Praha 3 - Žižkov", 50.083, 14.451, "Praha", 500),
    ("Praha 4 - Nusle", 50.059, 14.439, "Praha", 470),
    ("Praha 5 - Smíchov", 50.071, 14.403, "Praha", 510),
    ("Praha 6 - Dejvice", 50.100, 14.392, "Praha", 520),
    ("Praha 7 - Holešovice", 50.103, 14.435, "Praha", 530),
    ("Praha 8 - Karlín", 50.093, 14.450, "Praha", 540),
    ("Praha 9 - Vysočany", 50.110, 14.503, "Praha", 450),
    ("Praha 10 - Vršovice", 50.068, 14.458, "Praha", 470),
    ("Brno - střed", 49.195, 16.608, "Brno", 400),
    ("Brno - Královo Pole", 49.227, 16.595, "Brno", 360),
    ("Brno - Žabovřesky", 49.213, 16.575, "Brno", 380),
    ("Ostrava - Poruba", 49.829, 18.166, "Ostrava", 260),
    ("Ostrava - Moravská Ostrava", 49.837, 18.292, "Ostrava", 290),
    ("Plzeň - Jižní Předměstí", 49.727, 13.386, "Plzeň", 320),
]
LAYOUTS = [("1+kk", 24, 38), ("1+1", 32, 44), ("2+kk", 42, 62), ("2+1", 50, 70), ("3+kk", 64, 92), ("3+1", 70, 100), ("4+kk", 88, 125)]
PORTALS = [
    ("sreality", "https://www.sreality.cz/detail/pronajem/byt/{lay}/{id}"),
    ("bezrealitky", "https://www.bezrealitky.cz/nemovitosti-byty-domy/{id}"),
    ("idnes", "https://reality.idnes.cz/detail/pronajem/byt/{id}"),
    ("bazos", "https://reality.bazos.cz/inzerat/{id}/"),
    ("ceskereality", "https://www.ceskereality.cz/pronajem/byty/{id}.html"),
]
IMAGES = ["/static/site/assets/hero-apart.webp", "/static/site/assets/sold-1.webp"] + [
    f"/static/site/assets/room/{name}.webp" for name in ("dum", "kuchyne", "kytka", "nabytek", "postel", "skrin")
]


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else config.DB_PATH
    store = Store(path)
    rng = random.Random(42)
    now = datetime.now(timezone.utc)
    extras = json.dumps({"offer": "Pronájem", "estate": "Byt"}, ensure_ascii=False)
    rows = []
    for i in range(900):
        label, lat, lon, city, ppm = rng.choices(PLACES, weights=[w[4] ** 2 for w in PLACES])[0]
        lay, lo, hi = rng.choice(LAYOUTS)
        area = rng.randint(lo, hi)
        price = int(round(area * ppm * rng.uniform(0.82, 1.22), -2))
        # spread over the last ~80 days, a bit denser recently
        age = timedelta(days=rng.random() ** 1.6 * 80, minutes=rng.randint(0, 600))
        first = now - age
        portal, pattern = rng.choice(PORTALS)
        rid = 900000 + i
        rows.append(
            (
                rid, "default", f"Pronájem bytu {lay} {area} m²", price, f"{price:,} Kč/měsíc".replace(",", " "), lay, area,
                label, pattern.format(id=rid, lay=lay.replace("+", "-")), IMAGES[i % len(IMAGES)],
                first.isoformat(), now.isoformat(), 0, 0, lat + rng.uniform(-0.012, 0.012), lon + rng.uniform(-0.018, 0.018),
                extras,
            )
        )
    with store.connect() as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO listings(
              id, monitor_id, name, price_czk, price_label, disposition, area_m2, locality, url, image_url,
              first_seen, last_seen, gone, notified, lat, lon, extras
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )
    print(f"seeded {len(rows)} listings into {path}")


if __name__ == "__main__":
    main()
