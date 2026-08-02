from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


FRAME_SIZES: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)


def build_brand_assets(source: Path, png_output: Path, ico_output: Path) -> None:
    with Image.open(source) as loaded:
        logo = loaded.convert("RGBA")
    if logo.width != logo.height or logo.width < 512:
        raise ValueError("Brand source must be a square image of at least 512 pixels")
    alpha = logo.getchannel("A")
    if alpha.getextrema() != (0, 255):
        raise ValueError("Brand source must contain transparent and opaque pixels")
    corners = (
        (0, 0),
        (logo.width - 1, 0),
        (0, logo.height - 1),
        (logo.width - 1, logo.height - 1),
    )
    if any(alpha.getpixel(point) != 0 for point in corners):
        raise ValueError("Brand source corners must be transparent")

    png_output.parent.mkdir(parents=True, exist_ok=True)
    ico_output.parent.mkdir(parents=True, exist_ok=True)
    logo.save(png_output, format="PNG", optimize=True)
    logo.save(ico_output, format="ICO", sizes=[(size, size) for size in FRAME_SIZES])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--png-output", type=Path, required=True)
    parser.add_argument("--ico-output", type=Path, required=True)
    args = parser.parse_args()
    build_brand_assets(args.source, args.png_output, args.ico_output)


if __name__ == "__main__":
    main()
