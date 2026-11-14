import logging
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)


def optimize_image(
    image_path: Path,
    max_width: int = 1600,
    quality: int = 85,
    min_bytes: int = 100 * 1024,
) -> Path:
    """Optimize a manga page image for online reading and storage.

    Features:
    1. Transparency handling: Composes transparent images (PNG/RGBA/P) onto solid white backgrounds.
    2. Proportional downscaling: If image width exceeds `max_width`, downscales with LANCZOS to preserve sharp text and line-art.
    3. Progressive JPEG encoding: Employs custom Huffman optimization (`optimize=True`) and progressive rendering (`progressive=True`).
    4. Non-degradation safety: If compression does not reduce file size and the image wasn't downscaled or converted, keeps the original file.
    5. Clean replacement: Returns the resulting Path (renamed to .jpg if format changed).
    """
    if not image_path.is_file():
        return image_path

    original_size = image_path.stat().st_size
    ext = image_path.suffix.lower()

    # Skip if already small and already a standard JPEG
    if ext in (".jpg", ".jpeg") and original_size < min_bytes and max_width <= 0:
        return image_path

    temp_path = image_path.parent / f"{image_path.stem}.tmp_opt.jpg"
    try:
        with Image.open(image_path) as im:
            # Skip animated GIFs
            if getattr(im, "is_animated", False):
                return image_path

            width, height = im.size

            # If small JPEG and width within limit, no need to re-encode
            if ext in (".jpg", ".jpeg") and original_size < min_bytes and (max_width <= 0 or width <= max_width):
                return image_path

            # Proportional downscaling with high-quality LANCZOS resampling
            was_resized = False
            if max_width > 0 and width > max_width:
                new_height = max(1, int(height * (max_width / width)))
                im = im.resize((max_width, new_height), Image.Resampling.LANCZOS)
                was_resized = True

            # Color mode normalization
            if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                rgba = im.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.split()[3])
                im = bg
            elif im.mode in ("L", "1"):
                im = im.convert("L")
            elif im.mode != "RGB":
                im = im.convert("RGB")

            target_path = image_path.with_suffix(".jpg")
            save_kwargs = {
                "format": "JPEG",
                "quality": max(50, min(quality, 95)),
                "optimize": True,
                "progressive": True,
            }
            if im.mode == "RGB":
                save_kwargs["subsampling"] = "4:2:0"

            im.save(temp_path, **save_kwargs)
            compressed_size = temp_path.stat().st_size

            was_converted = ext not in (".jpg", ".jpeg")

            # Replace original if size was reduced, or if it was resized or converted from PNG/WebP
            if compressed_size < original_size or was_resized or was_converted:
                temp_path.replace(target_path)
                if image_path != target_path and image_path.exists():
                    image_path.unlink()
                logger.info(
                    "image_optimized file=%s %dKB -> %dKB (%.1f%% saved)",
                    image_path.name,
                    original_size // 1024,
                    compressed_size // 1024,
                    (1 - compressed_size / max(1, original_size)) * 100,
                )
                return target_path
            else:
                temp_path.unlink(missing_ok=True)
                return image_path

    except Exception as exc:
        logger.warning("optimize_image_failed file=%s error=%s; using original", image_path.name, exc)
        temp_path.unlink(missing_ok=True)
        return image_path
