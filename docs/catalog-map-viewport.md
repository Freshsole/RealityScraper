# Katalog: mapa, výřez a clustery

Poznámky k chování `/nabidka`, ať se při dalším portálu nebo podobné chybě nemusí hledat znovu.

## Co mapa ve skutečnosti filtruje

Výřez (`south/north/west/east`) se v SQL uplatní jen když není vybrané místo ani kruh.

Původní filtr bral **jen řádky s `lat/lon`**. iDNES (a do budoucna jakýkoli zdroj bez GPS) má souřadnice jen u zlomku inzerátů. Oddálení na Česko proto vypadalo, že se mapa „nenačte“: request šel, ale ve výřezu zůstaly skoro jen pražské GPS.

`Number(null) === 0` v JS taky umí poslat mapu na 0,0 (Null Island). Souřadnice 0,0 se musí zahodit.

## Inzeráty bez GPS

Text lokality (`"Bucharova, Praha 5 - Stodůlky"`, `"Olomouc"`) se mapuje na střed města / okresu v `app/places.py` (`CITY_CENTERS`, bundled tvary Prahy/Brna, `okres …`).

- **Úzký výřez:** GPS v bbox **nebo** lokalita měst, jejichž střed do bbox spadá.
- **Široký výřez** (součet rozpětí lat+lon ≥ 3.5): GPS v bbox **nebo** jakákoli neprázdná lokalita bez GPS. Piny se agregují **po městech** s `count`.
- Co se nepodaří zařadit a nemá GPS, jde do bubliny **Další** (střed Česka), ať součet bublin = číslo v nabídce.

Při širokém výřezu je `total` součtem `count` na pinech, ne samostatný `COUNT(*)` (ten umí rozcházet se s mapou).

Nominatim / Photon **nesmí** být na request path katalogu (timeouty, 429, 8–16 s). Street index z Overpass jen u vybraného OSM místa, výsledek se cachuje.

## Clustery na mapě

`web/catalog.js`: `pinWeight` bere `item.count`, ne počet markerů. Překryté bubliny se slučují podle pixelové vzdálenosti (`mergeNearbyPinGroups`). Na malém zoomu musí být buňka clusteru velká (jinak Praha ukáže spoustu „50“ vedle sebe).

Jednotlivé cenové piny (bez mergování) až od zoomu **16** — na půlce obrazovky je 14 pořád celé město. Překryté byty na stejné souřadnici se na 16+ jen mírně rozloží v pixelech (ne ve stupních, to posouvalo pin o ulici vedle). Klik na agregovaný městský pin má přiblížit, ne spiderfy 6000 fiktivních bodů.

Přesná adresa: portály často dají jen ulici. Po načtení detailu se z GPS doplní číslo popisné/orientační přes Nominatim reverse (`Kamenická 655/54, Praha 7 – Holešovice`) a uloží do `locality`. Mapový pin musí zůstat na `lat/lon` z inzerátu, ne na středu ulice.

## Proč to při oddálení viselo

`moveend` spouštěl `/api/catalog` na **každém stupni zoomu**. Každý request dělal `GROUP BY locality` přes desítky tisíc řádků. Abort na klientovi **nezruší** `asyncio.to_thread` — SQLite požadavky seřadily a čekání se sčítalo.

Opatření:

- debounce ~550 ms a přeskočit stejný zaokrouhlený výřez
- při širokém výřezu vynechat samostatný `COUNT(*)`
- cache městských pinů ~45 s (`Store._city_pin_cache`) pro celostátní pohled
- cache `locality → město` v `places.locality_anchor_match`

Nový zdroj bez GPS: rozšířit `CITY_CENTERS` / okresy, ne geocodovat v katalogu.

## Související kód

- `app/store.py` — `_apply_map_bbox`, `_catalog_pins`
- `app/places.py` — `locality_anchor_match`, `anchors_in_bbox`, `CITY_CENTERS`
- `web/catalog.js` — `mapBoundsParams`, `groupedPins`, `mergeNearbyPinGroups`, reload po `moveend`
