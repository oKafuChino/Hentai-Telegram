"""Multi-process intelligent image compressor, grayscale discriminator, and strip slicer."""

import argparse
import io
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

try:
    from src.config import CompressConfig
    from src.utils import format_bytes, scan_image_files, setup_logger
except ImportError:
    from config import CompressConfig
    from utils import format_bytes, scan_image_files, setup_logger

logger = setup_logger("compressor")


def is_grayscale_image(img: Image.Image, threshold: float = 3.5) -> bool:
    """Determine whether an image is predominantly black & white (grayscale).

    Creates a 128x128 thumbnail, calculates average channel difference (R-G and G-B).
    Returns True if average difference is below threshold.
    """
    if img.mode in ("L", "1"):
        return True

    # Resize thumbnail to 128x128 for speed
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


def compress_single_image(
    task: tuple[int, str, CompressConfig]
) -> list[tuple[str, bytes]]:
    """Compress and slice a single image strictly in memory.

    Args:
        task: (index, image_path, config)

    Returns:
        List of (filename, image_bytes) tuples.
        Format defaults to JPEG for 100% Telegram Instant View compatibility.
        Single image format: '001.jpg'
        Sliced format: '001_01.jpg', '001_02.jpg'
    """
    idx, image_path, config = task
    p = Path(image_path)

    with Image.open(p) as raw_img:
        # Handle transparency: composite RGBA or transparent palette onto white background
        if raw_img.mode in ("RGBA", "LA") or (
            raw_img.mode == "P" and "transparency" in raw_img.info
        ):
            rgba = raw_img.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[3])
            img = bg
        else:
            img = raw_img.copy()

    # Color routing: determine grayscale vs color
    is_bw = is_grayscale_image(img, config.grayscale_threshold)
    if is_bw:
        img = img.convert("L")
        target_max_w = config.max_width_bw
        target_quality = config.quality_bw
    else:
        img = img.convert("RGB")
        target_max_w = config.max_width_color
        target_quality = config.quality_color

    # Viewport alignment: Lanczos downscaling
    orig_w, orig_h = img.size
    if orig_w > target_max_w:
        scale = target_max_w / float(orig_w)
        new_h = max(1, round(orig_h * scale))
        img = img.resize((target_max_w, new_h), Image.Resampling.LANCZOS)

    w, h = img.size
    slices: list[tuple[str, bytes]] = []

    # Format selection (JPEG is required for Telegram Instant View compatibility)
    fmt = (getattr(config, "image_format", "JPEG") or "JPEG").upper()
    is_jpeg = fmt in ("JPEG", "JPG")
    ext = ".jpg" if is_jpeg else ".webp"

    def _encode_to_bytes(part_img: Image.Image) -> bytes:
        buf = io.BytesIO()
        if is_jpeg:
            save_kw = {
                "format": "JPEG",
                "quality": target_quality,
                "optimize": True,
                "progressive": True,
            }
            if part_img.mode == "RGB":
                save_kw["subsampling"] = "4:2:0"
            part_img.save(buf, **save_kw)
        else:
            part_img.save(buf, format="WEBP", quality=target_quality, method=6)
        return buf.getvalue()

    # Strip cutting / slicing
    if h <= config.max_slice_height:
        filename = f"{idx:03d}{ext}"
        slices.append((filename, _encode_to_bytes(img)))
    else:
        cuts: list[tuple[int, int]] = []
        y = 0
        while y < h:
            next_y = min(y + config.max_slice_height, h)
            remaining = h - next_y
            if 0 < remaining < 300:
                # Merge small tail (<300px) into current slice to avoid tiny slivers
                cuts.append((y, h))
                break
            else:
                cuts.append((y, next_y))
                y = next_y

        for s_idx, (y_start, y_end) in enumerate(cuts, start=1):
            part = img.crop((0, y_start, w, y_end))
            filename = f"{idx:03d}_{s_idx:02d}{ext}"
            slices.append((filename, _encode_to_bytes(part)))

    return slices


def compress_chapter(
    image_paths: Sequence[str | Path],
    config: CompressConfig,
) -> list[tuple[str, bytes]]:
    """Compress and slice a sequence of chapter images using ProcessPoolExecutor.

    Maintains natural ordering and outputs in-memory (filename, bytes) list.
    """
    if not image_paths:
        return []

    tasks = [(i + 1, str(p), config) for i, p in enumerate(image_paths)]

    # If single task or workers <= 1, avoid process pool overhead
    if config.cpu_workers <= 1 or len(tasks) == 1:
        nested_results = [compress_single_image(t) for t in tasks]
    else:
        with ProcessPoolExecutor(max_workers=config.cpu_workers) as executor:
            nested_results = list(executor.map(compress_single_image, tasks))

    # Flatten while preserving chapter page order
    results: list[tuple[str, bytes]] = []
    for sublist in nested_results:
        results.extend(sublist)

    return results


def run_compression_benchmark(directory: str | Path, config: CompressConfig) -> None:
    """Benchmark and test compression on a local directory without network upload."""
    files = scan_image_files(directory)
    if not files:
        print(f"❌ 目录下未找到支持的图片文件: {directory}")
        return

    print("=" * 60)
    print("🚀 智能图像压缩与切片测试 (In-Memory Compression Benchmark)")
    print("=" * 60)
    print(f"• 扫描目录: {directory}")
    print(f"• 原始页数: {len(files)} 页")
    print(f"• 输出格式: {getattr(config, 'image_format', 'JPEG')} (Progressive JPEG 优化)")
    print(f"• 并发进程: {config.cpu_workers}")
    print(f"• 黑白阈值: {config.grayscale_threshold} (宽限制: {config.max_width_bw}px, 质量: {config.quality_bw})")
    print(f"• 彩页配置: (宽限制: {config.max_width_color}px, 质量: {config.quality_color})")
    print(f"• 切片高度: {config.max_slice_height}px (尾部合并阈值: <300px)")
    print("-" * 60)

    # Calculate original total size and count grayscale
    orig_total_size = 0
    bw_count = 0
    color_count = 0

    for f in files:
        orig_total_size += f.stat().st_size
        try:
            with Image.open(f) as im:
                if is_grayscale_image(im, config.grayscale_threshold):
                    bw_count += 1
                else:
                    color_count += 1
        except OSError as exc:
            logger.debug(f"无法解析图片 {f}: {exc}")

    start_time = time.perf_counter()
    slices = compress_chapter(files, config)
    elapsed = time.perf_counter() - start_time

    compressed_total_size = sum(len(data) for _, data in slices)
    saved_bytes = orig_total_size - compressed_total_size
    reduction_pct = (saved_bytes / orig_total_size * 100.0) if orig_total_size > 0 else 0.0

    print("📊 测试结果统计:")
    print(f"• 原始总大小: {format_bytes(orig_total_size)}")
    print(f"• 压缩后大小: {format_bytes(compressed_total_size)}")
    print(f"• 体积缩减率: {reduction_pct:.2f}% (节省 {format_bytes(saved_bytes)})")
    print(f"• 页面识别统计: 黑白页 {bw_count} 张, 彩色页 {color_count} 张")
    print(f"• 输出切片总数: {len(slices)} 张 (原 {len(files)} 页)")
    print(f"• 处理耗时: {elapsed:.2f} 秒 ({len(files) / max(0.001, elapsed):.1f} 页/秒)")
    print("-" * 60)
    print("✅ 全部图像流式处理完毕，未产生任何本地临时文件！")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manga Image Compressor Benchmark")
    parser.add_argument("--dir", required=True, help="漫画章节图片所在目录")
    parser.add_argument("--workers", type=int, default=2, help="CPU 并发进程数 (默认: 2)")
    parser.add_argument("--bw-width", type=int, default=1280, help="黑白页最大宽度 (默认: 1280)")
    parser.add_argument("--color-width", type=int, default=1440, help="彩页最大宽度 (默认: 1440)")
    parser.add_argument("--slice-height", type=int, default=2400, help="切片阈值高度 (默认: 2400)")
    parser.add_argument("--bw-quality", type=int, default=75, help="黑白页质量 (默认: 75)")
    parser.add_argument("--color-quality", type=int, default=80, help="彩页质量 (默认: 80)")
    parser.add_argument("--threshold", type=float, default=3.5, help="黑白判定阈值 (默认: 3.5)")
    parser.add_argument("--format", default="JPEG", choices=["JPEG", "WEBP"], help="编码格式 (默认: JPEG)")
    args = parser.parse_args()

    cfg = CompressConfig(
        cpu_workers=args.workers,
        max_width_bw=args.bw_width,
        max_width_color=args.color_width,
        max_slice_height=args.slice_height,
        quality_bw=args.bw_quality,
        quality_color=args.color_quality,
        grayscale_threshold=args.threshold,
        image_format=args.format,
    )
    run_compression_benchmark(args.dir, cfg)
