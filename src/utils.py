"""Utility functions for natural sorting, structured logging, file scanning, and hashing."""

import hashlib
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

import natsort

SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def setup_logger(name: str = "pipeline", level: int = logging.INFO) -> logging.Logger:
    """Configure a structured, clean console logger."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)
        formatter = logging.Formatter(
            fmt="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.propagate = False
    return logger


def natural_sort_paths(paths: Sequence[str | Path]) -> list[Path]:
    """Sort file paths in natural numeric order (e.g. 2.jpg before 10.jpg)."""
    return [Path(p) for p in natsort.natsorted(paths, key=lambda x: str(x))]


def scan_image_files(directory: str | Path) -> list[Path]:
    """Scan a directory for supported image files and return them in natural sort order."""
    target_dir = Path(directory)
    if not target_dir.is_dir():
        raise FileNotFoundError(f"目录不存在: {directory}")

    image_files: list[Path] = []
    for item in target_dir.iterdir():
        if item.is_file() and item.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
            image_files.append(item)

    return natural_sort_paths(image_files)


def format_bytes(size: float) -> str:
    """Format byte count into human-readable unit (B, KB, MB, GB)."""
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0
    val = float(size)
    while val >= 1024.0 and unit_index < len(units) - 1:
        val /= 1024.0
        unit_index += 1
    return f"{val:.2f} {units[unit_index]}"


def calculate_md5(data_or_path: bytes | str | Path) -> str:
    """Calculate MD5 hex digest for raw bytes or a file path."""
    hasher = hashlib.md5()
    if isinstance(data_or_path, (str, Path)):
        p = Path(data_or_path)
        with p.open("rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
    else:
        hasher.update(data_or_path)
    return hasher.hexdigest()
