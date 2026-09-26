"""Cache one thumbnail per catalogued piece, taken from the piece's own page.

For each item in content/library/*.json this reads the page the item links to and
takes the image the publisher themselves nominate for sharing (`og:image`, then
`twitter:image`, then a `<link rel="image_src">`); YouTube items use the video's
own thumbnail. The file is downloaded once into app/static/thumbs/ so nothing is
hotlinked at render time, and the page it came from is recorded on the item as
`thumbnail_source` so the credit line can name it.

Run: .venv/bin/python scripts/fetch_thumbnails.py [item-id ...]
"""

from __future__ import annotations

import json
import re
import sys
from html import unescape
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "content" / "library"
THUMBS = ROOT / "app" / "static" / "thumbs"
MAX_WIDTH = 1200
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,image/*;q=0.8,*/*;q=0.5",
}

META_PATTERNS = [
    r'<meta[^>]+property=["\']og:image(?::secure_url|:url)?["\'][^>]+content=["\']([^"\']+)["\']',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::secure_url|:url)?["\']',
    r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image(?::src)?["\']',
    r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)["\']',
]
IMG_PATTERN = r'<img[^>]+src=["\']([^"\']+)["\']'


def youtube_id(url: str) -> str | None:
    host = urlparse(url).netloc.removeprefix("www.")
    if host in ("youtube.com", "m.youtube.com"):
        match = re.search(r"[?&]v=([\w-]{11})", url)
        return match.group(1) if match else None
    if host == "youtu.be":
        return urlparse(url).path.strip("/")[:11] or None
    return None


def page_images(client: httpx.Client, url: str) -> list[str]:
    response = client.get(url, headers=HEADERS, follow_redirects=True, timeout=25)
    response.raise_for_status()
    html = response.text
    found: list[str] = []
    for pattern in META_PATTERNS:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            found.append(urljoin(str(response.url), unescape(match.group(1))))
    # No share image: fall back to the first in-page images, which the size
    # filter in save() then narrows down to something actually illustrative.
    for match in re.finditer(IMG_PATTERN, html, re.IGNORECASE):
        candidate = urljoin(str(response.url), unescape(match.group(1)))
        if candidate.lower().endswith(".svg") or candidate.startswith("data:"):
            continue
        found.append(candidate)
        if len(found) > 8:
            break
    return found


def candidates(client: httpx.Client, url: str) -> list[str]:
    video = youtube_id(url)
    if video:
        return [
            f"https://i.ytimg.com/vi/{video}/maxresdefault.jpg",
            f"https://i.ytimg.com/vi/{video}/hqdefault.jpg",
        ]
    return page_images(client, url)


def save(client: httpx.Client, image_url: str, item_id: str) -> str | None:
    response = client.get(image_url, headers=HEADERS, follow_redirects=True, timeout=25)
    response.raise_for_status()
    image = Image.open(BytesIO(response.content))
    if image.width < 480 or image.height < 260:
        return None
    image = image.convert("RGB")
    if image.width > MAX_WIDTH:
        height = round(image.height * MAX_WIDTH / image.width)
        image = image.resize((MAX_WIDTH, height), Image.LANCZOS)
    THUMBS.mkdir(parents=True, exist_ok=True)
    name = f"{item_id}.jpg"
    image.save(THUMBS / name, "JPEG", quality=82, optimize=True)
    return name


def main(only: set[str]) -> None:
    with httpx.Client() as client:
        for path in sorted(LIBRARY.glob("*.json")):
            items = json.loads(path.read_text())
            changed = False
            for item in items:
                if only and item["id"] not in only:
                    continue
                if item.get("thumbnail") and (THUMBS / item["thumbnail"]).exists():
                    continue
                try:
                    urls = candidates(client, item["url"])
                except Exception as error:  # noqa: BLE001 - report and move on
                    print(f"  skip {item['id']}: {type(error).__name__} {error}")
                    continue
                name = None
                for candidate in urls:  # later candidates are fallbacks
                    try:
                        name = save(client, candidate, item["id"])
                    except Exception as error:  # noqa: BLE001
                        print(f"  .... {item['id']}: {type(error).__name__} {error}")
                        continue
                    if name:
                        break
                if not name:
                    print(f"  none {item['id']}")
                    continue
                item["thumbnail"] = name
                item["thumbnail_source"] = item["url"]
                changed = True
                print(f"  ok   {item['id']}")
            if changed:
                path.write_text(json.dumps(items, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main(set(sys.argv[1:]))
