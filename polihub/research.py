"""Record the plan before estimation. Each estimate belongs to one validated release."""

import csv
import math
import platform
import uuid
from pathlib import Path

import numpy as np
import pandas as pd

from .core import (
    RESULT_COLS,
    VERIFY_COLS,
    concepts,
    dataset,
    digest,
    fingerprint,
    ident,
    init_dirs,
    now,
    read,
    table,
    write,
)
from .design import comparison_review, reviewed_script
from .engines import allowed_field, imports, invoke, rq
from .formats import missing_mask
from .validation import load_item, sample_mask, validate


def sample(root, s, model):
    i = model["dataset"]
    item = next((it for it in s["datasets"] if it["id"] == i), None)
    if not item:
        raise ValueError("모형에 지정한 데이터셋이 연구명세에 없습니다.")
    m = read(root / "acquisition_manifest.json")["items"][i]
    df, meta, design, tokens = load_item(root, s, item, m)
    mask = sample_mask(df, item, design)
    method = model["method"]
    if method not in {"count", "mean", "ols"}:
        raise ValueError("이 추정법에는 별도로 검토한 분석 코드가 필요합니다.")
    for k in (
        "estimand",
        "unit",
        "sample_rule",
        "timing",
        "assumptions",
        "missing",
        "se",
        "weights",
    ):
        if not model.get(k):
            raise ValueError(f"분석계획에 {k}를 기록하세요.")
    if (
        model["weights"] != "none"
        or model["se"] not in {"classical", "none"}
        or any(
            model.get(k)
            for k in (
                "cluster",
                "strata",
                "treatment",
                "fixed_effects",
                "survey_design",
            )
        )
    ):
        raise ValueError("내장 추정기가 지원하지 않는 가중치·표본설계·인과모형입니다.")
    if model["missing"] not in ("complete_case", "error"):
        raise ValueError("결측 처리 방법은 complete_case 또는 error입니다.")
    if method == "ols" and model["se"] != "classical":
        raise ValueError("내장 OLS는 고전적 표준오차를 사용합니다.")
    if dataset(i)["design"] == "panel" and not design.get("panel_id"):
        raise ValueError("패널 식별자·차수와 표본이탈을 확인해야 합니다.")
    if model.get("geography_longitudinal") and not s.get("design_review", {}).get(i):
        raise ValueError(
            "지역·선거구·기관 경계 변경 및 대응표의 검토 기록이 필요합니다."
        )
    if not model.get("design_basis") and dataset(i)["design"] in (
        "panel",
        "cross",
        "single_cross",
        "mixed",
    ):
        raise ValueError("조사표본의 가중치·층화·집락 처리 근거가 필요합니다.")
    book = concepts(s, i)
    preds = model.get("predictors", []) + model.get("controls", [])
    if not isinstance(preds, list) or len(set(preds)) != len(preds):
        raise ValueError("설명변수와 통제변수 목록을 확인하세요.")
    if method == "ols" and (not preds or model.get("outcome") in preds):
        raise ValueError("종속변수와 서로 다른 설명변수가 필요합니다.")
    if method != "ols" and preds or method == "count" and model.get("outcome"):
        raise ValueError("지정한 추정법이 사용하지 않는 변수가 있습니다.")
    used = ([model["outcome"]] if method != "count" else []) + preds
    filters = model.get("filters", [])
    for k in used + [f["variable"] for f in filters]:
        allowed_field(k)
        if k not in df or k not in book:
            raise ValueError(
                f"실제 파일과 근거 있는 변수사전에 모두 있어야 합니다 · {k}"
            )
        v = book[k]
        if v.get("reverse") or v.get("derivation"):
            raise ValueError("파생·역코딩 변수는 검토한 변환 파일과 코드가 필요합니다.")
    missing = pd.Series(False, index=df.index)
    numeric = {}
    for k in set(used + [f["variable"] for f in filters]):
        extra = book[k].get("missing_codes", [])
        if isinstance(extra, (str, bytes)):
            raise ValueError("missing_codes는 코드 목록입니다.")
        bad = missing_mask(df[k], k, meta, tokens + extra + [str(x) for x in extra])
        missing |= bad
        if k in used or book[k].get("type") == "numeric":
            if book[k].get("type") != "numeric":
                raise ValueError(f"코드북에 수치형으로 정의하지 않은 변수 · {k}")
            v = pd.to_numeric(df[k].mask(bad), errors="coerce")
            if ((~bad) & (v.isna() | ~np.isfinite(v))).any():
                raise ValueError(f"수치형으로 해석할 수 없는 코드 · {k}")
            numeric[k] = v
    comparisons = {
        "eq": lambda x, y: x == y,
        "ge": lambda x, y: x >= y,
        "le": lambda x, y: x <= y,
    }
    for f in filters:
        k = f["variable"]
        if f["op"] not in comparisons:
            raise ValueError("지원하는 필터 비교는 eq·ge·le입니다.")
        series = numeric.get(k, df[k].astype(str))
        value = float(f["value"]) if k in numeric else str(f["value"])
        mask &= comparisons[f["op"]](series, value)
    n_before = int(mask.sum())
    if model["missing"] == "error" and (mask & missing).any():
        raise ValueError("허용되지 않은 결측값이 있습니다.")
    mask &= ~missing
    if not mask.any():
        raise ValueError("분석 표본이 비었습니다.")
    if method == "ols":
        x = np.column_stack(
            [np.ones(int(mask.sum()))] + [numeric[k][mask] for k in preds]
        )
        if x.shape[0] <= x.shape[1] or np.linalg.matrix_rank(x) < x.shape[1]:
            raise ValueError("OLS 표본 수 또는 행렬 순위가 부족합니다.")
    if method == "mean" and model["se"] == "classical" and mask.sum() < 2:
        raise ValueError("평균의 표준오차를 구하려면 두 관측치 이상이 필요합니다.")
    return (np.flatnonzero(mask) + 1).tolist(), preds, n_before


def model_script(root, engine, model, rows, preds, seed):
    name = ident(model["id"])
    i = ident(model["dataset"])
    method = model["method"]
    idx = "scripts/sample-" + name + ".csv"
    table(root / idx, ["row"], ({"row": j} for j in rows))
    out = "outputs/" + name + "-estimates.csv"
    data = "data/processed/" + i + "/import."
    if engine == "r":
        path = Path("scripts") / ("estimate-" + name + ".R")
        body = f"set.seed({seed})\nd <- readRDS({rq(data + 'rds')})\nd <- d[read.csv({rq(idx)})$row,,drop=FALSE]\n"
        if method == "count":
            body += 'result <- data.frame(statistic="count",estimate=nrow(d),std_error=NA_real_)\n'
        elif method == "mean":
            body += "y <- as.numeric(d[[" + rq(model["outcome"]) + "]])\n"
            se = "sd(y)/sqrt(length(y))" if model["se"] == "classical" else "NA_real_"
            body += f'result <- data.frame(statistic="mean",estimate=mean(y),std_error={se})\n'
        else:
            formula = model["outcome"] + " ~ " + " + ".join(preds)
            body += (
                "fit <- lm(as.formula(" + rq(formula) + "),data=d,na.action=na.fail)\n"
            )
            body += 'z <- coef(summary(fit))\nresult <- data.frame(statistic=ifelse(rownames(z)=="(Intercept)","intercept",rownames(z)),estimate=z[,1],std_error=z[,2])\n'
        body += (
            "result$n <- nrow(d)\noptions(digits=17)\nwrite.csv(result,"
            + rq(out)
            + ',row.names=FALSE,na="")\nprint(sessionInfo())\ncat(tail(commandArgs(TRUE),1),"\\n",sep="")\n'
        )
    else:
        path = Path("scripts") / ("estimate-" + name + ".do")
        body = (
            f'set seed {seed}\nimport delimited using "{idx}", clear\nisid row\ntempfile sample\nsave '
            + chr(96)
            + "sample'\n"
            + 'use "'
            + data
            + 'dta", clear\n'
        )
        body += (
            "confirm new variable row\ngenerate long row = _n\nmerge 1:1 row using "
            + chr(96)
            + "sample', keep(match) nogen\n"
        )
        body += (
            "tempname handle\ntempfile stats\npostfile "
            + chr(96)
            + "handle' str32 statistic double(estimate std_error n) using "
            + chr(96)
            + "stats'\n"
        )
        post = "post " + chr(96) + "handle' "
        if method == "count":
            body += post + '("count") (_N) (.) (_N)\n'
        elif method == "mean":
            se = "(r(sd)/sqrt(r(N)))" if model["se"] == "classical" else "(.)"
            body += (
                "quietly summarize "
                + model["outcome"]
                + "\n"
                + post
                + '("mean") (r(mean)) '
                + se
                + " (r(N))\n"
            )
        else:
            body += "quietly regress " + model["outcome"] + " " + " ".join(preds) + "\n"
            for k in ["_cons", *preds]:
                body += (
                    post
                    + f'("{k if k != "_cons" else "intercept"}") (_b[{k}]) (_se[{k}]) (e(N))\n'
                )
        body += (
            "postclose "
            + chr(96)
            + "handle'\nuse "
            + chr(96)
            + "stats', clear\nformat estimate std_error %24.17g\n"
            + 'export delimited using "'
            + out
            + '", replace datafmt\n'
        )
    write(root / path, body, True)
    return path, root / out


def reference_table(refs, results, executed):
    indexed = {(row["model"], row["statistic"]): row for row in results}
    verified = []
    for ref in refs:
        row = {
            k: ref.get(k)
            for k in ("table", "model", "statistic", "measure", "original", "tolerance")
        }
        row.update(decision="미실행", reason="추정 결과 없음")
        found = indexed.get((ref.get("model"), ref.get("statistic")))
        needed = (
            "source",
            "table",
            "model",
            "statistic",
            "original",
            "tolerance",
            "tolerance_basis",
            "unit",
            "expected_n",
            "data_version",
            "sample",
            "coding",
            "weights",
            "estimator",
            "se",
            "rounding",
            "original_code",
        )
        row["measure"] = ref.get("measure", "estimate")
        if any(ref.get(k) is None or ref.get(k) == "" for k in needed):
            row.update(
                decision="현재 자료로 검증 불가",
                reason="원문 표·버전·사양·표본·단위·사전 허용오차 근거 필요",
            )
        elif not found and executed:
            row.update(
                decision="현재 자료로 검증 불가", reason="대응되는 모형·통계량 없음"
            )
        elif found:
            try:
                tolerance = float(ref["tolerance"])
                expected = float(ref["original"])
                expected_n = int(ref["expected_n"])
                if (
                    not math.isfinite(tolerance)
                    or tolerance < 0
                    or not math.isfinite(expected)
                    or expected_n < 1
                ):
                    raise ValueError()
            except (TypeError, ValueError, OverflowError):
                row.update(
                    decision="현재 자료로 검증 불가",
                    reason="사전 허용오차·원문값·표본수 확인 필요",
                )
                verified.append(row)
                continue
            measure = ref.get("measure", "estimate")
            if measure not in {"estimate", "std_error", "n"} or found.get(measure) in (
                None,
                "",
                ".",
            ):
                row.update(
                    decision="현재 자료로 검증 불가",
                    reason="대응되는 추정량·표준오차·표본 수 없음",
                )
                verified.append(row)
                continue
            value = float(found[measure])
            diff = value - expected
            same_n = expected_n == found["n"]
            same_unit = ref["unit"] == found["unit"]
            same_ver = ref["data_version"] == found.get("data_version")
            if not all(
                ref[k] == found.get(k)
                for k in ("sample", "coding", "weights", "estimator", "se")
            ):
                row.update(
                    reproduced=value,
                    difference=diff,
                    n=found["n"],
                    decision="현재 자료로 검증 불가",
                    reason="원문과 실행계획의 표본·코딩·가중치·추정법·표준오차 설정 대조 필요",
                )
                verified.append(row)
                continue
            match = abs(diff) <= tolerance and same_n and same_unit and same_ver
            row.update(
                reproduced=value,
                difference=diff,
                n=found["n"],
                decision="재현" if match else "미재현",
                reason="사전 기준 충족"
                if match
                else "버전·표본수·단위·차이 및 코딩/가중치/추정법 대조 필요",
            )
        verified.append(row)
    return verified


def publish_outputs(root, s, state, results=(), refs=()):
    out = root / "outputs"
    table(out / "results.csv", RESULT_COLS, results)
    table(out / "verification.csv", VERIFY_COLS, refs)
    verdict = {r["decision"] for r in refs}
    decision = (
        "부분 재현"
        if "재현" in verdict and len(verdict) > 1
        else next(iter(verdict))
        if len(verdict) == 1
        else "현재 자료로 검증 불가"
        if verdict
        else "미실행"
    )
    report = [
        "# 연구 실행 보고서",
        "",
        s.get("question")
        or "연구질문 미입력. 수집·가져오기·자료점검 단계까지 기록합니다.",
        "",
        "| 자료 | 수집 | 엔진 가져오기 | 자료점검 |",
        "|---|---|---|---|",
    ]
    for i, v in state["datasets"].items():
        report.append(
            f"| {dataset(i)['name']} | {v['acquisition']} | {v['import']} | {v['validation']} |"
        )
    report += [
        "",
        f"분석 상태 · {state['analysis_status']}",
        "",
        f"원문 수치 대조 · {decision}",
    ]
    if results:
        report += ["", "| 모형 | 통계량 | 추정값 | 표본 수 |", "|---|---|---:|---:|"]
        report.extend(
            f"| {r['model']} | {r['statistic']} | {r['estimate']:.8g} | {r['n']} |"
            for r in results
        )
    for mod in state.get("models", []):
        report.append(
            "\n"
            + mod["id"]
            + " · "
            + mod["status"]
            + " · "
            + mod.get("reason", mod.get("rationale", ""))
        )
    report += [
        "",
        "가설 방향 · " + state["hypothesis"],
        "인과적 타당성 · "
        + state["causal_validity"]
        + ". 변수 대응, 표본 구성과 사전 가정은 연구명세 및 자료점검 보고서에 기록합니다.",
    ]
    write(out / "report_ko.md", "\n".join(report) + "\n", True)
    state.update(
        finished=now(),
        reproduction=decision,
        results_sha256=digest(out / "results.csv"),
        files=[
            p.relative_to(root).as_posix()
            for folder in ("outputs", "scripts", "logs")
            for p in sorted((root / folder).rglob("*"))
            if p.is_file() and p.name != "artifact_manifest.json"
        ]
        + [
            "study.json",
            "acquisition_manifest.json",
            "variable_map.csv",
            "comparability_report.md",
            "validation_report.json",
        ],
    )
    write(out / "artifact_manifest.json", state)


def workflow(root, s, engine, estimate=False, execute=True, local_scripts=False):
    init_dirs(root)
    validation = validate(root, s)
    m = read(root / "acquisition_manifest.json", {}).get("items", {})
    tag = uuid.uuid4().hex[:12]
    # Keep the pre-estimation plan and previous outputs, even when this run is blocked.
    out = root / "outputs"
    previous = read(out / "artifact_manifest.json", {})
    if previous.get("run_id"):
        from shutil import copy2

        old = root / "logs" / ("run-" + previous["run_id"]) / "outputs"
        old.mkdir(exist_ok=True, parents=True)
        for f in out.iterdir():
            if f.is_file():
                copy2(f, old / f.name)
                if f.name.endswith("-estimates.csv"):
                    f.unlink()
        for fig in (out / "figures").glob("*.png"):
            copy2(fig, old / fig.name)
            fig.unlink()
    write(
        out / "artifact_manifest.json",
        {
            "run_id": tag,
            "analysis_status": "실행 중",
            "spec_hash": fingerprint(s),
            "started": now(),
        },
    )
    table(out / "results.csv", RESULT_COLS, [])
    table(out / "verification.csv", VERIFY_COLS, [])
    snapshot = root / "logs" / ("run-" + tag) / "study.json"
    write(snapshot, s)
    imports_result = imports(root, s, m, engine, execute)
    command = "run" if estimate else "prepare"
    state = {
        "run_id": tag,
        "started": now(),
        "engine": engine,
        "seed": s.get("seed", 2026),
        "imports": imports_result,
        "dependencies": {
            "python": platform.python_version(),
            "pandas": pd.__version__,
            "numpy": np.__version__,
        },
        "command": f"python -m polihub {command} --spec study.json --engine {engine}"
        + ("" if execute else " --scripts-only"),
        "plan_snapshot": snapshot.relative_to(root).as_posix(),
        "spec_hash": fingerprint(s),
        "datasets": {},
        "models": [],
        "analysis_status": "미실행",
        "hypothesis": "미판정",
        "causal_validity": "미평가",
    }
    for i, v in validation["datasets"].items():
        state["datasets"][i] = {
            "acquisition": m.get(i, {}).get("status", "not_acquired"),
            "validation": v["status"],
            "import": imports_result["datasets"][i]["status"],
            "source_hash": m.get(i, {}).get("sha256"),
        }
    results = []
    models = [*s.get("analyses", []), *s.get("sensitivity", [])]
    alternatives = {str(m.get("id")) for m in s.get("sensitivity", [])}
    base_models = {m.get("id") for m in s.get("analyses", [])}
    if estimate and s.get("question", "").strip() and models:
        seen = set()
        for model in models:
            result = {
                "id": str(model.get("id", "unknown")),
                "status": "blocked",
                "rationale": model.get("rationale", ""),
            }
            try:
                name = ident(model["id"])
                if name in seen:
                    raise ValueError("모형 ID 중복")
                seen.add(name)
                if name in alternatives and (
                    model.get("base_model") not in base_models
                    or not model.get("rationale")
                ):
                    raise ValueError(
                        "민감도 사양에는 기본 모형과 대안 선택 이유가 필요합니다."
                    )
                selected_item = next(
                    (x for x in s["datasets"] if x["id"] == model["dataset"]), None
                )
                if not selected_item:
                    raise ValueError("선택되지 않은 데이터셋")
                comparison_review(s, selected_item, model)
                d = state["datasets"][model["dataset"]]
                if d["validation"] != "checked" or any(
                    x["status"] != "checked" for x in validation["join_checks"]
                ):
                    raise ValueError("자료 또는 결합키 검증 문제를 먼저 해결하세요.")
                seed = int(state["seed"])
                if not 0 <= seed <= 2147483647:
                    raise ValueError("난수시드 범위를 확인하세요.")
                custom = model["method"] == "script"
                if custom:
                    script, path, expected, n_available = reviewed_script(
                        root, s, model, engine, m[model["dataset"]], local_scripts
                    )
                    result.update(script=script.as_posix(), eligible=n_available)
                else:
                    rows, preds, n_before = sample(root, s, model)
                    script, path = model_script(root, engine, model, rows, preds, seed)
                    expected = (
                        {"mean"}
                        if model["method"] == "mean"
                        else {"count"}
                        if model["method"] == "count"
                        else {"intercept", *preds}
                    )
                    n_available = len(rows)
                    result.update(
                        script=script.as_posix(),
                        included=n_available,
                        before_missing=n_before,
                    )
                if not execute or d["import"] != "passed":
                    raise ValueError(
                        "분석 스크립트 생성. 엔진 가져오기 실행 확인 필요."
                    )
                path.unlink(missing_ok=True)
                result["execution"] = invoke(root, engine, script)
                if result["execution"]["status"] != "passed" or not path.is_file():
                    raise ValueError("엔진의 결과 파일·완료 로그를 확인하세요.")
                with path.open(encoding="utf-8-sig", newline="") as f:
                    raw = list(csv.DictReader(f))
                if {r["statistic"] for r in raw} != expected or len(raw) != len(
                    expected
                ):
                    raise ValueError("모형 결과 통계량 불일치")
                parsed = []
                for row in raw:
                    n = int(float(row["n"]))
                    val = float(row["estimate"])
                    se = row.get("std_error", "")
                    if (
                        not math.isfinite(val)
                        or not 0 < n <= n_available
                        or (not custom and n != n_available)
                    ):
                        raise ValueError("추정치 또는 표본수 불일치")
                    parsed.append(
                        dict(
                            model=name,
                            statistic=row["statistic"],
                            estimate=val,
                            std_error=se,
                            n=n,
                            unit=model["unit"],
                            sample=model["sample_rule"],
                            coding=model.get("coding"),
                            weights=model["weights"],
                            estimator=model.get("method_detail", model["method"]),
                            se=model["se"],
                            data_version=m[model["dataset"]]["version"],
                        )
                    )
                results.extend(parsed)
                result["status"] = "passed"
            except (KeyError, TypeError, ValueError, OSError) as e:
                result["reason"] = str(e)
            state["models"].append(result)
        successes = sum(r["status"] == "passed" for r in state["models"])
        state["analysis_status"] = (
            "완료"
            if successes == len(models)
            else "일부 완료"
            if successes
            else "실행 차단"
        )
    if results:
        import matplotlib

        matplotlib.use("Agg")
        from matplotlib import pyplot as plt

        for model in {r["model"] for r in results}:
            pts = [r for r in results if r["model"] == model]
            fig, ax = plt.subplots(figsize=(6, max(2, len(pts) * 0.45 + 1)))
            ax.scatter([r["estimate"] for r in pts], range(len(pts)), color="#08745e")
            ax.set_yticks(range(len(pts)), [r["statistic"] for r in pts])
            ax.set_xlabel("Estimate")
            fig.tight_layout()
            fig.savefig(out / "figures" / (model + ".png"), dpi=150)
            plt.close(fig)
    hypothesis = s.get("hypothesis")
    if hypothesis:
        found = next(
            (
                v
                for v in results
                if v["model"] == hypothesis.get("model")
                and v["statistic"] == hypothesis.get("statistic")
            ),
            None,
        )
        sign = {"positive": 1, "negative": -1}.get(hypothesis.get("direction"))
        if found and sign:
            state["hypothesis"] = (
                "사전 예상과 방향 일치"
                if found["estimate"] * sign > 0
                else "사전 예상과 방향 불일치"
            )
    state["estimates"] = results
    refs = reference_table(s.get("references", []), results, bool(results))
    publish_outputs(root, s, state, results, refs)
    return state
