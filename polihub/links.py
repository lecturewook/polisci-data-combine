"""Record link drift and candidate replacements. Changing the pinned file is an editorial action."""

from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from urllib.request import Request

from .acquisition import HTTP
from .core import CAT, dataset, locked, now, read, release, write


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a" and (v := dict(attrs).get("href")):
            self.links.append(v)


def audit(i, path):
    d = dataset(i)
    r = release(d)
    event = {
        "id": i,
        "checked": now(),
        "release": r["version"],
        "files": [],
        "candidates": [],
    }
    for f in r["files"]:
        file = CAT["files"][f]
        try:
            with HTTP.open(
                Request(file["url"], headers={"Range": "bytes=0-4095"}), timeout=15
            ) as res:
                b = res.read(4096)
                ok = res.headers.get_content_type() != "text/html" and b[:1] != b"<"
                event["files"].append(
                    {"url": file["url"], "stage": 2 if ok else 1, "http": res.status}
                )
        except Exception as e:
            event["files"].append({"url": file["url"], "stage": 1, "error": str(e)})
    try:
        with HTTP.open(d["source_url"], timeout=15) as res:
            p = Links()
            p.feed(
                res.read(2_000_000).decode(
                    res.headers.get_content_charset() or "utf-8", errors="replace"
                )
            )
        for link in p.links:
            url = urljoin(d["source_url"], link)
            if (
                url.startswith("https://")
                and urlsplit(url)
                .path.lower()
                .endswith((".csv", ".zip", ".dta", ".sav", ".xlsx"))
                and url not in event["candidates"]
            ):
                event["candidates"].append(url)
    except Exception as e:
        event["discovery_error"] = str(e)
    with locked(path.resolve().parent):
        history = read(path, [])
        history.append(event)
        write(path, history)
    return event
