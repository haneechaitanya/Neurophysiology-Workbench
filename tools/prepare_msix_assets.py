"""Create exact-size MSIX visual assets from the canonical project icon."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


SQUARE_ASSETS = {
    "StoreLogo.png": 50,
    "Square44x44Logo.png": 44,
    "Square150x150Logo.png": 150,
    "Square310x310Logo.png": 310,
}


def _resized(source: Image.Image, size: int) -> Image.Image:
    return source.resize((size, size), Image.Resampling.LANCZOS)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()

    source_path = args.source.resolve()
    destination = args.destination.resolve()
    if not source_path.is_file():
        raise SystemExit(f"Source icon not found: {source_path}")
    destination.mkdir(parents=True, exist_ok=True)

    with Image.open(source_path) as opened:
        icon = opened.convert("RGBA")
        if icon.width != icon.height:
            raise SystemExit("The canonical Store source icon must be square.")
        for filename, size in SQUARE_ASSETS.items():
            _resized(icon, size).save(destination / filename, format="PNG", optimize=True)

        wide = Image.new("RGBA", (310, 150), (0, 26, 58, 255))
        wide_icon = _resized(icon, 140)
        wide.alpha_composite(wide_icon, ((310 - 140) // 2, (150 - 140) // 2))
        wide.save(destination / "Wide310x150Logo.png", format="PNG", optimize=True)

    print(f"Created {len(SQUARE_ASSETS) + 1} MSIX assets in {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
