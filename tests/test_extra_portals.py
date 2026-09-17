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
        self.assertTrue(annonce_url.build_url({"offers": ["pronajem"]}).endswith("/byty-k-pronajmu.html"))
        self.assertIn("typ-nabidky=pronajem", mmreality_url.build_url({"offers": ["pronajem"]}))
        self.assertTrue(ulovdomov_url.build_url({"offers": ["pronajem"]}).endswith("/pronajem/byty"))
        self.assertIn("sale=2", remax_url.build_url({"offers": ["pronajem"]}))
        self.assertTrue(realitycz_url.build_url({"offers": ["pronajem"]}).endswith("/pronajem/byty/Ceska-republika/"))
        self.assertIn("/pronajem/byty/nejnovejsi/", ceskereality_url.build_url({"offers": ["pronajem"]}))
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

    def test_remax_list(self):
        client = RemaxClient("https://www.remax-czech.cz/reality/byty/?sale=2")
        items = client._parse_list(REMAX)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].id, 445566)
        self.assertEqual(items[0].price_czk, 18900)
        self.assertIn("Praha 3", items[0].locality)

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
        self.assertGreaterEqual(len(vypis), 2, vypis)
        self.assertTrue(any(item.advert_code == "DMQ-003729" for item in vypis))
        self.assertTrue(any("Troja" in f"{item.locality} {item.name}" for item in vypis))
        self.assertGreaterEqual(len(novinky), 2)
        self.assertTrue(all(item.url.startswith("https://www.reality.cz/") for item in vypis + novinky))

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
        self.assertEqual(len(rows), 4)
        by_id = {item[2]: item for item in rows}
        self.assertEqual(by_id[2037015][1], "pronajem")
        self.assertEqual(by_id[5669330][1], "prodej")
        self.assertEqual(by_id[5446569][1], "spolubydleni")
        self.assertEqual(offer_from_inzerat_slug("-hluboka-nad-vltavou-housing"), "prodej")
        name, locality, disp = fields_from_inzerat_slug("pronajem-praha-liben-na-korabe-1-kk", "pronajem")
        self.assertEqual(disp, "1+kk")
        self.assertIn("Praha", locality)
        client = UlovdomovClient("https://www.ulovdomov.cz/pronajem/byty")
        listing = client.listing_from_sitemap_url(by_id[3496443][0], "pronajem", by_id[3496443][3], 3496443)
        self.assertEqual(listing.id, 3496443)
        self.assertEqual(listing.disposition, "2+kk")
        self.assertTrue(listing.url.endswith("/3496443"))

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
        self.assertGreaterEqual(report["realitycz_vypis"]["after"], 2)
        self.assertTrue(all("nemovitosti" in item.url or item.url.endswith(".html") for item in ceske))
        self.assertTrue(all(item.id in {2432970, 3895290} for item in ceske))


if __name__ == "__main__":
    unittest.main()
