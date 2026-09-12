import unittest

from app.idnes import IdnesClient, parse_photos, upgrade_photo
from app.sreality import Listing


GALLERY = """
<a href="https://sta-reality2.1gr.cz/sta/compile/thumbs/9/a/4/abd496c8d664d02f06d32e6bd1e27.jpg?gt=r" data-fancybox="images">
<img src="https://sta-reality2.1gr.cz/sta/compile/thumbs/c/9/a/326d73c5a86220829925d6fcac725.jpg" width="1018">
</a>
<a href="https://sta-reality2.1gr.cz/sta/compile/thumbs/3/c/6/fc752f9031ae2a190fefcada6a8dc.jpg?gt=r" class="x" data-fancybox="images"></a>
<img src="https://sta-reality2.1gr.cz/sta/compile/thumbs/6/a/7/886b6988e2636b091ef537e7e7df9.webp" width="130" height="80">
"""


class IdnesPhotoTests(unittest.TestCase):
    def test_fancybox_keeps_retina_gallery(self):
        photos = parse_photos(GALLERY)
        self.assertEqual(len(photos), 2)
        self.assertTrue(all("gt=r" in url for url in photos))
        self.assertFalse(any(url.endswith(".webp") for url in photos))

    def test_upgrade_jpg_thumb(self):
        url = upgrade_photo("https://sta-reality2.1gr.cz/sta/compile/thumbs/a/b/c/abc.jpg")
        self.assertTrue(url.endswith("?gt=r"))

    def test_detail_parser_reads_gallery(self):
        listing = IdnesClient("https://reality.idnes.cz/s/byty/")._parse_detail(
            Listing(
                id=1,
                name="x",
                price_czk=1,
                price_label="1",
                disposition="",
                area_m2=None,
                locality="Praha 7",
                url="https://reality.idnes.cz/detail/pronajem/byt/praha-7-x/aaaaaaaaaaaaaaaaaaaaaaaa/",
                image_url=None,
            ),
            GALLERY,
        )
        self.assertEqual(len(listing.photos), 2)
        self.assertIn("gt=r", listing.image_url)
