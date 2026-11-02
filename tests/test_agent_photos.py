from app import agents


class _FakeStore:
    def catalog_item(self, monitor_id, listing_id=None, **_):
        if listing_id == 1:
            return {
                "id": 1,
                "name": "Byt 2+kk",
                "url": "https://example.cz/1",
                "photos": ["//cdn.example.cz/a.jpg", "https://cdn.example.cz/b.jpg"],
                "image_url": "https://cdn.example.cz/a.jpg",
            }
        return None


def test_tool_listed():
    assert "get_listing_photos" in {t["name"] for t in agents.TOOLS}


def test_photos_returned_as_image_blocks(monkeypatch):
    seen = {}

    def fake_download(urls, limit):
        seen["urls"], seen["limit"] = urls, limit
        return [{"type": "image", "data": "QUJD", "mimeType": "image/jpeg"}], 1

    monkeypatch.setattr(agents, "download_photos", fake_download)
    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "get_listing_photos", "arguments": {"monitor_id": "m", "listing_id": 1}},
    }
    res = agents.handle_mcp(_FakeStore(), msg)["result"]
    assert res["isError"] is False
    assert res["content"][1] == {"type": "image", "data": "QUJD", "mimeType": "image/jpeg"}
    assert "_images" not in res["content"][0]["text"]
    assert seen["urls"] == ["https://cdn.example.cz/a.jpg", "https://cdn.example.cz/b.jpg"]
    assert seen["limit"] == 3


def test_missing_listing_is_error():
    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "get_listing_photos", "arguments": {"monitor_id": "m", "listing_id": 99}},
    }
    assert agents.handle_mcp(_FakeStore(), msg)["result"]["isError"] is True


def test_private_hosts_rejected():
    assert agents._public_host("http://127.0.0.1/x.jpg") is False
    assert agents._public_host("http://localhost/x.jpg") is False
