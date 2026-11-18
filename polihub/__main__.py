"""Command-line entry points. Outputs are UTF-8 JSON, except the server's startup log."""

import argparse
import json
import sys
from pathlib import Path

from .acquisition import acquire
from .core import (
    CAT,
    ROOT,
    dataset,
    digest,
    fingerprint,
    init_dirs,
    locked,
    open_study,
    read,
    release,
    search,
    status,
    study,
    write,
)
from .engines import doctor
from .research import reference_table, workflow
from .validation import validate


def public_state(root, s):
    st = status(root)
    art = st["artifacts"]
    records = st["acquisition"].get("items", {})
    states = {k: dict(v) for k, v in art.get("datasets", {}).items()}
    changed = bool(art and art.get("spec_hash") != fingerprint(s))
    stale = set()
    from .acquisition import valid_local

    for key, previous in states.items():
        rec = records.get(key, {})
        if previous.get("source_hash") != rec.get("sha256") or (
            rec.get("status") == "received" and not valid_local(root, rec)
        ):
            stale.add(key)
    for key in states:
        if changed or key in stale:
            states[key].update(validation="재점검 필요", **{"import": "재실행 필요"})
    return {
        "project": str(root),
        "question": s.get("question"),
        "collected": {
            k: {a: v.get(a) for a in ("status", "version", "sha256", "via", "reason")}
            for k, v in st["acquisition"].get("items", {}).items()
        },
        "validation": {
            k: ("재점검 필요" if changed or k in stale else v["status"])
            for k, v in st["validation"].get("datasets", {}).items()
        },
        "analysis": "재실행 필요"
        if changed or stale
        else art.get("analysis_status", "미실행"),
        "reproduction": "재실행 필요"
        if changed or stale
        else art.get("reproduction", "미실행"),
        "engine": art.get("engine"),
        "datasets": states,
        "plan_current": art.get("spec_hash") == fingerprint(s),
        "artifacts": ["study.json", "acquisition_manifest.json"]
        if changed or stale
        else art.get("files", []),
        "models": art.get("models", []),
    }


def register_file(
    root,
    s,
    spec_path,
    i,
    file,
    version,
    member=None,
    encoding="utf-8-sig",
    delimiter=None,
):
    if i not in [item["id"] for item in s["datasets"]]:
        raise ValueError("연구명세에서 선택한 자료만 등록할 수 있습니다.")
    out = acquire(root, s, (i, file, version, member, encoding, delimiter))
    next(item for item in s["datasets"] if item["id"] == i)["version"] = version
    write(spec_path, s)
    return out


def verify(root, s):
    a = read(root / "outputs/artifact_manifest.json", {})
    if not a:
        return {"decision": "미실행", "reason": "실행 기록 없음"}
    path = root / "outputs/results.csv"
    frozen = read(root / a["plan_snapshot"])
    if (
        fingerprint(s) != a["spec_hash"]
        or fingerprint(frozen) != a["spec_hash"]
        or digest(path) != a["results_sha256"]
    ):
        raise ValueError(
            "실행 후 계획·결과가 달라졌습니다. 사전 명세로 다시 실행하세요."
        )
    from .acquisition import valid_local

    man = read(root / "acquisition_manifest.json", {}).get("items", {})
    for i, info in a["datasets"].items():
        if info["source_hash"] and (
            man.get(i, {}).get("sha256") != info["source_hash"]
            or not valid_local(root, man[i])
        ):
            raise ValueError("실행 이후 원자료가 바뀌었습니다.")
    rows = a.get("estimates", [])
    return {
        "decision": a["reproduction"],
        "comparisons": reference_table(frozen.get("references", []), rows, bool(rows)),
    }


def parser():
    p = argparse.ArgumentParser(prog="python -m polihub")
    sub = p.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("search")
    q.add_argument("query", nargs="?", default="")
    q.add_argument("--limit", type=int, default=3)
    for k in ("show", "access"):
        sub.add_parser(k).add_argument("id")
    q = sub.add_parser("variables")
    q.add_argument("query", nargs="?", default="")
    q.add_argument("--id")
    q = sub.add_parser("plan")
    q.add_argument("--spec", required=True, type=Path)
    q.add_argument("--ids", nargs="+")
    q.add_argument("--question", default="")
    q.add_argument("--engine", choices=["r", "stata"], default="r")
    q = sub.add_parser("doctor")
    q.add_argument("--spec", type=Path)
    q.add_argument("--context", choices=["local", "remote", "cloud"])
    q.add_argument("--offline", action="store_true")
    for k in (
        "acquire",
        "register",
        "validate",
        "prepare",
        "run",
        "pipeline",
        "verify",
        "status",
    ):
        q = sub.add_parser(k)
        q.add_argument("--spec", required=True, type=Path)
        if k == "register":
            q.add_argument("--id", required=True)
            q.add_argument("--file", type=Path, required=True)
            q.add_argument("--version", required=True)
            q.add_argument("--encoding", default="utf-8-sig")
            q.add_argument("--delimiter")
            q.add_argument("--member")
        if k in ("prepare", "run", "pipeline"):
            q.add_argument("--engine", choices=["r", "stata"])
            q.add_argument("--scripts-only", action="store_true")
    q = sub.add_parser("serve")
    q.add_argument("--port", type=int, default=8000)
    q.add_argument("--host", default="127.0.0.1")
    q.add_argument("--studies", type=Path, default=ROOT / "studies")
    q = sub.add_parser("check-links")
    q.add_argument("--id", required=True)
    q.add_argument("--log", type=Path, required=True)
    return p


def main(args):
    k = args.cmd
    if k == "search":
        return search(args.query)[: max(0, args.limit)]
    if k in {"show", "access"}:
        d = dataset(args.id)
        r = release(d)
        return (
            {"dataset": d, "release": r, "files": [CAT["files"][v] for v in r["files"]]}
            if k == "show"
            else {
                v: r.get(v)
                for v in (
                    "id",
                    "version",
                    "access",
                    "access_note",
                    "terms",
                    "authentication",
                    "api",
                    "files",
                )
            }
        )
    if k == "variables":
        return [
            v
            for v in CAT["variables"]
            if (not args.id or v["dataset"] == args.id)
            and args.query.casefold()
            in " ".join(
                str(v.get(key, "")) for key in ("concept", "name", "original_question")
            ).casefold()
        ]
    if k == "plan":
        ids = args.ids or [
            x["id"] for x in search(args.question)[:3] if args.question.strip()
        ]
        if not ids:
            raise ValueError("연구질문 또는 자료 ID가 필요합니다.")
        with locked(args.spec.resolve().parent):
            if args.spec.exists():
                raise ValueError("이미 연구명세가 있습니다. 새 폴더를 선택하세요.")
            s = study(ids, args.question, args.engine)
            write(args.spec, s)
            init_dirs(args.spec.resolve().parent)
        return {
            "spec": str(args.spec.resolve()),
            "datasets": s["datasets"],
            "next": "수집 후 변수·표본·추정대상 및 모형을 명세하세요.",
        }
    if k == "doctor":
        root, s = open_study(args.spec) if args.spec else (Path.cwd(), None)
        return doctor(
            root,
            [i["id"] for i in s["datasets"]] if s else None,
            not args.offline,
            args.context,
        )
    if k == "serve":
        from .server import serve

        return serve(args.host, args.port, args.studies)
    if k == "check-links":
        from .links import audit

        return audit(args.id, args.log)
    with locked(args.spec.resolve().parent):
        root, s = open_study(args.spec)
        if k == "acquire":
            return acquire(root, s)
        if k == "register":
            return register_file(
                root,
                s,
                args.spec,
                args.id,
                args.file,
                args.version,
                args.member,
                args.encoding,
                args.delimiter,
            )
        if k == "validate":
            v = validate(root, s)
            return {
                "report": str(root / "validation_report.json"),
                "datasets": {
                    i: {
                        k: v.get(k)
                        for k in ("status", "rows", "column_count", "issues", "reason")
                    }
                    for i, v in v["datasets"].items()
                },
                "join_checks": v["join_checks"],
            }
        if k == "verify":
            return verify(root, s)
        if k == "status":
            return public_state(root, s)
        if k == "pipeline":
            acquire(root, s)
        art = workflow(
            root,
            s,
            args.engine or s.get("engine", "r"),
            k != "prepare",
            not args.scripts_only,
            local_scripts=True,
        )
        return {
            "run_id": art["run_id"],
            "datasets": art["datasets"],
            "models": art["models"],
            "analysis_status": art["analysis_status"],
            "reproduction": art["reproduction"],
            "report": str(root / "outputs/report_ko.md"),
        }


if __name__ == "__main__":
    try:
        result = main(parser().parse_args())
        if result is not None:
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        if isinstance(result, dict):
            failed = any(
                isinstance(v, dict) and v.get("status") == "failed"
                for v in result.values()
            )
            states = result.get("datasets")
            if isinstance(states, dict):
                failed |= any(
                    isinstance(v, dict)
                    and (
                        v.get("status") == "blocked"
                        or v.get("import") in {"failed", "hash_mismatch"}
                        or v.get("acquisition") == "failed"
                    )
                    for v in states.values()
                )
            failed |= any(
                v.get("status") == "blocked"
                for v in result.get("models", [])
                if isinstance(v, dict)
            )
            if failed:
                sys.exit(2)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as e:
        print(
            json.dumps({"status": "error", "reason": str(e)}, ensure_ascii=False),
            file=sys.stderr,
        )
        sys.exit(1)
