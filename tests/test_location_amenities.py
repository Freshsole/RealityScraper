from app.location_amenities import score_from_elements


def _el(kind_tags: dict, lat: float, lon: float, name: str = "") -> dict:
    tags = dict(kind_tags)
    if name:
        tags["name"] = name
    return {"type": "node", "lat": lat, "lon": lon, "tags": tags}


def test_holesovice_like_amenities_score_high():
    lat, lon = 50.0998, 14.4319
    elements = [
        _el({"railway": "tram_stop"}, lat + 0.0004, lon + 0.0002, "Veverkova"),
        _el({"shop": "supermarket"}, lat + 0.001, lon - 0.0005, "Albert"),
        _el({"shop": "mall"}, lat + 0.003, lon + 0.002, "Stromovka Galerie"),
        _el({"leisure": "park"}, lat + 0.002, lon - 0.001, "Stromovka"),
        _el({"amenity": "cafe"}, lat + 0.0006, lon, "Café"),
        _el({"amenity": "restaurant"}, lat - 0.0005, lon + 0.0004, "Bistro"),
        _el({"amenity": "pharmacy"}, lat + 0.0008, lon + 0.0003, "Lékárna"),
    ]
    result = score_from_elements(
        lat,
        lon,
        elements,
        locality="Veverkova 731/19, Praha 7 – Holešovice",
        text="kousek od zastávky tramvají Stromovka obchodní centrum",
    )
    assert result["score"] >= 7.5
    assert result["source"] == "osm"
    assert any("Tramvaj" in f for f in result["factors"])


def test_remote_location_scores_lower():
    lat, lon = 50.0, 14.0
    elements = [
        _el({"highway": "bus_stop"}, lat + 0.004, lon, "Bus"),
    ]
    result = score_from_elements(lat, lon, elements, locality="Odlehlá obec", text="")
    assert result["score"] < 6.0


def test_josefov_heuristic_is_strong():
    result = score_from_elements(
        0,
        0,
        [],
        locality="Kozí 852/21, Praha 1 – Josefov",
        text="",
    )
    assert result["score"] >= 7.5
    assert result["source"] == "heuristic"


def test_heuristic_from_district_text():
    result = score_from_elements(
        0,
        0,
        [],
        locality="Praha - Holešovice",
        text="tramvaj metro park albert",
    )
    assert result["score"] >= 7.0
    assert result["note"]
