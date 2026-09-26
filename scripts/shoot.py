"""Screenshot the running app at desktop and mobile widths.

Run: .venv/bin/python scripts/shoot.py [outdir] [base-url]
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/shots")
BASE = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8100"

PROFILES = [
    ("all", {"topics": ["ai", "philosophy", "psychology", "product-design", "tech"], "depth": "mix"}),
    ("design", {"topics": ["product-design"], "interests": ["typography"], "depth": "mix"}),
    ("ai-tech", {"topics": ["ai", "tech"], "depth": "deep"}),
    ("philosophy", {"topics": ["philosophy"], "depth": "accessible"}),
]


def form_body(profile: dict) -> str:
    parts = [f"topics={t}" for t in profile["topics"]]
    parts += [f"interests={i}" for i in profile.get("interests", [])]
    parts.append(f"depth={profile['depth']}")
    return "&".join(parts)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width, height, tag in ((1440, 1000, "desktop"), (390, 844, "mobile")):
            page = browser.new_page(viewport={"width": width, "height": height})
            page.set_default_timeout(20000)
            page.goto(f"{BASE}/", wait_until="networkidle")
            page.screenshot(path=str(OUT / f"{tag}-landing.png"), full_page=True)
            page.goto(f"{BASE}/choose", wait_until="networkidle")
            page.screenshot(path=str(OUT / f"{tag}-onboarding.png"), full_page=True)
            for name, profile in PROFILES:
                page.goto(f"{BASE}/choose")
                page.evaluate(
                    """(body) => {
                        fetch('/preview', {method: 'POST', body: body, redirect: 'follow',
                            headers: {'Content-Type': 'application/x-www-form-urlencoded'}})
                            .then(r => r.text()).then(t => {
                                document.open(); document.write(t); document.close();
                            });
                    }""",
                    form_body(profile),
                )
                page.wait_for_selector(".edition", state="attached")
                page.wait_for_timeout(6000)
                page.evaluate("document.querySelectorAll('.delivery').forEach(e => e.remove())")
                page.evaluate("document.body.classList.remove('delivering')")
                page.wait_for_timeout(400)
                page.screenshot(path=str(OUT / f"{tag}-edition-{name}.png"), full_page=True)
            page.close()
        browser.close()
    print(f"wrote {len(list(OUT.glob('*.png')))} shots to {OUT}")


if __name__ == "__main__":
    main()
