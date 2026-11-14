import logging
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

logger = logging.getLogger(__name__)


def is_grayscale_image(img: Image.Image, threshold: float = 3.5) -> bool:
    """Determine whether an image is predominantly black & white (grayscale)."""
    if img.mode in ("L", "1"):
        return True

    thumb = img.resize((128, 128), Image.Resampling.BILINEAR)
    if thumb.mode != "RGB":
        thumb = thumb.convert("RGB")

    r, g, b = thumb.split()
    diff_rg = ImageChops.difference(r, g)
    diff_gb = ImageChops.difference(g, b)

    mean_rg = ImageStat.Stat(diff_rg).mean[0]
    mean_gb = ImageStat.Stat(diff_gb).mean[0]
    avg_diff = (mean_rg + mean_gb) / 2.0

    return avg_diff < threshold


def optimize_image(
    image_path: Path,
    max_width_bw: int = 1280,
    max_width_color: int = 1440,
    quality_bw: int = 75,
    quality_color: int = 80,
    grayscale_threshold: float = 3.5,
    max_width: int = 0,
    quality: int = 0,
    min_bytes: int = 0,
    image_format: str = "WEBP",
) -> Path:
    """Optimize a manga page image into high-efficiency WebP or Progressive JPEG.

    Features:
    1. Grayscale discrimination: Distinguishes B&W manga pages from color pages.
       - Black & white pages are converted to single-channel 8-bit 'L' grayscale (eliminates Cb/Cr chroma redundancy).
       - Color pages are converted to standard RGB.
    2. Proportional downscaling with high-quality Lanczos resampling:
       - B&W pages: restricted to max_width_bw (default 1280px).
       - Color pages: restricted to max_width_color (default 1440px).
    3. High-efficiency encoding:
       - Default WebP (method=6) for highest compression ratio and minimal bandwidth.
       - Progressive JPEG (optimize=True, progressive=True) if image_format="JPEG".
       - Strips all EXIF/ICC metadata.
    4. Safe replacement: Always returns the path to the optimized file (.webp or .jpg).
    """
    if not image_path.is_file():
        return image_path

    fmt = (image_format or "WEBP").strip().upper()
    target_ext = ".webp" if fmt == "WEBP" else ".jpg"

    original_size = image_path.stat().st_size
    if min_bytes > 0 and original_size < min_bytes and image_path.suffix.lower() == target_ext:
        return image_path

    # Support legacy parameters if explicitly passed
    if max_width > 0:
        max_width_bw = min(max_width_bw, max_width)
        max_width_color = min(max_width_color, max_width)
    if quality > 0:
        quality_bw = min(quality_bw, quality)
        quality_color = min(quality_color, quality)

    temp_path = image_path.parent / f"{image_path.stem}.tmp_opt{target_ext}"
    target_path = image_path.with_suffix(target_ext)

    try:
        with Image.open(image_path) as raw_im:
            if getattr(raw_im, "is_animated", False):
                return image_path

            # Transparency handling: composite onto solid white background
            if raw_im.mode in ("RGBA", "LA") or (raw_im.mode == "P" and "transparency" in raw_im.info):
                rgba = raw_im.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.split()[3])
                im = bg
            else:
                im = raw_im.copy()

            # Grayscale vs color discrimination
            is_bw = is_grayscale_image(im, threshold=grayscale_threshold)
            if is_bw:
                im = im.convert("L")
                target_w = max_width_bw
                target_q = quality_bw
            else:
                im = im.convert("RGB")
                target_w = max_width_color
                target_q = quality_color

            # Viewport downscaling
            orig_w, orig_h = im.size
            if orig_w <= 0 or orig_h <= 0:
                return image_path
            if target_w > 0 and orig_w > target_w:
                new_h = max(1, round(orig_h * (target_w / float(orig_w))))
                im = im.resize((target_w, new_h), Image.Resampling.LANCZOS)

            # Encoding
            if fmt == "WEBP":
                save_kwargs = {
                    "format": "WEBP",
                    "quality": max(50, min(target_q, 95)),
                    "method": 6,
                }
            else:
                save_kwargs = {
                    "format": "JPEG",
                    "quality": max(50, min(target_q, 95)),
                    "optimize": True,
                    "progressive": True,
                }
                if im.mode == "RGB":
                    save_kwargs["subsampling"] = "4:2:0"

            im.save(temp_path, **save_kwargs)
            compressed_size = temp_path.stat().st_size

            # Replace original image with compressed file
            temp_path.replace(target_path)
            if image_path != target_path and image_path.exists():
                image_path.unlink()

            logger.info(
                "image_optimized file=%s -> %s format=%s mode=%s %dKB -> %dKB (%.1f%% saved)",
                image_path.name,
                target_path.name,
                fmt,
                "BW" if is_bw else "Color",
                original_size // 1024,
                compressed_size // 1024,
                (1 - compressed_size / max(1, original_size)) * 100,
            )
            return target_path

    except Exception as exc:  # noqa: BLE001
        logger.warning("optimize_image_failed file=%s error=%s; using original", image_path.name, exc)
        temp_path.unlink(missing_ok=True)
        return image_path
