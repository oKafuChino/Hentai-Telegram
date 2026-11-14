import tempfile
import unittest
from pathlib import Path

from PIL import Image

from hentai_telegram.storage.optimizer import optimize_image


class TestOptimizer(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_optimize_oversized_png(self):
        # Create an oversized 2400x3200 PNG
        img = Image.new("RGB", (2400, 3200), "white")
        src = self.root / "oversized.png"
        img.save(src, "PNG")
        orig_size = src.stat().st_size

        opt = optimize_image(src, max_width_bw=1600, quality=85)
        self.assertEqual(opt.suffix, ".jpg")
        self.assertTrue(opt.is_file())
        self.assertFalse(src.exists())  # Original was replaced
        self.assertLess(opt.stat().st_size, orig_size)

        with Image.open(opt) as res:
            self.assertEqual(res.size, (1600, 2133))
            self.assertEqual(res.format, "JPEG")

    def test_optimize_rgba_transparency(self):
        # Create an RGBA image with transparent background
        img = Image.new("RGBA", (800, 1000), (0, 0, 0, 0))
        src = self.root / "transparent.png"
        img.save(src, "PNG")

        opt = optimize_image(src, max_width_bw=1600, quality=85)
        self.assertEqual(opt.suffix, ".jpg")
        with Image.open(opt) as res:
            # Fully transparent composited onto white is B&W -> 'L' mode
            self.assertIn(res.mode, ("L", "RGB"))
            # Transparent background should become solid white (255)
            self.assertEqual(res.getpixel((10, 10)) if res.mode == "L" else res.getpixel((10, 10))[0], 255)

    def test_skip_already_small_jpeg(self):
        # Create a tiny JPEG under 100KB
        img = Image.new("RGB", (500, 500), "white")
        src = self.root / "tiny.jpg"
        img.save(src, "JPEG", quality=80)
        orig_mtime = src.stat().st_mtime

        opt = optimize_image(src, max_width=1600, quality=85, min_bytes=100 * 1024)
        self.assertEqual(opt, src)
        self.assertEqual(opt.stat().st_mtime, orig_mtime)

    def test_compress_image_larger_than_100kb(self):
        # Create an uncompressed large JPEG larger than 100KB
        img = Image.new("RGB", (2000, 2000), "gray")
        src = self.root / "large.jpg"
        img.save(src, "JPEG", quality=100, subsampling=0)
        self.assertGreater(src.stat().st_size, 100 * 1024)

        opt = optimize_image(src, max_width_bw=1280, quality_bw=75, min_bytes=100 * 1024)
        self.assertEqual(opt.suffix, ".jpg")
        self.assertLess(opt.stat().st_size, 100 * 1024)

    def test_nonexistent_file(self):
        src = self.root / "missing.jpg"
        self.assertEqual(optimize_image(src), src)


if __name__ == "__main__":
    unittest.main()
