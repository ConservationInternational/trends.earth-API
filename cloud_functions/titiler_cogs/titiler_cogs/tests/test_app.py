import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

import app
import numpy
import rasterio
from rasterio.io import MemoryFile
from rasterio.shutil import copy as copy_raster
from rasterio.transform import from_bounds
from rio_tiler.errors import TileOutsideBounds
from rio_tiler.io import Reader

SOURCE = (
    "s3://private-webmap-shares/public-shares/"
    "11111111-1111-4111-8111-111111111111/"
    "22222222-2222-4222-8222-222222222222.tif"
)


def event(url=SOURCE, path="/cog/tiles/WebMercatorQuad/0/0/0.png", **params):
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": urlencode({"url": url, "bidx": 1, **params}),
        "headers": {"host": "private-renderer"},
        "requestContext": {
            "http": {
                "method": "GET",
                "path": path,
                "sourceIp": "127.0.0.1",
                "protocol": "HTTP/1.1",
            }
        },
        "isBase64Encoded": False,
    }


class RasterLambdaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.cog = Path(cls.directory.name) / "snapshot.tif"
        source = Path(cls.directory.name) / "source.tif"
        values = numpy.ones((256, 256), dtype="uint8")
        values[:, 128:] = 2
        with rasterio.open(
            source,
            "w",
            driver="GTiff",
            width=256,
            height=256,
            count=1,
            dtype="uint8",
            crs="EPSG:3857",
            nodata=0,
            transform=from_bounds(
                -20037508.342789244,
                -20037508.342789244,
                20037508.342789244,
                20037508.342789244,
                256,
                256,
            ),
        ) as dataset:
            dataset.write(values, 1)
        copy_raster(source, cls.cog, driver="COG")

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def reader(self, source, **options):
        self.assertEqual(source, SOURCE)
        return Reader(str(self.cog), **options)

    def test_actual_categorical_and_continuous_png_rendering(self):
        cases = [
            (
                {
                    "colormap": json.dumps({"1": "#00ff00", "2": "#ff0000"}),
                    "resampling": "nearest",
                },
                True,
            ),
            (
                {
                    "colormap_name": "viridis",
                    "rescale": "1,2",
                    "resampling": "bilinear",
                },
                False,
            ),
        ]
        for params, categorical in cases:
            with (
                self.subTest(params=params),
                patch.object(app.cog, "reader", self.reader),
            ):
                response = app.handler(event(**params), None)
            self.assertEqual(response["statusCode"], 200)
            self.assertEqual(response["headers"]["content-type"], "image/png")
            self.assertEqual(response["headers"]["cache-control"], "no-store")
            tile = base64.b64decode(response["body"], validate=True)
            self.assertTrue(tile.startswith(b"\x89PNG\r\n\x1a\n"))
            with MemoryFile(tile) as memory, memory.open() as dataset:
                self.assertEqual((dataset.width, dataset.height), (256, 256))
                image = dataset.read()
            if categorical:
                self.assertEqual(image[:3, 128, 64].tolist(), [0, 255, 0])
                self.assertEqual(image[:3, 128, 192].tolist(), [255, 0, 0])
            else:
                self.assertNotEqual(
                    image[:3, 128, 64].tolist(), image[:3, 128, 192].tolist()
                )

    def test_rejects_unregistered_sources_before_opening_raster(self):
        sources = [
            SOURCE.replace("private-webmap-shares", "other-bucket"),
            "https://example.com/raster.tif",
            "http://169.254.169.254/latest/meta-data/",
            "/local/raster.tif",
            SOURCE + "?versionId=other",
            SOURCE.replace("public-shares/", "public-shares/vector/"),
            SOURCE.replace(".tif", ".pmtiles"),
            SOURCE.replace("public-shares/", "public-shares/../public-shares/"),
        ]
        with patch.object(app.cog, "reader") as reader:
            for source in sources:
                with self.subTest(source=source):
                    response = app.handler(event(url=source), None)
                    self.assertEqual(response["statusCode"], 403)
            reader.assert_not_called()

    def test_rejects_non_tile_endpoints_and_unsupported_parameters(self):
        with patch.object(app.cog, "reader") as reader:
            for path in [
                "/docs",
                "/cog/info",
                "/cog/statistics",
                "/cog/tiles/WebMercatorQuad/23/0/0.png",
                "/cog/tiles/WebMercatorQuad/0/1/0.png",
            ]:
                self.assertEqual(app.handler(event(path=path), None)["statusCode"], 400)
            self.assertEqual(app.handler(event(tilesize=4096), None)["statusCode"], 400)
            duplicate = event()
            duplicate["rawQueryString"] += "&url=" + SOURCE
            self.assertEqual(app.handler(duplicate, None)["statusCode"], 400)
            reader.assert_not_called()

    def test_rendering_failures_do_not_return_storage_details(self):
        with patch.object(app.cog, "reader", side_effect=RuntimeError(SOURCE)):
            response = app.handler(event(), None)
        self.assertEqual(response["statusCode"], 502)
        self.assertNotIn(SOURCE, response["body"])

    def test_outside_raster_bounds_returns_empty_tile(self):
        with patch.object(app.cog, "reader", side_effect=TileOutsideBounds()):
            response = app.handler(event(), None)
        self.assertEqual(response["statusCode"], 204)
        self.assertFalse(response.get("body"))

    def test_oversized_and_invalid_output_is_rejected(self):
        for response in [
            {"statusCode": 200, "isBase64Encoded": False, "body": "not png"},
            {"statusCode": 200, "isBase64Encoded": True, "body": "%%%"},
            {
                "statusCode": 200,
                "isBase64Encoded": True,
                "body": base64.b64encode(b"x" * (app.MAX_TILE_BYTES + 1)).decode(),
            },
        ]:
            with (
                self.subTest(response_size=len(response["body"])),
                patch.object(app, "adapter", return_value=response),
            ):
                self.assertEqual(app.handler(event(), None)["statusCode"], 502)

    def test_exact_two_mebibyte_limit(self):
        for size, status in [(app.MAX_TILE_BYTES, 200), (app.MAX_TILE_BYTES + 1, 502)]:
            tile = b"\x89PNG\r\n\x1a\n" + b"x" * (size - 8)
            response = {
                "statusCode": 200,
                "headers": {"content-type": "image/png"},
                "isBase64Encoded": True,
                "body": base64.b64encode(tile).decode(),
            }
            with (
                self.subTest(size=size),
                patch.object(app, "adapter", return_value=response),
            ):
                self.assertEqual(app.handler(event(), None)["statusCode"], status)
