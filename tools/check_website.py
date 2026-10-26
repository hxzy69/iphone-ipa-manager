"""Validate the static Pages payload without installing a web framework."""

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1] / "docs/site"


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.links = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            if attrs["id"] in self.ids:
                self.errors.append(f"Duplicate id: {attrs['id']}")
            self.ids.add(attrs["id"])
        if tag == "img" and not all(key in attrs for key in ("alt", "width", "height")):
            # The lightbox image is populated only when opened and sizes to its content.
            if attrs.get("id") != "dialog-image":
                self.errors.append("Image needs alt text and explicit dimensions")
        for key in ("href", "src"):
            if attrs.get(key):
                self.links.append(attrs[key])


def main():
    page = Page()
    page.feed((ROOT / "index.html").read_text(encoding="utf-8"))
    for link in page.links:
        parsed = urlsplit(link)
        if parsed.scheme or parsed.netloc:
            if parsed.scheme != "https":
                page.errors.append(f"Non-HTTPS external link: {link}")
            continue
        if parsed.path:
            path = (ROOT / unquote(parsed.path)).resolve()
            if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
                page.errors.append(f"Missing or out-of-scope asset: {link}")
        elif parsed.fragment and parsed.fragment not in page.ids:
            page.errors.append(f"Unknown anchor: {link}")
    allowed = {".html", ".css", ".js", ".svg", ".png"}
    for path in ROOT.rglob("*"):
        if path.is_symlink():
            page.errors.append(f"Symlink in published payload: {path.name}")
        if path.is_file() and path.suffix not in allowed and path.name != ".nojekyll":
            page.errors.append(f"Unexpected published file: {path.name}")
    if page.errors:
        raise SystemExit("\n".join(page.errors))
    print(f"Website valid: {len(page.ids)} unique IDs, {len(page.links)} links/assets checked.")


if __name__ == "__main__":
    main()
