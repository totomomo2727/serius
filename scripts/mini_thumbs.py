"""Write the small copies of each source thumbnail used by the delivery screen.

The whirl of clippings on /delivering shows two dozen pieces at once; at full
size that is a couple of megabytes, so each thumbnail gets a 280px copy in
app/static/thumbs/mini/. Run after scripts/fetch_thumbnails.py adds images.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

THUMBS = Path(__file__).resolve().parents[1] / "app" / "static" / "thumbs"
MINI = THUMBS / "mini"
WIDTH = 280


def main() -> None:
    MINI.mkdir(exist_ok=True)
    for source in sorted(THUMBS.glob("*.jpg")):
        image = Image.open(source).convert("RGB")
        height = min(round(image.height * WIDTH / image.width), WIDTH * 2)
        image = image.resize((WIDTH, round(image.height * WIDTH / image.width)), Image.LANCZOS)
        image = image.crop((0, 0, WIDTH, height))
        image.save(MINI / source.name, "JPEG", quality=72, optimize=True)
    print(f"{len(list(MINI.glob('*.jpg')))} minis in {MINI}")


if __name__ == "__main__":
    main()
