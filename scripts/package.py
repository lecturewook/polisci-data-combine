"""기존 독립 HTML·소스 묶음과 버전별 저널 열람본을 빌드한다."""

import argparse
import base64
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def build(out, versioned_only=False):
    out.mkdir(parents=True, exist_ok=True)
    version = int((ROOT / "catalog/site-version").read_text())
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "web/style.css").read_text(encoding="utf-8")
    png = base64.b64encode((ROOT / "web/east-asia.png").read_bytes()).decode()
    css = css.replace("east-asia.png", "data:image/png;base64," + png)
    resources = {
        k: json.loads((ROOT / "catalog" / (k + ".json")).read_text(encoding="utf-8"))
        for k in ("catalog", "sources", "journals", "methods", "journal-sources")
    }
    resources["version"] = version
    embedded = json.dumps(resources, ensure_ascii=False, separators=(",", ":")).replace(
        "<", "\\u003c"
    )
    packed = base64.b64encode(
        (ROOT / "catalog/journal-index.json.gz").read_bytes()
    ).decode()
    html = re.sub(
        r'<link\b[^>]*href="web/style\.css"[^>]*>',
        lambda _: "<style>" + css + "</style>",
        html,
    )
    html = re.sub(
        r'(?m)^[ \t]*<script\b[^>]*src="web/(?:app|journals)\.js"[^>]*>\s*</script>\n?', "", html
    )
    js = "\n".join(
        (ROOT / "web" / f).read_text(encoding="utf-8")
        for f in ("journals.js", "app.js")
    )
    js = js.replace("</script", "<\\/script")
    html = html.replace(
        "</body>",
        '<script type="application/octet-stream" id="journal-data">'
        + packed
        + "</script><script>window.POLIHUB_OFFLINE="
        + embedded
        + ";</script><script>"
        + js
        + "</script></body>",
    )
    (out / f"PoliHub-v{version}.html").write_text(
        html.replace("<body>", '<body data-start="journals">'), encoding="utf-8"
    )
    if versioned_only:
        return out
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
            ".github/workflows",
        ):
            paths.extend(
                p
                for p in (ROOT / folder).rglob("*")
                if p.is_file()
                and "__pycache__" not in p.parts
                and p.name != ".project.lock"
            )
        for p in sorted(paths):
            z.write(p, "PoliHub/" + p.relative_to(ROOT).as_posix())
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--versioned-only", action="store_true")
    args = parser.parse_args()
    print(build(args.out, args.versioned_only))
