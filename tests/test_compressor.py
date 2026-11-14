"""Unit tests for compressor and utils modules."""

import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from src.compressor import compress_chapter, compress_single_image, is_grayscale_image
from src.config import CompressConfig
from src.utils import calculate_md5, format_bytes, natural_sort_paths


class TestCompressorAndUtils(unittest.TestCase):
    def test_natural_sort(self):
        paths = ["10.jpg", "1.jpg", "2.jpg", "20.jpg", "chapter_1.png", "chapter_10.png"]
        sorted_p = [p.name for p in natural_sort_paths(paths)]
        self.assertEqual(sorted_p, ["1.jpg", "2.jpg", "10.jpg", "20.jpg", "chapter_1.png", "chapter_10.png"])

    def test_format_bytes(self):
        self.assertEqual(format_bytes(500), "500.00 B")
        self.assertEqual(format_bytes(2048), "2.00 KB")
        self.assertEqual(format_bytes(1048576 * 2.5), "2.50 MB")

    def test_md5(self):
        data = b"hello telegraph pipeline"
        self.assertEqual(len(calculate_md5(data)), 32)

    def test_grayscale_discrimination(self):
        # Pure B&W image
        bw_img = Image.new("RGB", (200, 200), (120, 120, 120))
        self.assertTrue(is_grayscale_image(bw_img, threshold=3.5))

        # Color image (vibrant red)
        color_img = Image.new("RGB", (200, 200), (240, 30, 20))
        self.assertFalse(is_grayscale_image(color_img, threshold=3.5))

        # Direct 'L' mode
        l_img = Image.new("L", (100, 100), 128)
        self.assertTrue(is_grayscale_image(l_img))

    def test_compress_single_image_normal(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            p = Path(tmp_dir) / "01.png"
            # 800x1200 normal image
            img = Image.new("RGB", (800, 1200), (200, 100, 50))
            img.save(p)

            cfg = CompressConfig(
                cpu_workers=1,
                max_width_bw=1280,
                max_width_color=1440,
                max_slice_height=2400,
            )
            slices = compress_single_image((1, str(p), cfg))
            self.assertEqual(len(slices), 1)
            filename, data = slices[0]
            self.assertEqual(filename, "001.webp")
            # Verify WebP output
            with Image.open(io.BytesIO(data)) as out_img:
                self.assertEqual(out_img.format, "WEBP")
                self.assertEqual(out_img.size, (800, 1200))

    def test_compress_single_image_slice_with_tail_merge(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            p = Path(tmp_dir) / "long.png"
            # Height = 5000: slices 2400, 2400 + tail 200 (<300) -> 2 slices of (2400, 2600)
            img = Image.new("L", (1000, 5000), 128)
            img.save(p)

            cfg = CompressConfig(
                cpu_workers=1,
                max_slice_height=2400,
            )
            slices = compress_single_image((2, str(p), cfg))
            self.assertEqual(len(slices), 2)
            self.assertEqual(slices[0][0], "002_01.webp")
            self.assertEqual(slices[1][0], "002_02.webp")

            with Image.open(io.BytesIO(slices[0][1])) as s1:
                self.assertEqual(s1.size, (1000, 2400))
            with Image.open(io.BytesIO(slices[1][1])) as s2:
                self.assertEqual(s2.size, (1000, 2600))

    def test_compress_chapter(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = []
            for i in range(1, 4):
                p = Path(tmp_dir) / f"{i}.jpg"
                img = Image.new("RGB", (600, 800), (i * 50, i * 30, i * 40))
                img.save(p)
                paths.append(p)

            cfg = CompressConfig(cpu_workers=2)
            results = compress_chapter(paths, cfg)
            self.assertEqual(len(results), 3)
            self.assertEqual([r[0] for r in results], ["001.webp", "002.webp", "003.webp"])


if __name__ == "__main__":
    unittest.main()
