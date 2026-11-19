"""RePEc가 공개한 ReDIF 배포본에서 경제학 저널의 출판사 초록을 대조한다."""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

from .journal_records import abstract_text, doi

SERIES = {
    "jpe": "eee/pubeco",
    "pcho": "kap/pubcho",
    "joem": "eee/econom",
    "jpope": "spr/jopoec",
    "aejapp": "aea/aejapp",
}


def entries(text):
    record = {}
    last = ""
    for line in text.splitlines():
        if line[:1].isspace() and last:
            record[last] += " " + line.strip()
            continue
        field, sep, value = line.partition(":")
        if not sep:
            continue
        last = field.lower().strip()
        if last == "template-type" and record:
            yield record
            record = {}
        record[last] = value.strip()
    if record:
        yield record


def repec_batch(journal, papers):
    series = SERIES[journal]
    if not shutil.which("rsync"):
        raise OSError("RePEc 배포본 조회에 필요한 rsync 미설치")
    by_doi = {a["doi"] for a in papers}
    by_pii = {}
    for a in papers:
        match = re.search(
            r"/pii/(S[0-9A-Z]+)(?:[?#].*)?$", a.get("publisher_url") or "", re.I
        )
        if match:
            by_pii[match[1].lower()] = a["doi"]
    hits = {}
    with tempfile.TemporaryDirectory() as folder:
        try:
            done = subprocess.run(
                [
                    "rsync",
                    "-r",
                    "--contimeout=12",
                    "--timeout=45",
                    "--max-size=32m",
                    "rsync://rsync.repec.org/RePEc-ReDIF/" + series + "/",
                    folder + "/",
                ],
                capture_output=True,
                timeout=160,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise OSError("RePEc 배포본 응답 시간 초과") from None
        if done.returncode:
            raise OSError(f"RePEc rsync 연결·전송 실패 (종료 {done.returncode})")
        prefix = "repec:" + series.replace("/", ":") + ":"
        for path in Path(folder).rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".rdf", ".redif"}:
                continue
            data = path.read_bytes()
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = data.decode("latin-1")
            for r in entries(text):
                handle = r.get("handle", "")
                if not handle.lower().startswith(prefix) or not r.get(
                    "template-type", ""
                ).lower().startswith("redif-article"):
                    continue
                d = doi(r.get("doi") or r.get("x-doi"))
                if not d:
                    d = by_pii.get(handle.rsplit(":", 1)[-1].lower())
                if (
                    d not in by_doi
                    or not abstract_text(r.get("abstract"))
                    or not r.get("title")
                ):
                    continue
                link = "https://econpapers.repec.org/" + quote(handle, safe=":")
                hits[d] = (
                    r["title"],
                    r["abstract"],
                    link,
                    "출판사 ReDIF-Article · Abstract",
                )
    return hits
