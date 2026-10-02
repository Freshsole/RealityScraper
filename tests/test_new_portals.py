"""Testy pro nové scrapery a deduplikaci."""

from unittest.mock import Mock

from app.dedup import (
    are_duplicates,
    area_close,
    dedup_key,
    normalize_address,
    normalize_disposition,
    price_close,
)


def _mock_client(cls, url):
    mock = Mock()
    mock.search_url = url
    return mock


def test_eurobydleni_parse():
    from app.eurobydleni import EurobydleniClient
    html = """
    <li class="list-items__item " data-deleted="0" itemid="/pronajem-bytu/test/detail/12345/">
    <a href="/pronajem-bytu/test/detail/12345/" itemprop="url">Pronájem bytu 2+kk, Praha</a>
    <span>18 000Kč/měsíc</span>
    <meta itemprop="addressLocality" content="Praha 5">
    <meta itemprop="streetAddress" content="Krnovská 1">
    </li><ul>
    """
    mock = _mock_client(EurobydleniClient, "https://www.eurobydleni.cz/byty/praha/pronajem/")
    listings = EurobydleniClient._parse_list(mock, html)
    assert len(listings) == 1
    assert "2+kk" in listings[0].name
    assert listings[0].price_czk == 18000


def test_realitymix_parse():
    from app.realitymix import RealitymixClient
    html = """
    <li class="w-full advert-item">
    <a href="https://realitymix.cz/detail/praha/byt-2kk-1234567.html">detail</a>
    <h3 class="text-lg sm:text-xl font-extrabold">Byt 2+kk Praha</h3>
    <p class="text-sm sm:text-base text-body-light">Praha 5, Radlice</p>
    <span>19 000 Kč/měsíc</span>
    </li>
    """
    mock = _mock_client(RealitymixClient, "https://realitymix.cz/reality/byty/pronajem/praha")
    listings = RealitymixClient._parse_list(mock, html)
    assert len(listings) == 1
    assert listings[0].price_czk == 19000
    assert "Radlice" in listings[0].locality


def test_realingo_parse():
    from app.realingo import RealingoClient
    html = """
    <script type="application/ld+json">{"@type": "SingleFamilyResidence",
    "name": "2+kk, Praha", "url": "/pronajem/byt-praha/12345678",
    "address": "Praha", "image": "/img.jpg"}</script>
    """
    mock = _mock_client(RealingoClient, "https://www.realingo.cz/pronajem_reality/cr/")
    listings = RealingoClient._parse_list(mock, html)
    assert len(listings) == 1
    assert listings[0].name == "2+kk, Praha"


def test_espolubydleni_parse():
    from app.espolubydleni import EspolubydleniClient
    html = """
    <a href="/podnajem-spolubydlici/12345-test.htm"
    title="Nabízím 1x místo, byt 2+kk
 Plzeň
8.000Kč/měsíc">odkaz</a>
    """
    mock = _mock_client(EspolubydleniClient, "https://www.espolubydleni.cz/podnajem-spolubydlici/")
    listings = EspolubydleniClient._parse_list(mock, html)
    assert len(listings) == 1
    assert listings[0].price_czk == 8000
    assert listings[0].locality == "Plzeň"


def test_normalize_address():
    assert normalize_address("Praha 5, Radlice") == "praha 5 radlice"
    assert normalize_address("PRAHA") == "praha"
    assert "krnovska" in normalize_address("ul. Krnovská")


def test_normalize_disposition():
    assert normalize_disposition("2 + kk") == "2+kk"
    assert normalize_disposition("2+KK") == "2+kk"


def test_price_close():
    assert price_close(19000, 19500)  # 2.6% rozdíl
    assert not price_close(19000, 25000)  # 31% rozdíl
    assert not price_close(None, 19000)


def test_area_close():
    assert area_close(55, 56)
    assert not area_close(55, 70)


def test_dedup_key():
    k1 = dedup_key("Praha 5, Radlice", "2+kk", 55, 19000)
    k2 = dedup_key("praha 5 radlice", "2 + kk", 56, 19200)
    assert k1 == k2


def test_are_duplicates():
    a = {"locality": "Praha 5, Radlice", "disposition": "2+kk", "area_m2": 55, "price_czk": 19000}
    b = {"locality": "praha 5 radlice", "disposition": "2 + kk", "area_m2": 56, "price_czk": 19500}
    assert are_duplicates(a, b)

    c = {"locality": "Brno", "disposition": "2+kk", "area_m2": 55, "price_czk": 19000}
    assert not are_duplicates(a, c)

    d = {"locality": "Praha 5, Radlice", "disposition": "3+1", "area_m2": 80, "price_czk": 25000}
    assert not are_duplicates(a, d)
