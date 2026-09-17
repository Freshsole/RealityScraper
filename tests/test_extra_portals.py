import unittest
from pathlib import Path

from app.annonce import AnnonceClient
from app.block_page import classify_block
from app.ceskereality import CeskerealityClient
from app.mmreality import MmrealityClient
from app.portal_urls import (
    annonce_url,
    ceskereality_url,
    mmreality_url,
    realitycz_url,
    remax_url,
    ulovdomov_url,
)
from app.realitycz import RealityczClient
from app.remax import RemaxClient
from app.ulovdomov import (
    UlovdomovClient,
    fields_from_inzerat_slug,
    offer_from_inzerat_slug,
    offers_from_payload,
    parse_sitemap_offers,
)

FIXTURES = Path(__file__).parent / "fixtures"


CESKE = """
<article class="i-estate">
  <a href="/pronajem/byty/praha/123456/">x</a>
  <h2>Pronájem bytu 2+kk 54 m², Praha 3 – Žižkov</h2>
  <div class="i-estate__price">16 500 Kč</div>
  <img src="https://img-cache.ceskereality.cz/nemovitosti/abc/123456/320x320_1.jpg" alt="Pronájem 2+kk" />
</article>
"""

ANNONCE = """
<div class="box q ext-item">
  <h2><a href="/inzerat/pronajem-bytu-2kk-praha-223238855-ab12.html">Pronájem bytu 2+kk Praha 7</a></h2>
  <table>
    <tr><th>Lokalita:</th><td>Praha 7</td></tr>
    <tr><th>Dispozice:</th><td>2+kk</td></tr>
    <tr><th>Plocha:</th><td>44 m2</td></tr>
  </table>
  <div class="price">24 000 Kč</div>
  <img src="https://static.annonce.cz/foto/1.jpg" />
</div>
<div class="rows js-more"></div>
"""

REMAX = """
<div class="pl-items__item" data-url="/reality/detail/445566/pronajem-bytu-2kk-praha-3" data-title="Pronájem bytu 2+kk" data-display-address="Praha 3" data-price="18 900 Kč" data-img="https://www.remax-czech.cz/foto.jpg">
  karta
</div>
"""

MM = """
<a href="/nemovitosti/pronajem-bytu-2kk-praha-778899">
  <h3>Pronájem bytu 2+kk, Praha 4</h3>
  <span>19 800 Kč</span>
  <img src="https://cdn.mmreality.cz/foto.jpg" />
</a>
"""

REALITYCZ = """
<a href="/detail/556677-pronajem-bytu-praha">
  <h2>Pronájem bytu 2+kk, Praha 10</h2>
  17 200 Kč
  <img src="/foto.jpg" />
</a>
"""

ULOV_HTML = """
<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"offers":[{"id":991122,"title":"Pronájem 2+kk Praha","price":{"amount":21000},"address":{"city":"Praha 8"},"seoUrl":"/pronajem/byt/praha/991122"}]}}}</script>
"""

ULOV_JSON = {"data": {"offers": [{"id": 42, "title": "Byt", "price": {"amount": 15000}, "seoUrl": "/pronajem/byt/42"}]}, "total": 1}


class ExtraPortalTests(unittest.TestCase):
    def test_url_builders(self):
        self.assertIn("/pronajem/byty/", ceskereality_url.build_url({"offers": ["pronajem"]}))
        rent = annonce_url.build_url({"offers": ["pronajem"]})
        self.assertIn("/byty-k-pronajmu.html", rent)
        self.assertIn("nabidkovy=1", rent)
        houses = annonce_url.build_url({"offers": ["pronajem"], "category": "domy"})
        self.assertIn("/domy-k-pronajmu.html", houses)
        self.assertIn("nabidkovy=1", houses)
        self.assertIn("typ-nabidky=pronajem", mmreality_url.build_url({"offers": ["pronajem"]}))
        self.assertTrue(ulovdomov_url.build_url({"offers": ["pronajem"]}).endswith("/pronajem/byty"))
        ulov_houses = ulovdomov_url.build_url({"offers": ["pronajem"], "category": "domy"})
        self.assertTrue(ulov_houses.endswith("/pronajem/domy"))
        self.assertEqual(
            ulovdomov_url.parse_url("https://www.ulovdomov.cz/prodej/domy")["category"],
            "domy",
        )
        self.assertEqual(
            ulovdomov_url.parse_url("https://www.ulovdomov.cz/prodej/byty")["offers"],
            ["prodej"],
        )
        rent = remax_url.build_url({"offers": ["pronajem"]})
        self.assertIn("/reality/byty/pronajem/", rent)
        self.assertIn("order_by_published_date=0", rent)
        houses = remax_url.build_url({"offers": ["pronajem"], "category": "domy"})
        self.assertIn("/reality/domy-a-vily/pronajem/", houses)
        self.assertIn("order_by_published_date=0", houses)
        self.assertEqual(
            remax_url.parse_url("https://www.remax-czech.cz/reality/domy-a-vily/prodej/?order_by_published_date=0")["category"],
            "domy",
        )
        rent = realitycz_url.build_url({"offers": ["pronajem"]})
        self.assertIn("/pronajem/byty/Ceska-republika/", rent)
        self.assertIn("s=2", rent)
        houses = realitycz_url.build_url({"offers": ["pronajem"], "category": "domy"})
        self.assertIn("/pronajem/domy/Ceska-republika/", houses)
        self.assertIn("s=2", houses)
        self.assertEqual(
            realitycz_url.parse_url("https://www.reality.cz/pronajem/domy/Ceska-republika/?s=2")["category"],
            "domy",
        )
        self.assertIn("/pronajem/byty/nejnovejsi/", ceskereality_url.build_url({"offers": ["pronajem"]}))
        ceske_houses = ceskereality_url.build_url({"offers": ["pronajem"], "category": "domy"})
        self.assertIn("/pronajem/rodinne-domy/nejnovejsi/", ceske_houses)
        self.assertNotIn("/pronajem/domy/", ceske_houses)
        self.assertEqual(
            ceskereality_url.parse_url("https://www.ceskereality.cz/pronajem/rodinne-domy/")["category"],
            "domy",
        )
        self.assertEqual(ceskereality_url.parse_url("https://www.ceskereality.cz/prodej/byty/")["offers"], ["prodej"])
        self.assertEqual(annonce_url.parse_url("https://www.annonce.cz/byty-na-prodej.html")["offers"], ["prodej"])
        self.assertEqual(remax_url.parse_url("https://www.remax-czech.cz/reality/byty/?sale=1")["offers"], ["prodej"])

    def test_ceskereality_list(self):
        client = CeskerealityClient("https://www.ceskereality.cz/pronajem/byty/")
        items = client._parse_list(CESKE)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].id, 123456)
        self.assertEqual(items[0].price_czk, 16500)
        self.assertIn("Žižkov", items[0].locality or items[0].name)

    def test_annonce_list(self):
        client = AnnonceClient("https://www.annonce.cz/byty-k-pronajmu.html")
        items = client._parse_list(ANNONCE)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].id, 223238855)
        self.assertEqual(items[0].disposition, "2+kk")
        self.assertEqual(items[0].area_m2, 44)
        self.assertEqual(items[0].price_czk, 24000)

    def test_annonce_slideshow_cards_keep_every_listing(self):
        html = (FIXTURES / "annonce_cards.html").read_text()
        client = AnnonceClient("https://www.annonce.cz/byty-k-pronajmu.html")
        items = client._parse_list(html)
        ids = [item.id for item in items]
        self.assertEqual(ids, [88323899, 88695951, 88672809, 88696077, 88159057])
        self.assertEqual(items[0].locality, "Praha 4")
        self.assertEqual(items[0].price_czk, 13500)
        self.assertIn("attachment", items[0].image_url or "")
        self.assertEqual(items[1].disposition, "3+1")
        self.assertEqual(items[1].locality, "Karlovy Vary")
        self.assertNotIn(88693793, ids)
        self.assertEqual(items[3].locality, "Praha 5")
        self.assertEqual(items[3].price_czk, 26000)
        self.assertTrue(items[4].url.endswith("88159057-w2713c.html"))
        self.assertEqual(client._page_url(2), "https://www.annonce.cz/byty-k-pronajmu.html?page=2")

    def test_remax_list(self):
        client = RemaxClient("https://www.remax-czech.cz/reality/byty/pronajem/?order_by_published_date=0")
        items = client._parse_list(REMAX)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].id, 445566)
        self.assertEqual(items[0].price_czk, 18900)
        self.assertIn("Praha 3", items[0].locality)

    def test_remax_live_cards_fill_price_image_locality_gps(self):
        from app.places import approx_point_from_locality
        from app.remax import parse_remax_gps

        html = (FIXTURES / "remax_cards.html").read_text()
        client = RemaxClient("https://www.remax-czech.cz/reality/byty/pronajem/")
        items = client._parse_list(html)
        self.assertEqual([item.id for item in items], [447815, 446000, 449001])
        first = items[0]
        self.assertEqual(first.price_czk, 70000)
        self.assertIn("th350.jpg", first.image_url or "")
        self.assertIn("Praha 2", first.locality)
        self.assertNotIn("ulice", (first.locality or "").casefold())
        self.assertAlmostEqual(first.lat, 50 + 4 / 60 + 40.5 / 3600, places=5)
        self.assertAlmostEqual(first.lon, 14 + 26 / 60 + 38.7 / 3600, places=5)
        self.assertEqual(first.disposition, "4+kk")
        self.assertEqual(first.area_m2, 130)
        self.assertIsNone(items[1].lat)
        self.assertEqual(items[1].locality, "Praha 7")
        self.assertEqual(items[1].price_czk, 18900)
        self.assertEqual(items[2].extras.get("estate"), "Dům")
        self.assertEqual(client._parse_total(html), 1097)
        gps = parse_remax_gps("50°04'40.5\"N,14°26'38.7\"E")
        self.assertIsNotNone(gps)
        self.assertIn("order_by_published_date=0", client._page_url(1, newest=True))
        self.assertNotIn("order_by_price", client._page_url(1, newest=True))
        self.assertIn("stranka=2", client._page_url(2, newest=True))
        pin = approx_point_from_locality("Praha 7")
        self.assertIsNotNone(pin)

    def test_remax_fetch_page_uses_html_gps_not_photon(self):
        import asyncio
        from unittest.mock import AsyncMock, patch

        import httpx

        from app.places import approx_point_from_locality

        html = (FIXTURES / "remax_cards.html").read_text()
        geocode = AsyncMock(side_effect=AssertionError("list fetch must not hit Nominatim/Photon"))
        hits: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hits.append(str(request.url))
            return httpx.Response(200, text=html)

        async def _run() -> None:
            client = RemaxClient("https://www.remax-czech.cz/reality/byty/?sale=2&order_by_price=0")
            await client.aclose()
            client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            try:
                with patch("app.places.geocode_locality", geocode), patch(
                    "app.places.geocode_locality_sync",
                    side_effect=AssertionError("list fetch must not geocode"),
                ):
                    listings, total = await client.fetch_page(1, newest=True)
            finally:
                await client.aclose()
            self.assertEqual(len(listings), 3)
            self.assertEqual(total, 1097)
            geocode.assert_not_called()
            self.assertTrue(any("order_by_published_date=0" in url for url in hits))
            self.assertFalse(any("order_by_price" in url for url in hits))
            by_id = {item.id: item for item in listings}
            self.assertAlmostEqual(by_id[447815].lat, 50 + 4 / 60 + 40.5 / 3600, places=5)
            praha7 = approx_point_from_locality("Praha 7")
            if praha7:
                self.assertEqual((by_id[446000].lat, by_id[446000].lon), praha7)

        asyncio.run(_run())

    def test_mmreality_list(self):
        client = MmrealityClient("https://www.mmreality.cz/nemovitosti/?typ-nabidky=pronajem")
        items = client._parse_list(MM)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].id, 778899)
        self.assertEqual(items[0].price_czk, 19800)

    def test_realitycz_list(self):
        client = RealityczClient("https://www.reality.cz/pronajem/byty/")
        items = client._parse_list(REALITYCZ)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].id, 556677)
        self.assertEqual(items[0].price_czk, 17200)

    def test_realitycz_maintenance_empty(self):
        client = RealityczClient("https://www.reality.cz/pronajem/byty/")
        self.assertEqual(client._parse_list("Probíhá údržba serveru"), [])

    def test_realitycz_fetch_page_uses_html_gps_not_photon(self):
        import asyncio
        from unittest.mock import AsyncMock, patch

        import httpx

        from app.places import approx_point_from_locality

        html = (FIXTURES / "realitycz_vypis.html").read_text()
        geocode = AsyncMock(side_effect=AssertionError("list fetch must not hit Nominatim/Photon"))
        hits: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hits.append(str(request.url))
            return httpx.Response(200, text=html)

        async def _run() -> None:
            client = RealityczClient("https://www.reality.cz/pronajem/byty/Ceska-republika/")
            await client.aclose()
            client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            try:
                with patch("app.places.geocode_locality", geocode), patch(
                    "app.places.geocode_locality_sync",
                    side_effect=AssertionError("list fetch must not geocode"),
                ):
                    listings, total = await client.fetch_page(1, newest=True)
            finally:
                await client.aclose()
            self.assertGreaterEqual(len(listings), 6)
            self.assertEqual(total, 841)
            geocode.assert_not_called()
            self.assertTrue(any("s=2" in url for url in hits))
            by_code = {item.advert_code: item for item in listings}
            self.assertEqual((by_code["L00-006971"].lat, by_code["L00-006971"].lon), (49.329672, 18.003328))
            hamry = approx_point_from_locality("Velké Hamry")
            if hamry:
                self.assertEqual((by_code["AUZ-N09768"].lat, by_code["AUZ-N09768"].lon), hamry)

        asyncio.run(_run())

    def test_bezrealitky_page_size_matches_peers(self):
        from app.bezrealitky import PAGE_SIZE, BezrealitkyClient

        self.assertEqual(PAGE_SIZE, 20)
        client = BezrealitkyClient(
            "https://www.bezrealitky.cz/vyhledat?offerType=PRONAJEM&estateType=BYT&order=TIMEORDER_DESC"
        )
        try:
            self.assertIn("limit: 20", client._args(1))
            self.assertIn("offset: 0", client._args(1))
            self.assertIn("offset: 20", client._args(2))
        finally:
            import asyncio

            asyncio.run(client.aclose())

    def test_ulovdomov_next_data_and_json(self):
        client = UlovdomovClient("https://www.ulovdomov.cz/pronajem/byty")
        items = client._parse_list(ULOV_HTML)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].id, 991122)
        self.assertEqual(items[0].price_czk, 21000)
        rows, total = offers_from_payload(ULOV_JSON)
        self.assertEqual(total, 1)
        listing = client.listing_from_offer(rows[0], "pronajem")
        self.assertEqual(listing.id, 42)
        self.assertEqual(listing.price_czk, 15000)

    def test_ceskereality_live_cards_use_listing_id_not_favorite_url(self):
        html = (FIXTURES / "ceskereality_cards.html").read_text()
        client = CeskerealityClient("https://www.ceskereality.cz/pronajem/byty/nejnovejsi/")
        items = client._parse_list(html)
        self.assertGreaterEqual(len(items), 2)
        self.assertEqual(items[0].id, 2432970)
        self.assertTrue(items[0].url.endswith("2432970.html"))
        self.assertNotIn("muj-profil", items[0].url)
        self.assertEqual(items[1].id, 3895290)
        self.assertEqual(items[1].price_czk, 23000)
        self.assertEqual(client._parse_total(html), 4779)

    def test_realitycz_vypis_and_novinky_yield(self):
        client = RealityczClient("https://www.reality.cz/pronajem/byty/Ceska-republika/")
        vypis = client._parse_list((FIXTURES / "realitycz_vypis.html").read_text())
        novinky = client._parse_list((FIXTURES / "realitycz_novinky.html").read_text())
        self.assertGreaterEqual(len(vypis), 6, [item.advert_code for item in vypis])
        self.assertTrue(any(item.advert_code == "DMQ-003729" for item in vypis))
        self.assertTrue(any("Troja" in f"{item.locality} {item.name}" for item in vypis))
        by_code = {item.advert_code: item for item in vypis}
        self.assertEqual(by_code["L00-006971"].locality, "Vsetín")
        self.assertEqual(by_code["L00-006971"].lat, 49.329672)
        self.assertEqual(by_code["L00-006971"].lon, 18.003328)
        self.assertEqual(by_code["AUZ-N09768"].locality, "Velké Hamry")
        self.assertEqual(by_code["AUZ-N09767"].locality, "Vítkovice")
        self.assertIsNotNone(by_code["AUZ-N09767"].lat)
        self.assertEqual(by_code["EXN-HSZJIV"].advert_code, "EXN-HSZJIV")
        self.assertTrue(by_code["EXN-HSZJIV"].price_czk)
        self.assertTrue(by_code["EXN-HSZJIV"].image_url)
        self.assertIn("/thumb/", by_code["DMQ-003729"].image_url or "")
        self.assertNotIn("makler", (by_code["DMQ-003729"].image_url or "").casefold())
        self.assertEqual(by_code["DMQ-003729"].disposition, "2+kk")
        self.assertEqual(by_code["DMQ-003729"].area_m2, 50)
        self.assertEqual(client._parse_total((FIXTURES / "realitycz_vypis.html").read_text()), 841)
        self.assertGreaterEqual(len(novinky), 2)
        self.assertTrue(all(item.url.startswith("https://www.reality.cz/") for item in vypis + novinky))
        self.assertIn("s=2", client._page_url(1, newest=True))
        self.assertIn("g=1-2", client._page_url(2, newest=True))
        self.assertNotIn("strana", client._page_url(2, newest=True))
        self.assertEqual(
            client._parse_total("Příliš široká kriteria výběru, zobrazuji prvních 1.000 nabídek."),
            1000,
        )

    def test_mmreality_jsonld_and_cloudflare_not_listings(self):
        client = MmrealityClient("https://www.mmreality.cz/nemovitosti/?typ-nabidky=pronajem")
        cloudflare = (FIXTURES / "mm_cloudflare.html").read_text()
        self.assertEqual(client._parse_list(cloudflare), [])
        self.assertIsNotNone(classify_block(403, cloudflare, {"server": "cloudflare"}))
        html = """
        <script type="application/ld+json">
        {"@type":"ItemList","itemListElement":[
          {"@type":"ListItem","item":{"@type":"Offer","name":"Pronájem 2+kk Praha",
           "url":"https://www.mmreality.cz/nemovitosti/pronajem-bytu-2kk-praha-445566",
           "offers":{"price":18500},"address":{"addressLocality":"Praha 5"}}}
        ]}
        </script>
        """
        items = client._parse_list(html)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].id, 445566)
        self.assertEqual(items[0].price_czk, 18500)

    def test_ulovdomov_empty_ssr_and_nested_payload(self):
        client = UlovdomovClient("https://www.ulovdomov.cz/pronajem/byty")
        empty = client._parse_list((FIXTURES / "ulov_empty_ssr.html").read_text())
        self.assertEqual(empty, [])
        nested = {
            "props": {
                "pageProps": {
                    "dehydratedState": {
                        "queries": [
                            {
                                "state": {
                                    "data": {
                                        "offers": [
                                            {
                                                "id": 77,
                                                "title": "Nested",
                                                "price": {"amount": 12000},
                                                "seoUrl": "/pronajem/byt/77",
                                            }
                                        ]
                                    }
                                }
                            }
                        ]
                    }
                }
            }
        }
        rows, total = offers_from_payload(nested)
        self.assertEqual(total, 1)
        listing = client.listing_from_offer(rows[0], "pronajem")
        self.assertEqual(listing.id, 77)
        self.assertEqual(listing.price_czk, 12000)

    def test_ulovdomov_sitemap_slug_and_cards(self):
        xml = (FIXTURES / "ulov_sitemap_offers.xml").read_text()
        rows = parse_sitemap_offers(xml)
        self.assertEqual(len(rows), 6)
        by_id = {item[2]: item for item in rows}
        self.assertEqual(by_id[2037015][1], "pronajem")
        self.assertEqual(by_id[5669330][1], "prodej")
        self.assertEqual(by_id[5446569][1], "spolubydleni")
        self.assertEqual(by_id[5222881][1], "pronajem")
        self.assertEqual(by_id[5653004][1], "prodej")
        self.assertEqual(offer_from_inzerat_slug("-hluboka-nad-vltavou-housing"), "prodej")
        name, locality, disp = fields_from_inzerat_slug("pronajem-praha-liben-na-korabe-1-kk", "pronajem")
        self.assertEqual(disp, "1+kk")
        self.assertIn("Praha", locality)
        from app.ulovdomov import estate_from_inzerat_slug

        self.assertEqual(estate_from_inzerat_slug("pronajem-troubsko-troubsko-troubsko-dum"), "dum")
        self.assertEqual(estate_from_inzerat_slug("-senohraby-senohraby-ve-vilach-fiveplusrooms"), "dum")
        self.assertEqual(estate_from_inzerat_slug("-hluboka-nad-vltavou-zahradni-housing"), "byt")
        self.assertEqual(estate_from_inzerat_slug("pronajem-praha-krc-u-novych-domu-i-2-1"), "byt")
        client = UlovdomovClient("https://www.ulovdomov.cz/pronajem/byty")
        listing = client.listing_from_sitemap_url(by_id[3496443][0], "pronajem", by_id[3496443][3], 3496443)
        self.assertEqual(listing.id, 3496443)
        self.assertEqual(listing.disposition, "2+kk")
        self.assertTrue(listing.url.endswith("/3496443"))
        house = client.listing_from_sitemap_url(by_id[5222881][0], "pronajem", by_id[5222881][3], 5222881)
        self.assertEqual(house.extras.get("estate"), "Dům")
        self.assertIn("domu", house.name.casefold())

    def test_ceskereality_live_nejnovejsi_uses_html_id_not_firm_image(self):
        html = (FIXTURES / "ceskereality_nejnovejsi.html").read_text()
        client = CeskerealityClient("https://www.ceskereality.cz/pronajem/byty/nejnovejsi/")
        items = client._parse_list(html)
        self.assertEqual([item.id for item in items], [3895307, 3895297])
        self.assertTrue(items[0].url.endswith("3895307.html"))
        self.assertTrue(items[1].url.endswith("3895297.html"))
        self.assertIn("/nejnovejsi/", items[1].url)
        self.assertNotIn("muj-profil", items[0].url)
        self.assertEqual(items[0].price_czk, 16000)
        self.assertEqual(items[1].price_czk, 22000)
        self.assertEqual(client._parse_total(html), 4782)
        self.assertFalse(any(item.id in {200631, 5025350} for item in items))

    def test_ceskereality_empty_nejnovejsi_falls_back_to_base_list(self):
        import asyncio

        import httpx

        empty = "<html><body>Hledáte nové byty k pronájmu? Máme tady 4 782 bytů.</body></html>"
        cards = (FIXTURES / "ceskereality_nejnovejsi.html").read_text()
        hits: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hits.append(str(request.url.path))
            if "/nejnovejsi/" in request.url.path:
                return httpx.Response(200, text=empty)
            return httpx.Response(200, text=cards)

        async def _run() -> None:
            client = CeskerealityClient("https://www.ceskereality.cz/pronajem/byty/nejnovejsi/")
            await client.aclose()
            client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            try:
                listings, total = await client.fetch_page(1, newest=True)
            finally:
                await client.aclose()
            self.assertEqual([item.id for item in listings], [3895307, 3895297])
            self.assertEqual(total, 4782)
            self.assertEqual(hits, ["/pronajem/byty/nejnovejsi/", "/pronajem/byty/"])

        asyncio.run(_run())

    def test_measured_fixture_yield(self):
        """Before/after counts on recorded HTML. Old parsers missed vypis / used favorite URLs."""
        ceske_html = (FIXTURES / "ceskereality_cards.html").read_text()
        rcz_html = (FIXTURES / "realitycz_vypis.html").read_text()
        ceske = CeskerealityClient("https://www.ceskereality.cz/pronajem/byty/")._parse_list(ceske_html)
        rcz = RealityczClient("https://www.reality.cz/pronajem/byty/Ceska-republika/")._parse_list(rcz_html)
        report = {
            "ceskereality_cards": {"before": "20 live cards, but favorite URL + firm id", "after": len(ceske)},
            "realitycz_vypis": {"before": 0, "after": len(rcz)},
        }
        self.assertGreaterEqual(report["ceskereality_cards"]["after"], 2)
        self.assertGreaterEqual(report["realitycz_vypis"]["after"], 6)
        self.assertTrue(all("nemovitosti" in item.url or item.url.endswith(".html") for item in ceske))
        self.assertTrue(all(item.id in {2432970, 3895290} for item in ceske))


if __name__ == "__main__":
    unittest.main()
