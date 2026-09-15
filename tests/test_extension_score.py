from app.extension_score import (
    _price_vs_label,
    _score_components,
    _tier_for,
    extract_sreality_id,
    normalize_url,
    not_found_payload,
)


def test_extract_sreality_id():
    assert extract_sreality_id("52351052") == "52351052"
    assert (
        extract_sreality_id("https://www.sreality.cz/detail/pronajem/byt/2+kk/praha-vysehrad/52351052")
        == "52351052"
    )


def test_normalize_url():
    assert normalize_url("/detail/pronajem/byt/2+kk/praha/1") == "https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/1"


def test_tiers_and_labels():
    assert _tier_for(40)["key"] == "low"
    assert _tier_for(65)["label"] == "Dobrý deal"
    assert _tier_for(89)["key"] == "high"
    assert _price_vs_label(-8) == "8% pod průměrem"
    assert 50 <= _score_components(price_vs_pct=-8, days=31, trend=-2, agency_label="Realitka") <= 90
    assert not_found_payload("9")["found"] is False


def test_rental_detection():
    from app.extension_score import _is_rental

    assert _is_rental(
        {
            "url": "https://www.sreality.cz/detail/pronajem/byt/1+kk/praha/1",
            "price_label": "23 400 Kč/měsíc",
            "price_czk": 23400,
            "extras": {"offer": "Pronájem"},
        }
    )
    assert not _is_rental(
        {
            "url": "https://www.sreality.cz/detail/prodej/byt/2+kk/praha/2",
            "price_label": "8 890 000 Kč",
            "price_czk": 8890000,
            "extras": {"offer": "Prodej"},
        }
    )
