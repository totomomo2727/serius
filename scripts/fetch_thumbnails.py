"""Cache one thumbnail per catalogued piece, taken from the piece's own page.

For each item in content/library/*.json this reads the page the item links to and
takes the image the publisher themselves nominate for sharing (`og:image`, then
`twitter:image`, then a `<link rel="image_src">`); YouTube items use the video's
own thumbnail. The file is downloaded once into app/static/thumbs/ so nothing is
hotlinked at render time, and the page it came from is recorded on the item as
`thumbnail_source` so the credit line can name it.

Plain scholarly pages (Stanford Encyclopedia entries, Gutenberg texts, PDFs)
publish no share image at all. Rather than fall back to a drawing, those are
captured from the page itself with a headless browser and marked
`thumbnail_kind: page`, so the credit says where the capture came from.

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
BLOCKED_MARKERS = (
    "performing security verification",
    "your access has been blocked",
    "access denied",
    "just a moment",
    "enable javascript and cookies",
    "are you a robot",
    "captcha",
)
CONTENT_SELECTORS = "article, main, #main-content, #content, .entry-content"


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


def capture_pdf(client: httpx.Client, url: str) -> Image.Image:
    """The first page of a paper, rendered."""
    import pypdfium2

    response = client.get(url, headers=HEADERS, follow_redirects=True, timeout=45)
    response.raise_for_status()
    document = pypdfium2.PdfDocument(BytesIO(response.content))
    try:
        return document[0].render(scale=2).to_pil()
    finally:
        document.close()


def capture_page(url: str, item_id: str, client: httpx.Client | None = None) -> str | None:
    """Photograph the top of the page the piece lives on."""
    from playwright.sync_api import sync_playwright

    THUMBS.mkdir(parents=True, exist_ok=True)
    name = f"{item_id}.jpg"
    if client is not None and urlparse(url).path.lower().endswith(".pdf"):
        image = capture_pdf(client, url).convert("RGB")
        image.thumbnail((MAX_WIDTH, MAX_WIDTH * 2), Image.LANCZOS)
        image.save(THUMBS / name, "JPEG", quality=82, optimize=True)
        return name
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1200, "height": 675}, device_scale_factor=2)
            response = page.goto(url, wait_until="load", timeout=45000)
            page.wait_for_timeout(1200)
            if response is not None and response.status >= 400:
                return None
            body = page.inner_text("body")[:2000].lower()
            if any(marker in body for marker in BLOCKED_MARKERS):
                return None  # a bot wall is not the article
            clip = {"x": 0, "y": 0, "width": 1200, "height": 675}
            element = page.query_selector(CONTENT_SELECTORS)
            box = element.bounding_box() if element else None
            if box and box["width"] > 420:
                left = max(box["x"], 0)
                top = max(box["y"], 0)
                clip = {
                    "x": left,
                    "y": top,
                    "width": min(box["width"], 1200 - left),
                    "height": min(box["height"], 675),
                }
            shot = page.screenshot(type="png", clip=clip)
        finally:
            browser.close()
    image = Image.open(BytesIO(shot)).convert("RGB")
    if image.width > MAX_WIDTH:
        height = round(image.height * MAX_WIDTH / image.width)
        image = image.resize((MAX_WIDTH, height), Image.LANCZOS)
    if len(image.getcolors(maxcolors=1 << 16) or [(0, 0)]) < 24:
        return None  # a blank or error page is worse than no image
    image.save(THUMBS / name, "JPEG", quality=82, optimize=True)
    return name


def save(client: httpx.Client, image_url: str, item_id: str) -> str | None:
    response = client.get(image_url, headers=HEADERS, follow_redirects=True, timeout=25)
    response.raise_for_status()
    image = Image.open(BytesIO(response.content))
    if image.width < 480 or image.height < 260:
        return None
    if image.width / image.height > 3.2:
        return None  # a masthead banner, not a picture of the piece
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
                except Exception as error:  # noqa: BLE001 - a capture may still work
                    print(f"  meta {item['id']}: {type(error).__name__} {error}")
                    urls = []
                name = None
                for candidate in urls:  # later candidates are fallbacks
                    try:
                        name = save(client, candidate, item["id"])
                    except Exception as error:  # noqa: BLE001
                        print(f"  .... {item['id']}: {type(error).__name__} {error}")
                        continue
                    if name:
                        break
                kind = "image"
                source = item["url"]
                if not name:
                    try:
                        source = item.get("thumbnail_page") or item["url"]
                        name = capture_page(source, item["id"], client)
                        kind = "page"
                    except Exception as error:  # noqa: BLE001
                        print(f"  shot {item['id']}: {type(error).__name__} {error}")
                if not name:
                    print(f"  none {item['id']}")
                    continue
                item["thumbnail"] = name
                item["thumbnail_source"] = source
                item["thumbnail_kind"] = kind
                changed = True
                print(f"  {kind[:4]} {item['id']}")
            if changed:
                path.write_text(json.dumps(items, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main(set(sys.argv[1:]))
