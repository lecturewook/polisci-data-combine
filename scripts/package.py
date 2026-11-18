"""Build a single-file, offline catalogue and a portable source bundle without research data."""

import argparse
import base64
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def build(out):
    out.mkdir(parents=True, exist_ok=True)
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "web/style.css").read_text(encoding="utf-8")
    image = base64.b64encode((ROOT / "web/east-asia.png").read_bytes()).decode("ascii")
    css = css.replace("east-asia.png", "data:image/png;base64," + image)
    resources = {
        k: json.loads((ROOT / "catalog" / (k + ".json")).read_text(encoding="utf-8"))
        for k in ("catalog", "sources")
    }
    embedded = json.dumps(resources, ensure_ascii=False).replace("<", "\\u003c")
    js = (
        (ROOT / "web/app.js")
        .read_text(encoding="utf-8")
        .replace("</script", "<\\/script")
    )
    import re

    html = re.sub(
        r'<link\b[^>]*href="web/style\.css"[^>]*>',
        lambda _: "<style>" + css + "</style>",
        html,
    )
    html = re.sub(r'<script\b[^>]*src="web/app\.js"[^>]*>\s*</script>', "", html)
    html = html.replace(
        "</body>",
        "<script>window.POLIHUB_OFFLINE="
        + embedded
        + ";</script><script>"
        + js
        + "</script></body>",
    )
    (out / "PoliHub.html").write_text(html, encoding="utf-8")
    with zipfile.ZipFile(
        out / "PoliHub-source.zip", "w", compression=zipfile.ZIP_DEFLATED
    ) as z:
        paths = [
            ROOT / n
            for n in (
                "README.md",
                "AGENTS.md",
                ".gitignore",
                "requirements.txt",
                "index.html",
            )
        ]
        for folder in (
            "catalog",
            "docs",
            "examples",
            "polihub",
            "scripts",
            "tests",
            "web",
        ):
            paths.extend(
                p
                for p in (ROOT / folder).rglob("*")
                if p.is_file() and "__pycache__" not in p.parts
            )
        for p in sorted(paths):
            z.write(p, "PoliHub/" + p.relative_to(ROOT).as_posix())
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, required=True)
    print(build(p.parse_args().out))
