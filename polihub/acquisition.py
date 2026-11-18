"""Pinned public assets, authenticated provider requests, and user-supplied files."""

import base64
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .core import CAT, ROOT, dataset, digest, init_dirs, now, read, release, write
from .formats import FORMATS, copy_limited, load_frame, unpack


class SecureRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        dest = urllib.parse.urlsplit(newurl)
        if dest.scheme != "https" or dest.username or dest.password:
            raise ValueError("HTTPS 배포처 리디렉션을 확인하세요.")
        if req.data is not None:
            raise ValueError("인증 요청의 리디렉션을 확인하세요.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


HTTP = urllib.request.build_opener(SecureRedirect)


def valid_local(root, rec):
    if rec.get("status") != "received":
        return False
    for name, key in (("raw", "sha256"), ("table", "table_sha256")):
        path = project_path(root, rec[name])
        if not path.is_file() or digest(path) != rec[key]:
            return False
    return True


def project_path(root, value):
    p = (root / value).resolve()
    if not p.is_relative_to(root) or p == root:
        raise ValueError("연구 폴더 밖의 경로입니다.")
    return p


def cache_dir():
    return Path(
        os.environ.get("POLIHUB_CACHE", str(ROOT / ".cache" / "polihub"))
    ).expanduser()


def download(url, to, body=None, progress=None):
    request = urllib.request.Request(
        url, data=body, headers={"User-Agent": "PoliHub/1.0"}
    )
    with HTTP.open(request, timeout=40) as res:
        mime = res.headers.get_content_type()
        if mime in {"text/html", "application/xhtml+xml"}:
            raise ValueError("배포처가 로그인 또는 안내 페이지를 반환했습니다.")
        if progress is not None:
            progress.update(stage=2, http=res.status, content_type=mime)
        copy_limited(res, to)
        if progress is not None:
            progress.update(stage=3)
        return {
            "http": res.status,
            "content_type": mime,
            "final_url": res.geturl().split("?")[0],
        }


def commit_file(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dst.parent, delete=False) as f:
        tmp = Path(f.name)
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)


def accept(
    root, item, tmp, file, version, source, meta, options, member=None, envelope=None
):
    h = digest(file)
    if options.get("sha256") and h != options["sha256"]:
        raise ValueError("공식 배포판의 SHA-256과 다릅니다. 배포 공지를 확인하세요.")
    if options.get("bytes") and file.stat().st_size != options["bytes"]:
        raise ValueError("고정 배포판 크기 불일치")
    tab = unpack(file, tmp / "unpacked", member)
    df, details = load_frame(tab, options)
    rawdir = root / "data/raw" / item["id"] / h
    raw = rawdir / ("original" + file.suffix.lower())
    table = raw if tab == file else root / "data/processed" / item["id"] / h / tab.name
    if table != raw:
        commit_file(tab, table)
    commit_file(file, raw)
    extra = {}
    if envelope:
        dest = rawdir / "provider-response.json"
        commit_file(envelope, dest)
        extra = {
            "response": str(dest.relative_to(root)),
            "response_sha256": digest(dest),
        }
    return {
        **meta,
        **extra,
        "status": "received",
        "stage": 4,
        "release": release(item)["id"],
        "version": version,
        "source": source,
        "raw": raw.relative_to(root).as_posix(),
        "sha256": h,
        "bytes": raw.stat().st_size,
        "format": tab.suffix[1:].lower(),
        "table": table.relative_to(root).as_posix(),
        "table_sha256": digest(table),
        "rows": len(df),
        "columns": df.shape[1],
        "encoding": details["encoding"],
        "delimiter": details["delimiter"],
        "member": member,
        "documents": release(item)["documentation"],
        "at": now(),
    }


def acquire_one(root, item, old, tmp, progress, local=None):
    r = release(item)
    version = item.get("version") or r["version"]
    if local:
        file, ver, member, encoding, sep = local
        if not ver:
            raise ValueError("등록할 파일의 배포판 식별자가 필요합니다.")
        options = {"encoding": encoding, "delimiter": sep}
        if r["files"]:
            pin = CAT["files"][r["files"][0]]
            if ver != r["version"]:
                raise ValueError("공식 파일에 지정된 버전과 다릅니다.")
            options.update(pin)
        elif item.get("version") and r["access"] == "api" and ver != item["version"]:
            raise ValueError("연구명세의 API 버전과 다릅니다.")
        ext = file.suffix.lower()
        if ext not in FORMATS | {".zip"}:
            raise ValueError("지원되지 않는 파일 형식입니다.")
        staged = tmp / ("local" + ext)
        with file.open("rb") as f:
            copy_limited(f, staged)
        return accept(
            root,
            item,
            tmp,
            staged,
            ver,
            "user_file",
            {"via": "local", "registered_name": file.name},
            options,
            member,
        )
    if valid_local(root, old):
        if old.get("release") == r["id"] and old.get("version") == version:
            return {**old, "via": "study_reuse"}
    if r["access"] == "direct":
        f = CAT["files"][r["files"][0]]
        progress.update(stage=1, source=f["url"])
        cached = cache_dir() / f["sha256"]
        file = tmp / f["name"]
        if cached.is_file() and digest(cached) == f["sha256"]:
            shutil.copyfile(cached, file)
            meta = {"via": "hash_cache"}
        else:
            meta = {**download(f["url"], file, progress=progress), "via": "download"}
        rec = accept(root, item, tmp, file, version, f["url"], meta, f, f.get("member"))
        if meta["via"] == "download":
            commit_file(file, cached)
        return rec
    if r["access"] == "api":
        key = os.environ.get(r["authentication"])
        if (
            key
            and version
            and re.fullmatch(r"MPDS(?:SA)?[0-9]{4}[a-zA-Z0-9.-]*", version)
        ):
            body = urllib.parse.urlencode(
                {"api_key": key, "key": version, "kind": "dta"}
            ).encode()
            envelope = tmp / "response.json"
            meta = download(r["api"]["url"], envelope, body)
            file = tmp / "manifesto.dta"
            content = json.loads(envelope.read_bytes())["content"]
            file.write_bytes(base64.b64decode("".join(content.split()), validate=True))
            return accept(
                root,
                item,
                tmp,
                file,
                version,
                r["api"]["url"],
                {**meta, "via": "provider_api"},
                {},
                envelope=envelope,
            )
        message = "MANIFESTO_API_KEY와 확인된 MPDS 버전을 설정하세요."
    else:
        message = r["access_note"]
    return {
        "status": "waiting",
        "release": r["id"],
        "version": version,
        "stage": 0,
        "reason": message,
        "source": dataset(item["id"])["source_url"],
        "documents": r["documentation"],
        "at": now(),
    }


def acquire(root, s, register=None):
    init_dirs(root)
    path = root / "acquisition_manifest.json"
    m = read(path, {"schema_version": 1, "items": {}, "history": []})
    for item in s["datasets"]:
        i = item["id"]
        if register and i != register[0]:
            continue
        old = m["items"].get(i, {})
        progress = {"stage": 0, "source": dataset(i)["source_url"]}
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            try:
                rec = acquire_one(
                    root,
                    item,
                    old,
                    Path(tmp),
                    progress,
                    register[1:] if register else None,
                )
            except Exception as e:
                # Never log an authenticated request, its body, or a server response.
                reason = (
                    type(e).__name__
                    if release(item)["access"] == "api"
                    else str(e)[:450]
                )
                rec = {
                    "status": "failed",
                    "reason": reason,
                    "at": now(),
                    "release": release(item)["id"],
                    "version": item.get("version"),
                    **progress,
                }
        event = {"dataset": i, **rec}
        m["history"].append(event)
        if register and rec["status"] == "failed" and valid_local(root, old):
            m["items"][i] = {**old, "last_registration_error": rec["reason"]}
        else:
            m["items"][i] = rec
        write(path, m)
        if register and rec["status"] == "failed":
            raise ValueError(rec["reason"])
    return {
        i: {
            k: v
            for k, v in rec.items()
            if k
            in {
                "status",
                "reason",
                "via",
                "stage",
                "sha256",
                "bytes",
                "raw",
                "rows",
                "columns",
                "version",
            }
        }
        for i, rec in m["items"].items()
        if i in {x["id"] for x in s["datasets"]}
    }
