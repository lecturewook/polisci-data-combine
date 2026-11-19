"""Local research API and a static, read-only catalogue for remote visitors."""

import ipaddress
import json
import mimetypes
import re
import shutil
import sys
import tempfile
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .__main__ import public_state, register_file
from .acquisition import acquire, cache_dir, commit_file, download
from .core import CAT, ROOT, digest, init_dirs, locked, open_study, study, write
from .engines import doctor
from .research import workflow

STATIC = {
    "/": ROOT / "index.html",
    "/index.html": ROOT / "index.html",
    **{
        "/web/" + f: ROOT / "web" / f
        for f in ("app.js", "journals.js", "style.css", "east-asia.png")
    },
    **{
        "/catalog/" + f: ROOT / "catalog" / f
        for f in (
            "journals.json",
            "methods.json",
            "journal-sources.json",
            "journal-index.json.gz",
        )
    },
    "/catalog/catalog.json": ROOT / "catalog/catalog.json",
    "/catalog/sources.json": ROOT / "catalog/sources.json",
}
ARTIFACTS = {
    "study.json",
    "acquisition_manifest.json",
    "validation_report.json",
    "comparability_report.md",
    "variable_map.csv",
    "outputs/report_ko.md",
    "outputs/results.csv",
    "outputs/verification.csv",
    "outputs/artifact_manifest.json",
}


class Hub(BaseHTTPRequestHandler):
    server_version = "PoliHub/1"

    def local(self):
        try:
            host = urlsplit("//" + self.headers.get("Host", "")).hostname
            return ipaddress.ip_address(
                self.client_address[0]
            ).is_loopback and host in ("localhost", "127.0.0.1", "::1")
        except ValueError:
            return False

    def respond(self, code, body, ctype="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'",
        )
        self.end_headers()
        self.wfile.write(body)

    def route(self):
        parsed = urlsplit(self.path)
        route = unquote(parsed.path)
        if self.command == "GET" and route in STATIC:
            f = STATIC[route]
            return self.respond(
                200,
                f.read_bytes(),
                (mimetypes.guess_type(f.name)[0] or "application/octet-stream")
                + (
                    "; charset=utf-8"
                    if f.suffix in (".js", ".json", ".html", ".css")
                    else ""
                ),
            )
        if route == "/api/capabilities" and self.command == "GET":
            return self.respond(200, {"execution": self.local()})
        if not self.local():
            return self.respond(
                403, {"error": "연구 실행은 로컬 API 또는 CLI에서 가능합니다."}
            )
        if self.command == "GET" and route.startswith("/api/download/"):
            f = CAT["files"].get(route.split("/")[-1])
            if not f:
                return self.respond(404, {"error": "등록된 공개 배포파일이 아닙니다."})
            cached = cache_dir() / f["sha256"]
            with tempfile.TemporaryDirectory(dir=self.server.studies) as tmp:
                file = Path(tmp) / f["name"]
                if cached.is_file() and digest(cached) == f["sha256"]:
                    shutil.copyfile(cached, file)
                else:
                    download(f["url"], file)
                    if digest(file) != f["sha256"] or file.stat().st_size != f["bytes"]:
                        raise ValueError("고정 배포판 해시 또는 크기 불일치")
                    commit_file(file, cached)
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(f["bytes"]))
                self.send_header(
                    "Content-Disposition", 'attachment; filename="' + f["name"] + '"'
                )
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                with file.open("rb") as src:
                    shutil.copyfileobj(src, self.wfile)
            return
        if self.command == "POST":
            origin = self.headers.get("Origin")
            if self.headers.get("X-PoliHub") != "1" or (
                origin and origin != "http://" + self.headers.get("Host", "")
            ):
                return self.respond(
                    403, {"error": "같은 출처의 연구 요청이 필요합니다."}
                )
        if route == "/api/doctor" and self.command == "POST":
            return self.respond(200, doctor(self.server.studies))
        if route == "/api/studies" and self.command == "POST":
            o = self.body()
            ids = o["ids"]
            if (
                not isinstance(ids, list)
                or not 1 <= len(ids) <= len(CAT["datasets"])
                or len(set(ids)) != len(ids)
            ):
                raise ValueError("데이터셋을 선택하세요.")
            if o.get("engine", "r") not in ("r", "stata"):
                raise ValueError("잘못된 엔진")
            key = uuid.uuid4().hex
            root = self.server.studies / key
            s = study(ids, str(o.get("question", ""))[:4000], o.get("engine", "r"))
            init_dirs(root)
            write(root / "study.json", s)
            return self.respond(201, {"id": key, "spec": s})
        match = re.fullmatch(
            r"/api/studies/([0-9a-f]{32})/(state|pipeline|acquire|prepare|run|file|register|plan)",
            route,
        )
        if not match:
            return self.respond(404, {"error": "경로를 찾을 수 없습니다."})
        key, action = match.groups()
        root = self.server.studies / key
        path = root / "study.json"
        if not path.is_file():
            return self.respond(404, {"error": "연구 프로젝트를 찾을 수 없습니다."})
        q = parse_qs(parsed.query)
        if self.command == "GET" and action == "file":
            name = q.get("name", [""])[0]
            if name not in ARTIFACTS and not re.fullmatch(
                r"outputs/figures/[A-Za-z0-9_-]+\.png", name
            ):
                return self.respond(404, {"error": "미등록 산출물"})
            f = root / name
            if not f.is_file():
                return self.respond(404, {"error": "아직 생성되지 않았습니다."})
            c = "image/png" if f.suffix == ".png" else "text/plain; charset=utf-8"
            return self.respond(200, f.read_bytes(), c)
        with locked(root):
            root, s = open_study(path)
            if self.command == "GET" and action == "state":
                return self.respond(200, {"spec": s, "status": public_state(root, s)})
            if self.command != "POST":
                return self.respond(405, {"error": "지원하지 않는 요청"})
            if action == "register":
                n = int(self.headers.get("Content-Length", -1))
                if not 0 < n <= 100 * 1024 * 1024:
                    raise ValueError("브라우저 등록 한도는 100 MiB입니다.")
                ext = q["ext"][0].lower()
                if ext not in ("csv", "tsv", "sav", "dta", "zip") and not (
                    ext in ("rdata", "rda", "rds")
                    and CAT["releases"][CAT["datasets"][q["id"][0]]["default_release"]][
                        "files"
                    ]
                ):
                    raise ValueError("잘못된 형식")
                with tempfile.TemporaryDirectory(dir=root) as tmp:
                    f = Path(tmp) / ("upload." + ext)
                    with f.open("wb") as out:
                        while n:
                            b = self.rfile.read(min(n, 1024 * 1024))
                            if not b:
                                raise ValueError("원본 전송이 중단되었습니다.")
                            out.write(b)
                            n -= len(b)
                    register_file(
                        root,
                        s,
                        path,
                        q["id"][0],
                        f,
                        q["version"][0],
                        q.get("member", [None])[0],
                        q.get("encoding", ["utf-8-sig"])[0],
                    )
            elif action == "plan":
                updated = self.body()
                if (
                    [x["id"] for x in updated["datasets"]]
                    != [x["id"] for x in s["datasets"]]
                    or updated.get("schema_version") != 1
                    or updated.get("engine") not in ("r", "stata")
                ):
                    raise ValueError("같은 자료 선택과 유효한 엔진을 사용하세요.")
                # Validate shape before replacing an active plan.
                with tempfile.TemporaryDirectory(dir=root) as tmp:
                    test = Path(tmp) / "study.json"
                    write(test, updated)
                    open_study(test)
                write(path, updated)
                s = updated
            elif action in ("acquire", "prepare", "run", "pipeline"):
                if action in ("acquire", "pipeline"):
                    acquire(root, s)
                if action != "acquire":
                    workflow(root, s, s["engine"], action in ("pipeline", "run"))
            else:
                return self.respond(405, {"error": "지원하지 않는 요청"})
            return self.respond(200, {"spec": s, "status": public_state(root, s)})

    def body(self):
        n = int(self.headers.get("Content-Length", -1))
        if not 0 < n <= 256 * 1024:
            raise ValueError("요청 크기 오류")
        return json.loads(self.rfile.read(n))

    def handle_method(self):
        try:
            self.route()
        except (ValueError, TypeError, KeyError, OSError, RuntimeError) as err:
            self.respond(400, {"error": str(err)[:400]})

    do_GET = handle_method
    do_POST = handle_method

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.command, urlsplit(self.path).path))


def serve(host, port, studies):
    server = ThreadingHTTPServer((host, port), Hub)
    server.studies = studies.resolve()
    server.studies.mkdir(parents=True, exist_ok=True)
    print(f"PoliHub · {host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
