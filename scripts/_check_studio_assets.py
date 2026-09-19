"""Check that a running Streamlit app's JavaScript and CSS are reachable."""
from __future__ import annotations

import argparse
from html.parser import HTMLParser
from http.client import HTTPException
import urllib.request
from urllib.parse import urljoin, urlsplit


class AssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name in {"src", "href"} and value:
                if urlsplit(value).path.lower().endswith((".js", ".css")):
                    self.urls.append(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", nargs="?", default="http://localhost:8501")
    args = parser.parse_args(argv)

    try:
        with urllib.request.urlopen(args.url, timeout=5) as response:
            page_url = response.geturl()
            html = response.read().decode("utf-8", "replace")
        assets = AssetParser()
        assets.feed(html)
        urls = list(dict.fromkeys(urljoin(page_url, url) for url in assets.urls))
    except (OSError, ValueError, HTTPException) as exc:
        print("FAIL", args.url, exc)
        return 1

    print("static assets found:", len(urls))
    if not urls:
        print("FAIL: no JavaScript or CSS assets found")
        return 1

    ok = 0
    for url in urls:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                size = len(response.read())
                print("OK", response.status, size, url)
            ok += 1
        except (OSError, ValueError, HTTPException) as exc:
            print("FAIL", url, exc)
    print(f"accessible {ok}/{len(urls)}")
    return 0 if ok == len(urls) else 1


if __name__ == "__main__":
    raise SystemExit(main())
