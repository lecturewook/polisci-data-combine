"""Generate portable research scripts and verify the engine's own completion log."""

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from .acquisition import HTTP, valid_local
from .core import CAT, ROOT, concepts, now, release, write

LOCAL_R = ROOT.parent / ".polihub-tools/r/bin/Rscript"
FIELD = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")


def executable(engine):
    override = os.getenv("POLIHUB_RSCRIPT" if engine == "r" else "POLIHUB_STATA")
    if override:
        return shutil.which(override)
    names = (
        ["Rscript"]
        if engine == "r"
        else [
            "stata-mp",
            "stata-se",
            "stata",
            "StataMP-64.exe",
            "StataSE-64.exe",
            "StataBE-64.exe",
            "StataMP",
            "StataSE",
        ]
    )
    for name in names:
        if path := shutil.which(name):
            return path
    if engine == "r" and LOCAL_R.is_file():
        return str(LOCAL_R)
    return None


def invoke(root, engine, relative, *, timeout=300):
    path = executable(engine)
    if not path:
        return {
            "status": "unavailable",
            "reason": f"{engine} 실행파일을 찾을 수 없습니다.",
        }
    logs = root / "logs"
    logs.mkdir(exist_ok=True, parents=True)
    token = uuid.uuid4().hex
    logfile = logs / (relative.stem + "-" + token[:8] + ".log")
    marker = "POLIHUB_DONE_" + token
    do = None
    if engine == "r":
        cmd = [path, "--vanilla", relative.as_posix(), marker]
    else:
        do = logs / ("batch_" + token + ".do")
        child = relative.as_posix()
        write(
            do,
            'capture log close _all\nlog using "'
            + logfile.relative_to(root).as_posix()
            + '", text replace\n'
            + 'capture noisily do "'
            + child
            + '"\nlocal jobrc = _rc\nif '
            + chr(96)
            + "jobrc' == 0 {\n"
            + '    display "'
            + marker
            + '"\n}\nlog close\nexit '
            + chr(96)
            + "jobrc'\n",
            True,
        )
        cmd = [
            path,
            "/e" if os.name == "nt" else "-b",
            "do",
            do.relative_to(root).as_posix(),
        ]
    result = {
        "engine": engine,
        "executable": path,
        "script": relative.as_posix(),
        "log": logfile.relative_to(root).as_posix(),
        "started": now(),
    }
    try:
        proc = subprocess.run(
            cmd,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        logtext = proc.stdout.decode(errors="replace")
        if engine == "r":
            write(logfile, logtext, True)
        elif logfile.exists():
            logtext += "\n" + logfile.read_text(errors="replace")
        else:
            write(logfile, logtext, True)
        done = re.search(r"(?m)^" + marker + r"\s*$", logtext)
        stata_error = engine == "stata" and re.search(
            r"(?m)^\s*r\([1-9]\d*\);\s*$", logtext
        )
        result.update(
            status="passed"
            if proc.returncode == 0 and done and not stata_error
            else "failed",
            exit_code=proc.returncode,
        )
        if result["status"] == "failed":
            result["reason"] = "실행 종료코드·완료 표식 또는 오류 로그를 확인하세요."
        if m := re.search(r"(?m)^STATA_VERSION\s+([0-9.]+)\s*$", logtext):
            result["version"] = m.group(1)
    except (subprocess.TimeoutExpired, OSError) as e:
        write(logfile, type(e).__name__ + "\n", True)
        result.update(status="failed", reason=type(e).__name__)
    finally:
        if do:
            (root / (do.stem + ".log")).unlink(missing_ok=True)
    return result


def doctor(root, ids=None, network=True, context=None):
    root = Path(root).resolve()
    info = {
        "at": now(),
        "os": platform.platform(),
        "python": sys.executable,
        "python_version": platform.python_version(),
        "context": context
        or os.getenv("POLIHUB_CONTEXT")
        or (
            "cloud"
            if ROOT.parent == Path("/workspace")
            and any(k.startswith("CODEX_") for k in os.environ)
            else "unspecified"
        ),
        "disk_free": shutil.disk_usage(root).free,
        "credentials": {"MANIFESTO_API_KEY": bool(os.getenv("MANIFESTO_API_KEY"))},
    }
    r, st = executable("r"), executable("stata")
    info["r"] = {"path": r, "status": "unavailable"}
    if r:
        proc = subprocess.run(
            [
                r,
                "--vanilla",
                "-e",
                'cat(R.version.string,"\\n"); for(p in c("readr","haven")) cat(p, if(requireNamespace(p,quietly=TRUE)) as.character(packageVersion(p)) else "missing","\\n")',
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        info["r"].update(
            status="available" if proc.returncode == 0 else "failed",
            versions=proc.stdout.strip().splitlines(),
            exit_code=proc.returncode,
        )
    try:
        with tempfile.TemporaryDirectory(dir=root) as td:
            tmp = Path(td)
            (tmp / "doctor.do").write_text(
                'clear\nset obs 1\ngenerate check = 2 + 2\nassert check == 4\ndisplay "STATA_VERSION " c(stata_version)\n'
            )
            info["writable"] = True
            info["stata"] = {
                "path": st,
                **invoke(tmp, "stata", Path("doctor.do"), timeout=45),
            }
            if info["stata"].get("status") == "failed":
                diagnostic = (tmp / info["stata"]["log"]).read_text(errors="replace")
                info["stata"]["license_error"] = bool(
                    re.search("licen[cs]e|expired|authorization", diagnostic, re.I)
                )
            info["stata"].pop("script", None)
            info["stata"].pop("log", None)
    except OSError:
        info.update(writable=False, stata={"path": st, "status": "not_tested"})

    def check(url):
        try:
            req = urllib.request.Request(
                url, headers={"Range": "bytes=0-1023", "User-Agent": "PoliHub/1.0"}
            )
            with HTTP.open(req, timeout=8) as res:
                res.read(1024)
                return {
                    "host": urlsplit(url).hostname,
                    "http": res.status,
                    "status": "reachable",
                }
        except Exception as e:
            return {
                "host": urlsplit(url).hostname,
                "status": "unavailable",
                "reason": str(e)[:140],
            }

    if network:
        hosts = {}
        for i in ids or ["pop-candidates", "ert", "kgss", "manifesto"]:
            d = CAT["datasets"][i]
            r = release(d)
            url = CAT["files"][r["files"][0]]["url"] if r["files"] else d["source_url"]
            hosts[urlsplit(url).hostname] = url
        with ThreadPoolExecutor(max_workers=6) as pool:
            info["network"] = list(pool.map(check, hosts.values()))
    return info


def rq(s):
    return json.dumps(str(s), ensure_ascii=False)


def rvec(vals):
    return "c(" + ", ".join(map(rq, vals)) + ")" if vals else "character()"


def allowed_field(s):
    if not isinstance(s, str) or not FIELD.fullmatch(s):
        raise ValueError("변수 이름에 지원하지 않는 문자가 있습니다.")
    return s


def imports(root, s, m, engine, execute):
    items = {}
    scripts = []
    for item in s["datasets"]:
        i = item["id"]
        rec = m.get(i, {})
        if not valid_local(root, rec):
            items[i] = {
                "status": rec.get("status", "not_acquired")
                if rec.get("status") != "received"
                else "hash_mismatch"
            }
            continue
        path = rec["table"]
        ext = rec["format"]
        r = release(item)
        dest = "data/processed/" + i + "/"
        (root / dest).mkdir(parents=True, exist_ok=True)
        variables = concepts(s, i)
        numeric = [k for k, v in variables.items() if v.get("type") == "numeric"]
        opts = CAT["files"][r["files"][0]] if r["files"] else {}
        tokens = (item.get("design") or {}).get(
            "missing_tokens", opts.get("missing_tokens", [""])
        )
        try:
            r_native = ext in {"rdata", "rda", "rds"}
            if r_native:
                expr = (
                    "readRDS(" + rq(path) + ")"
                    if ext == "rds"
                    else "{ box <- new.env(parent=baseenv()); names_in_file <- load("
                    + rq(path)
                    + ",envir=box); stopifnot(length(names_in_file)==1L); box[[names_in_file]] }"
                )
                native_body = "d <- " + expr + "\nstopifnot(is.data.frame(d))\n"
            if engine == "r":
                script = Path("scripts") / ("import-" + i + ".R")
                if r_native:
                    body = native_body
                elif ext in {"dta", "sav"}:
                    expr = (
                        "haven::read_dta(" + rq(path) + ")"
                        if ext == "dta"
                        else "haven::read_sav(" + rq(path) + ", user_na=TRUE)"
                    )
                    body = "d <- " + expr + "\n"
                else:
                    body = (
                        "d <- readr::read_delim("
                        + rq(path)
                        + ", delim="
                        + rq(rec["delimiter"])
                        + ", locale=readr::locale(encoding="
                        + rq(rec["encoding"].replace("-sig", ""))
                        + '), col_types=readr::cols(.default=readr::col_character()), na=character(), trim_ws=FALSE, name_repair="minimal", show_col_types=FALSE)\n'
                        + 'stopifnot(nrow(readr::problems(d)) == 0)\nnames(d)[names(d) == ""] <- "source_row"\n'
                        + "for (v in "
                        + rvec(numeric)
                        + ") {\n  x <- d[[v]]\n  x[x %in% "
                        + rvec(tokens)
                        + "] <- NA_character_\n"
                        + "  n <- suppressWarnings(as.numeric(x))\n  stopifnot(!any(is.na(n) & !is.na(x)))\n  d[[v]] <- n\n}\n"
                    )
                body += (
                    "stopifnot(nrow(d) == "
                    + str(rec["rows"])
                    + ", ncol(d) == "
                    + str(rec["columns"])
                    + ")\n"
                    + "saveRDS(d, "
                    + rq(dest + "import.rds")
                    + ")\n"
                    + 'audit <- data.frame(variable=names(d), type=vapply(d, function(x) paste(class(x),collapse="/"), ""), missing=vapply(d,function(x) sum(is.na(x)),integer(1)), label=vapply(d,function(x) paste(attr(x,"label"),collapse=" "), ""))\n'
                    + "dput(lapply(d,attributes),file="
                    + rq(dest + "r-attributes.txt")
                    + ")\n"
                    + "write.csv(audit,"
                    + rq(dest + "r-columns.csv")
                    + ',row.names=FALSE,fileEncoding="UTF-8")\n'
                    + 'print(sessionInfo())\ncat(tail(commandArgs(TRUE),1),"\\n",sep="")\n'
                )
            else:
                script = Path("scripts") / ("import-" + i + ".do")
                body = "clear\nset more off\n"
                bridge_result = None
                if r_native:
                    bridge = Path("scripts") / ("convert-" + i + ".R")
                    write(
                        root / bridge,
                        native_body
                        + "haven::write_dta(d,"
                        + rq(dest + "from-r.dta")
                        + ')\ncat(tail(commandArgs(TRUE),1),"\\n",sep="")\n',
                        True,
                    )
                    if execute:
                        bridge_result = invoke(root, "r", bridge)
                    body += 'use "' + dest + 'from-r.dta", clear\n'
                elif ext == "dta":
                    body += 'use "' + path + '", clear\n'
                elif ext == "sav":
                    body += 'import spss using "' + path + '", clear\n'
                else:
                    enc = rec["encoding"].replace("-sig", "")
                    if not re.fullmatch(r"[A-Za-z0-9_.-]+", enc):
                        raise ValueError("인코딩 이름이 잘못되었습니다.")
                    delim = (
                        "tab"
                        if rec["delimiter"] == "\t"
                        else '"' + rec["delimiter"] + '"'
                    )
                    body += (
                        'import delimited using "'
                        + path
                        + '", clear varnames(1) case(preserve) stringcols(_all) bindquote(strict) maxquotedrows(unlimited) delimiters('
                        + delim
                        + ') encoding("'
                        + enc
                        + '")\n'
                    )
                    if opts.get("header", [])[:1] == ["source_row"]:
                        body += "rename v1 source_row\n"
                    for k in numeric:
                        allowed_field(k)
                        for val in tokens:
                            if not re.fullmatch(r"[-a-zA-Z0-9. ]*", str(val)):
                                raise ValueError(
                                    "결측코드는 안전한 숫자·문자열이어야 합니다."
                                )
                            body += (
                                "replace "
                                + k
                                + ' = "" if '
                                + k
                                + ' == "'
                                + str(val)
                                + '"\n'
                            )
                        body += (
                            "destring "
                            + k
                            + ", replace\nconfirm numeric variable "
                            + k
                            + "\n"
                        )
                body += (
                    f'assert _N == {rec["rows"]}\nquietly describe\nassert r(k) == {rec["columns"]}\ndescribe\ncodebook, compact\nsave "'
                    + dest
                    + 'import.dta", replace\n'
                )
            write(root / script, body, True)
            scripts.append((i, script))
            items[i] = (
                invoke(root, engine, script)
                if execute
                and not (
                    engine == "stata"
                    and r_native
                    and bridge_result["status"] != "passed"
                )
                else {"status": "generated", "script": script.as_posix()}
            )
            if engine == "stata" and r_native:
                items[i]["r_conversion"] = bridge_result or {"status": "generated"}
                if execute and bridge_result["status"] != "passed":
                    items[i].update(
                        status="unavailable",
                        reason="R 원본을 Stata DTA로 변환하려면 Rscript와 haven이 필요합니다.",
                    )
            items[i]["source_sha256"] = rec["sha256"]
        except (ValueError, OSError) as e:
            items[i] = {"status": "failed", "reason": str(e)}
    names = [
        i
        for i, _ in scripts
        if items[i]["status"] in ("passed", "generated", "unavailable")
    ]
    bundle = None
    if names:
        if engine == "r":
            script = Path("scripts/load-study.R")
            body = (
                "ids <- "
                + rvec(names)
                + '\ndatasets <- setNames(lapply(ids,function(i) readRDS(file.path("data","processed",i,"import.rds"))),ids)\nsaveRDS(datasets,"data/processed/datasets.rds")\ncat(tail(commandArgs(TRUE),1),"\\n",sep="")\n'
            )
        else:
            script = Path("scripts/load-study.do")
            body = "* Stata 16+ frames. Earlier versions can run each import do-file independently.\nversion 16\n"
            for n, i in enumerate(names):
                body += f'capture frame drop ph{n + 1}\nframe create ph{n + 1}\nframe ph{n + 1}: use "data/processed/{i}/import.dta", clear\n'
                body += f"* ph{n + 1} = {i}\n"
        write(root / script, body, True)
        if (
            execute
            and engine == "r"
            and all(items[i]["status"] == "passed" for i in names)
        ):
            bundle = invoke(root, engine, script)
    return {"engine": engine, "datasets": items, "bundle": bundle}
