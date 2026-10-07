import unittest

from app.annonce import AnnonceClient
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
from app.ulovdomov import UlovdomovClient, listing_from_detail, offers_from_payload


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
<a href="https://www.mmreality.cz/nemovitosti/778899/" data-card-id="778899" class="tw-rounded-responsive">
<article class="rds-image-card rds-property-preview-card">
<img src="https://cdn.mmreality.cz/medium/offer/ab/12/foto.jpg" />
<button type="button" data-favorite-toggle="778899" data-realty-id="778899" data-realty-name="Pronájem, Byt 2+kk, 52 m², Praha, Žižkov" data-realty-price="19 800 Kč">fav</button>
</article>
</a>
"""

REALITYCZ = """
<div class="xvypis vypismd ui-corner-all gpsx0 gpsy0" id="iddmq003730">
  <div class="thumbnail">
    <a href="DMQ-003730/?c=1_48919499_48919501_48919339"><img src="/thumb/1790780386/dmq003730_0.jpg" alt="" /></a><br />
  </div>
  <div class="obaltextu">
    <p class="vypisnaz">
      <a href="DMQ-003730/?c=1_48919499_48919501_48919339">Nad Rokoskou, Praha 8 - Libeň</a>
    </p>
    <p class="lokalita mb10">
      byt 2+kk, 69 m², cihla, osobní
    </p>
    <p class="vypiscena"><span class=""><strong>25.900 Kč/měs.</strong></span></p>
  </div>
</div>
"""

ULOV_HTML = """
<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"offers":[{"id":991122,"title":"Pronájem 2+kk Praha","price":{"amount":21000},"address":{"city":"Praha 8"},"seoUrl":"/pronajem/byt/praha/991122"}]}}}</script>
"""

ULOV_JSON = {"data": {"offers": [{"id": 42, "title": "Byt", "price": {"amount": 15000}, "seoUrl": "/pronajem/byt/42"}]}, "total": 1}


class ExtraPortalTests(unittest.TestCase):
    def test_url_builders(self):
        self.assertIn("/pronajem/byty/", ceskereality_url.build_url({"offers": ["pronajem"]}))
        self.assertTrue(annonce_url.build_url({"offers": ["pronajem"]}).endswith("/byty-k-pronajmu.html"))
        self.assertIn("/nemovitosti/pronajem/", mmreality_url.build_url({"offers": ["pronajem"]}))
        self.assertTrue(ulovdomov_url.build_url({"offers": ["pronajem"]}).endswith("/pronajem/byty"))
        self.assertIn("sale=2", remax_url.build_url({"offers": ["pronajem"]}))
        self.assertTrue(realitycz_url.build_url({"offers": ["pronajem"]}).endswith("/pronajem/byty/hlavni-mesto-Praha/"))
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
        client = MmrealityClient("https://www.mmreality.cz/nemovitosti/pronajem/")
        items = client._parse_list(MM)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].id, 778899)
        self.assertEqual(items[0].price_czk, 19800)
        self.assertIn("2+kk", items[0].name)
        self.assertEqual(items[0].disposition, "2+kk")
        self.assertEqual(items[0].area_m2, 52)
        self.assertIn("Praha", items[0].locality)
        self.assertTrue(items[0].url.endswith("/nemovitosti/778899/"))

    def test_mmreality_url_builder(self):
        self.assertTrue(
            mmreality_url.build_url({"offers": ["pronajem"]}).endswith("/nemovitosti/pronajem/")
        )
        self.assertTrue(
            mmreality_url.build_url({"offers": ["prodej"]}).endswith("/nemovitosti/prodej/")
        )
        self.assertEqual(
            mmreality_url.parse_url("https://www.mmreality.cz/nemovitosti/pronajem/")["offers"],
            ["pronajem"],
        )
        self.assertEqual(
            mmreality_url.parse_url("https://www.mmreality.cz/nemovitosti/prodej/")["offers"],
            ["prodej"],
        )

    def test_realitycz_list(self):
        client = RealityczClient("https://www.reality.cz/pronajem/byty/hlavni-mesto-Praha/")
        items = client._parse_list(REALITYCZ)
        self.assertEqual(len(items), 1)
        self.assertIn("DMQ-003730", items[0].url)
        self.assertEqual(items[0].price_czk, 25900)
        self.assertIn("Libeň", items[0].name)

    def test_realitycz_maintenance_empty(self):
        client = RealityczClient("https://www.reality.cz/pronajem/byty/hlavni-mesto-Praha/")
        self.assertEqual(client._parse_list("Probíhá údržba serveru"), [])

    def test_realitycz_js_warning_with_listings_parses(self):
        # The page contains a noscript JS warning, but listings are
        # server-rendered in HTML - they must still be parsed.
        client = RealityczClient("https://www.reality.cz/pronajem/byty/hlavni-mesto-Praha/")
        html = (
            "<noscript>Váš prohlížeč má vypnutý Javascript. "
            "Bez zapnutí Javascriptu tato stránka nebude správně fungovat.</noscript>"
            + REALITYCZ
        )
        items = client._parse_list(html)
        self.assertEqual(len(items), 1)

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

    def test_ulovdomov_detail_mapper(self):
        data = {
            "id": 3496443,
            "offerTypeId": "rent",
            "title": "Pronájem bytu 1+kk 18 m2",
            "status": "ACTIVE",
            "seo": "pronajem-usti-nad-labem-predlice-tovarni-1-kk",
            "absoluteUrl": "https://www.ulovdomov.cz/inzerat/pronajem-usti-nad-labem-predlice-tovarni-1-kk/3496443",
            "rentalPrice": {"value": 6000, "currency": "CZK"},
            "priceNote": "+ voda + elektřina",
            "street": {"name": "Tovární"},
            "village": {"name": "Ústí nad Labem"},
            "geoCoordinates": {"lat": 50.65715, "lng": 14.00863},
            "photos": [{"path": "https://storage.livendo.eu/cdn/image?id=abc&size=large"}],
            "parameters": {
                "disposition": {"options": [{"title": "1+kk"}]},
                "floorArea": {"value": "18 m2"},
            },
        }
        listing = listing_from_detail(data)
        self.assertIsNotNone(listing)
        self.assertEqual(listing.id, 3496443)
        self.assertEqual(listing.price_czk, 6000)
        self.assertEqual(listing.disposition, "1+kk")
        self.assertEqual(listing.area_m2, 18)
        self.assertIn("Ústí nad Labem", listing.locality)
        self.assertAlmostEqual(listing.lat, 50.65715, places=4)
        # Neaktivní nabídky se mapují na None (nesmí se dostat do katalogu).
        self.assertIsNone(listing_from_detail({**data, "status": "INACTIVE"}))
        self.assertIsNone(listing_from_detail({**data, "status": "DELETED"}))

    def test_ulovdomov_sitemap_fallback_on_find_500(self):
        import asyncio

        from app import ulovdomov as ulov_mod

        client = UlovdomovClient("https://www.ulovdomov.cz/pronajem/byty")

        class FakeResp:
            def __init__(self, status_code, text="", payload=None):
                self.status_code = status_code
                self._text = text
                self._payload = payload

            @property
            def text(self):
                return self._text

            def json(self):
                return self._payload

            def raise_for_status(self):
                pass

        sitemap_xml = (
            "<urlset>"
            "<url><loc>https://www.ulovdomov.cz/inzerat/pronajem-praha/111</loc></url>"
            "<url><loc>https://www.ulovdomov.cz/inzerat/prodej-praha/222</loc></url>"
            "<url><loc>https://www.ulovdomov.cz/inzerat/-praha-oneroom/333</loc></url>"
            "</urlset>"
        )
        detail_payload = {
            "success": True,
            "data": {
                "id": 111,
                "offerTypeId": "rent",
                "title": "Pronájem bytu 2+kk",
                "status": "ACTIVE",
                "absoluteUrl": "https://www.ulovdomov.cz/inzerat/pronajem-praha/111",
                "rentalPrice": {"value": 20000, "currency": "CZK"},
            },
        }

        async def fake_request(client_, method, url, **kwargs):
            if url == ulov_mod.API:
                return FakeResp(500)
            if url == ulov_mod.SITEMAP_URL:
                return FakeResp(200, text=sitemap_xml)
            if url == ulov_mod.DETAIL_API:
                return FakeResp(200, payload=detail_payload)
            raise AssertionError(f"neočekávané URL: {url}")

        async def run():
            import app.scrape_http as sh

            async def spy(c, method, url, **kw):
                return await fake_request(c, method, url, **kw)

            real = sh.request_with_log
            sh.request_with_log = spy
            try:
                return await client.fetch_page(1)
            finally:
                sh.request_with_log = real
                await client.aclose()

        listings, total = asyncio.run(run())
        # Jen pronájem ze sitemap (prodej se přeskočí), detail se namapuje.
        self.assertEqual(total, 1)
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0].id, 111)
        self.assertEqual(listings[0].price_czk, 20000)

    def test_parse_total_is_bounded(self):
        from app.html_listing import parse_total

        self.assertEqual(parse_total("Nalezeno 1 234 inzerátů v Praze"), 1234)
        blob = ("123 456 789 <script>var x=1;</script> " * 8_000)
        started = __import__("time").perf_counter()
        self.assertEqual(parse_total(blob), 0)
        self.assertLess(__import__("time").perf_counter() - started, 0.05)

    def test_parse_price_never_returns_card_blob(self):
        from app.html_listing import parse_price, sanitize_price_label

        blob = (
            "Pronájem bytu 2+kk 62 m² - Olomouc. ID nabídky: - inzerát | inzerce na Annonce.cz "
            "dataLayer = []; dataLayer.push({\"version\":\"live\"}); Olomouc 19 900 Kč Pronájem"
        )
        amount, label = parse_price(blob)
        self.assertEqual(amount, 19900)
        self.assertEqual(label, "19 900 Kč")
        self.assertNotIn("dataLayer", label)
        cleaned = sanitize_price_label(19900, blob, rent=True)
        self.assertEqual(cleaned, "19 900 Kč/měsíc")
        # Even without price_czk, recover the amount from a junk blob.
        self.assertEqual(sanitize_price_label(None, blob), "19 900 Kč")
        self.assertEqual(sanitize_price_label(None, "dataLayer = [];"), "Cena neuvedena")


if __name__ == "__main__":
    unittest.main()
