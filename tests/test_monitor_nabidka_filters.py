from types import SimpleNamespace

from app import url_builder
from app.catalog_sync import listing_matches_search, normalize_search_url


def _listing(extras, disposition="2+kk"):
    return SimpleNamespace(
        url="https://www.sreality.cz/detail/pronajem/byt/x",
        extras={"offer": "Pronájem", **extras},
        price_czk=1,
        area_m2=50,
        locality="Praha 1",
        disposition=disposition,
        name="x",
        price_label="1 Kč/měsíc",
        description="",
    )


def _url(**kw):
    filters = url_builder.default_filters()
    filters.update(districts=[], sizes=["2+kk"], **kw)
    return url_builder.build_url(filters)


def test_nabidka_filters_survive_url_roundtrip():
    url = _url(nb_ownership=["Osobní"], nb_pets="s", nb_disp=["5+kk"], estate=["byt"])
    parsed = url_builder.parse_url(normalize_search_url(url))
    assert parsed["nb_ownership"] == ["Osobní"]
    assert parsed["nb_pets"] == "s"
    assert parsed["nb_disp"] == ["5+kk"]
    assert parsed["estate"] == ["byt"]


def test_listing_matches_ownership_building_and_extra_disposition():
    url = _url(nb_ownership=["Osobní"], nb_building=["cihlova"], nb_disp=["5+kk"])
    extras = {"params": [{"name": "Vlastnictví", "value": "Osobní"}], "building": "Cihlová"}
    assert listing_matches_search(_listing(extras), url, ignore_source=True)
    assert listing_matches_search(_listing(extras, "5+kk"), url, ignore_source=True)
    assert not listing_matches_search(_listing(extras, "3+kk"), url, ignore_source=True)
    assert not listing_matches_search(_listing({"building": "Cihlová"}), url, ignore_source=True)


def test_tri_state_and_estate():
    with_pets = _url(nb_pets="s")
    without_pets = _url(nb_pets="bez")
    assert listing_matches_search(_listing({"pets": True}), with_pets, ignore_source=True)
    assert not listing_matches_search(_listing({}), with_pets, ignore_source=True)
    assert listing_matches_search(_listing({}), without_pets, ignore_source=True)
    assert not listing_matches_search(_listing({"estate": "Pozemek"}), _url(estate=["byt", "dum"]), ignore_source=True)
