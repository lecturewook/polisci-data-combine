"""Catalogue access and per-study state. All project paths in manifests are relative."""

import csv
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAT = json.loads((ROOT / "catalog/catalog.json").read_text(encoding="utf-8"))
SAFE_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}\Z")
RESULT_COLS = ["model", "statistic", "estimate", "std_error", "n", "unit"]
VERIFY_COLS = [
    "table",
    "model",
    "statistic",
    "measure",
    "original",
    "reproduced",
    "difference",
    "tolerance",
    "n",
    "decision",
    "reason",
]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ident(v):
    if not isinstance(v, str) or not SAFE_ID.fullmatch(v):
        raise ValueError("안전한 영문 ID가 필요합니다.")
    return v


def read(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write(path, value, plain=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, delete=False, mode="w", encoding="utf-8"
    ) as f:
        f.write(
            value
            if plain
            else json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        )
    os.replace(f.name, path)


def table(path, cols, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, delete=False, mode="w", newline="", encoding="utf-8"
    ) as f:
        out = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        out.writeheader()
        out.writerows(rows)
    os.replace(f.name, path)


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def fingerprint(obj):
    return hashlib.sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


@contextmanager
def locked(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".project.lock").open("a+b") as f:
        if os.name == "nt":
            import msvcrt

            if not f.tell():
                f.write(b"0")
                f.flush()
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_UN)


def dataset(i):
    if i not in CAT["datasets"]:
        raise ValueError(f"카탈로그에 없는 자료 ID {i}")
    return CAT["datasets"][i]


def release(item):
    d = dataset(item["id"])
    r = CAT["releases"].get(item.get("release", d["default_release"]))
    if not r or r["dataset"] != d["id"]:
        raise ValueError("자료와 배포판이 일치하지 않습니다.")
    return r


def study(ids, question="", engine="r"):
    return {
        "schema_version": 1,
        "question": question,
        "objective": None,
        "population": None,
        "unit": None,
        "engine": engine,
        "seed": 2026,
        "datasets": [
            {
                "id": i,
                "release": dataset(i)["default_release"],
                "version": CAT["releases"][dataset(i)["default_release"]]["version"],
                "countries": [],
                "period": None,
            }
            for i in ids
        ],
        "analyses": [],
        "references": [],
        "variable_definitions": [],
        "comparisons": [],
        "design_review": {},
        "join_checks": [],
        "assumptions": [],
        "sensitivity": [],
    }


def open_study(path):
    path = Path(path).resolve()
    s = read(path)
    if (
        not isinstance(s, dict)
        or s.get("schema_version") != 1
        or not isinstance(s.get("datasets"), list)
        or not s["datasets"]
    ):
        raise ValueError("schema_version 1 및 한 개 이상의 datasets가 필요합니다.")
    for k in (
        "analyses",
        "sensitivity",
        "references",
        "comparisons",
        "join_checks",
        "variable_definitions",
    ):
        if not isinstance(s.get(k, []), list) or any(
            not isinstance(v, dict) for v in s.get(k, [])
        ):
            raise ValueError(k + "에는 객체 목록을 지정하세요.")
    if not isinstance(s.get("question", ""), str):
        raise ValueError("연구질문은 문자열이어야 합니다.")
    seen = set()
    for item in s["datasets"]:
        r = release(item)
        if item["id"] in seen:
            raise ValueError("자료 ID가 중복됩니다.")
        seen.add(item["id"])
        if r["files"] and item.get("version", r["version"]) != r["version"]:
            raise ValueError("고정 배포 버전 불일치")
        if not isinstance(item.get("countries", []), list):
            raise ValueError("countries는 코드 목록이어야 합니다.")
        period = item.get("period")
        if period is not None and (
            not isinstance(period, list)
            or len(period) != 2
            or not all(type(v) is int for v in period)
            or period[0] > period[1]
        ):
            raise ValueError("요청 기간을 [시작연도, 종료연도]로 지정하세요.")
    if s.get("engine", "r") not in ("r", "stata"):
        raise ValueError("engine은 r 또는 stata입니다.")
    return path.parent, s


def init_dirs(root):
    for sub in ["data/raw", "data/processed", "scripts", "logs", "outputs/figures"]:
        (root / sub).mkdir(parents=True, exist_ok=True)
    target = root / "scripts/replay.py"
    if not target.exists():
        write(target, (ROOT / "scripts/replay.py").read_text(encoding="utf-8"), True)


def concepts(s, i):
    r = release(next(x for x in s["datasets"] if x["id"] == i))
    base = {v["name"]: v.copy() for v in CAT["variables"] if v["release"] == r["id"]}
    for v in s.get("variable_definitions", []):
        if v.get("dataset") == i:
            if not all(v.get(k) for k in ("name", "concept", "evidence", "location")):
                raise ValueError(
                    "사용자 변수사전에는 변수·개념·코드북·근거 위치가 필요합니다."
                )
            base[v["name"]] = {**base.get(v["name"], {}), **v}
    return base


def status(root):
    return {
        k: read(root / f, {})
        for k, f in (
            ("acquisition", "acquisition_manifest.json"),
            ("validation", "validation_report.json"),
            ("artifacts", "outputs/artifact_manifest.json"),
        )
    }


def search(query):
    from unicodedata import normalize

    def tokens(s):
        out = set()
        for t in re.findall(r"[0-9a-z가-힣]+", normalize("NFKC", s).lower()):
            out.add(t)
            if len(t) > 2 and re.search("[가-힣]", t):
                out.update(t[k : k + 2] for k in range(len(t) - 1))
        return out

    q = tokens(query)
    rows = []
    for d in CAT["datasets"].values():
        title = tokens(" ".join([d["name"], *d["aliases"]]))
        terms = tokens(
            " ".join(
                [
                    d["summary"],
                    *d["topics"],
                    CAT["categories"][d["category"]],
                    *[CAT["regions"][v] for v in release(d)["regions"]],
                ]
            )
        )
        hit = (q & title) | (q & terms)
        if not q or hit:
            rows.append((len(q & title) * 3 + len(hit), d["id"], sorted(hit)))
    return [
        {
            "id": i,
            "name": dataset(i)["name"],
            "reason": dataset(i)["political_use"],
            "matched": m,
        }
        for _, i, m in sorted(rows, key=lambda x: -x[0])
    ]
