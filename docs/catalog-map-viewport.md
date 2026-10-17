# Katalog: mapa, seznam a počet (`/nabidka`)

Jedno pravidlo: **seznam, číslo „N nemovitostí“ a piny na mapě jsou vždy ten samý výřez z jedné množiny.**

## Co je „na mapě“

Inzerát je na mapě, právě když má **uložené souřadnice** (`lat/lon`, ne NULL, ne 0,0). Prostorový filtr je jen jeden z těchto (vzájemně se vylučují):

| Režim | Podmínka |
| --- | --- |
| vybrané místo (polygon, ulice, bod) | souřadnice leží uvnitř tvaru (`places.point_matches`) |
| kruh | souřadnice do `radius_m` |
| výřez mapy | souřadnice v `south/north/west/east` |
| nic z toho (např. vybraný hlídaný pes) | bez prostorového omezení; `pinned < total`, klient napíše „N na mapě“ |

Čtení **nikdy nehádá ani neposouvá souřadnice** a nehledá text lokality („Praha 7“) jako náhradu za GPS. Dřív to dělal seznam (text + dopočet souřadnic podle názvu ulice napříč celou DB), piny ne, a proto se počty rozcházely a piny byly na špatných místech (`Šimáčkova, Praha 7` skončila u Kolína).

## Jedna cesta v `app/store.py`

`Store.catalog()` složí `WHERE` (všechny ne-prostorové filtry + prostorový filtr výše) a pak:

- `_resolve_in_places` (polygon): jeden SELECT, dedup podle identity (`canonical_key`), přesný test polygonu v Pythonu → `matched`. Z `matched` se řežou `total`, stránka seznamu i piny.
- `_resolve_in_sql` (výřez, kruh, bez prostoru): `COUNT` přes `GROUP BY identity`, stránka `ORDER BY … LIMIT/OFFSET` (stránkování jde do konce), piny ze stejné `WHERE`.

Piny: do `PIN_DETAIL_MAX` (3000) nabídek jeden pin na nabídku. Nad to mřížkové clustery (`PIN_GRID_CELLS`² buněk) s **přesným `count`**, takže součet `count` přes piny = `pinned`. Buňka s jednou nabídkou zůstává normální cenový pin. Cluster z mřížky má `cluster: true`, klik na něj vždy přiblíží.

`/api/catalog?include_pins=1` vrací `items`, `total`, `pins` i `pinned` v jedné odpovědi (jeden požadavek, jedna cache položka, jeden snímek filtrů a výřezu). `/api/catalog/pins` (`pins_only`) používá stejnou funkci. Klient (`web/catalog.js`, `loadCatalog`) už druhý požadavek nedělá.

`approx` na pinu je jen příznak (souřadnice = střed města/čtvrti a lokalita nemá ulici), pin se kvůli němu nehýbe.

## Odkud se souřadnice berou

Souřadnice se doplňují **při zápisu / v údržbě**, ne při čtení: `Store.locate_catalog_listings` (spouští `Monitor.catalog_coords_loop` každých ~5 min, plná oprava 1× denně).

1. Inzerát bez GPS: ulice z OSM street indexu / geocode cache, jinak střed města či čtvrti z `places.CITY_CENTERS`.
2. `repair=True` opraví už uložené špatné souřadnice: adresa se souřadnicemi na středu města/čtvrti, vnější čtvrť přišpendlená na střed Prahy, iDNES/Bazoš víc než 30 km od města v textu.
3. Každý výsledek musí ležet do `LOCALITY_MAX_KM` (25 km) od města v textu lokality, jinak se použije střed toho města. Stejný název ulice v jiném městě (`Táborská, Brno` vs. Tábor) tak nemůže vyhrát. Sourozenecké inzeráty z té samé ulice (`_peer_street_point`) se berou jen z okolí města.
4. Lokalita, která nejde přiřadit k žádnému místu, zůstane bez souřadnic, tedy mimo mapu i seznam s prostorovým filtrem.

Žádné Nominatim/Photon/Overpass na request path. Nový zdroj bez GPS: rozšířit `CITY_CENTERS` / okresy, ne geocodovat v katalogu.

## Klient

`web/catalog.js`: `pinWeight` bere `item.count`; překryté bubliny se slučují podle pixelové vzdálenosti (`mergeNearbyPinGroups`), součet se zachovává. Jednotlivé cenové piny (bez mergování) až od zoomu **16**. Přesná adresa: po načtení detailu se z GPS doplní číslo popisné přes Nominatim reverse a uloží do `locality`; pin ale zůstává na `lat/lon` z inzerátu.

`moveend` spouští `/api/catalog` s debounce a přeskočí stejný zaokrouhlený výřez. Zrušení `AbortController` na klientovi nezastaví `asyncio.to_thread` na serveru, proto držíme dotazy levné (viz výkon).

## Výkon (lokální kopie produkční DB, 219 tis. řádků)

| Dotaz | Doba |
| --- | --- |
| celá ČR, pronájem, seznam + 148 clusterů | ~0,35 s |
| Praha, pronájem | ~0,2 s |
| polygon Praha 7 | ~0,1 s |
| bez prostoru, prodej (141 tis.) | ~0,9 s |

## Testy

`tests/test_catalog_map_zoom.py` (výřez), `tests/test_catalog_place.py` (polygon, seznam == piny, stránkování, zamítnutí stejné ulice v jiném městě), `tests/test_locality_street_geo.py` (údržbová oprava souřadnic).
