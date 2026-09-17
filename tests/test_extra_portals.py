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
from app.ulovdomov import UlovdomovClient, offers_from_payload


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
        self.assertTrue(realitycz_url.build_url({"offers": ["pronajem"]}).endswith("/pronajem/byty/"))
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

    def test_parse_total_is_bounded(self):
        from app.html_listing import parse_total

        self.assertEqual(parse_total("Nalezeno 1 234 inzerátů v Praze"), 1234)
        blob = ("123 456 789 <script>var x=1;</script> " * 8_000)
        started = __import__("time").perf_counter()
        self.assertEqual(parse_total(blob), 0)
        self.assertLess(__import__("time").perf_counter() - started, 0.05)


if __name__ == "__main__":
    unittest.main()
