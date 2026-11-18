"""Study-level evidence, grain and coverage checks without joining respondents."""

import pandas as pd

from .acquisition import project_path, valid_local
from .core import CAT, concepts, dataset, fingerprint, now, read, release, table, write
from .formats import load_frame, missing_mask, profile

MAP_COLS = [
    "dataset",
    "release",
    "concept",
    "name",
    "type",
    "original_question",
    "categories",
    "population",
    "wave",
    "missing",
    "reverse",
    "derivation",
    "evidence",
    "location",
]
COMP_COLS = [
    "개념",
    "조사·국가",
    "원문 문항",
    "변수",
    "시점",
    "척도",
    "모집단",
    "조사방식",
    "가중치",
    "변환규칙",
    "비교가능성",
]


def survey_info(item, r):
    overrides = item.get("design") or {}
    for key in ("countries", "country_key"):
        if r["files"] and key in overrides and overrides[key] != r.get(key):
            raise ValueError("고정 배포판에서 검증한 국가 범위와 키가 다릅니다.")
    if overrides and not overrides.get("evidence"):
        raise ValueError("설계·국가·키 지정에는 배포판 코드북 근거가 필요합니다.")
    return {
        **r,
        **{
            k: v
            for k, v in overrides.items()
            if k
            in {
                "keys",
                "panel_id",
                "wave_key",
                "year_key",
                "country_key",
                "countries",
                "weight_key",
                "strata_key",
                "cluster_key",
                "household_key",
                "area_key",
                "missing_tokens",
            }
        },
    }


def load_item(root, s, item, rec):
    if not valid_local(root, rec):
        raise ValueError("원자료가 없거나 보관한 SHA-256과 다릅니다.")
    r = release(item)
    if (
        rec["release"] != r["id"]
        or item.get("version")
        and item["version"] != rec["version"]
    ):
        raise ValueError("수집 파일의 배포판과 연구명세가 다릅니다.")
    opts = dict(encoding=rec.get("encoding"), delimiter=rec.get("delimiter"))
    if r["files"]:
        opts.update(CAT["files"][r["files"][0]])
        if rec["sha256"] != opts["sha256"]:
            raise ValueError("공식 배포판의 고정 해시와 수집 이력이 다릅니다.")
    frame, meta = load_frame(project_path(root, rec["table"]), opts)
    design = survey_info(item, r)
    tokens = design.get("missing_tokens", opts.get("missing_tokens", [""]))
    return frame, meta, design, tokens


def sample_mask(frame, item, r):
    mask = pd.Series(True, index=frame.index)
    countries, period = item.get("countries", []), item.get("period")
    coverage = r.get("countries") or {}
    unknown = set(countries) - set(coverage)
    if unknown:
        raise ValueError("배포판에 확인되지 않은 대상국 " + ", ".join(sorted(unknown)))
    country_key = r.get("country_key")
    if countries:
        if country_key:
            mask &= frame[country_key].astype(str).isin(countries)
            if set(countries) - set(frame.loc[mask, country_key].astype(str)):
                raise ValueError("선택한 국가의 실제 행이 없습니다.")
        elif len(coverage) != 1:
            raise ValueError("국가별 행을 식별할 키가 없습니다.")
    if period:
        year_key = r.get("year_key") or (
            "year" if r.get("time_key") == "year" else None
        )
        if not year_key:
            raise ValueError("달력연도 키를 확인해야 기간을 선택할 수 있습니다.")
        if year_key not in frame:
            raise ValueError("연도 변수가 없습니다.")
        years = pd.to_numeric(frame[year_key], errors="coerce")
        mask &= years.between(*period)
        for c in countries:
            lo, hi = coverage[c]
            if period[0] < lo or period[1] > hi:
                raise ValueError(f"{c} 수록기간을 벗어납니다.")
            if country_key and not (mask & frame[country_key].astype(str).eq(c)).any():
                raise ValueError(f"{c}의 선택 기간 관측치가 없습니다.")
    if not mask.any():
        raise ValueError("선택 범위에 관측치가 없습니다.")
    return mask


def panel_checks(df, design):
    idcol, wave = design.get("panel_id"), design.get("wave_key")
    if not (idcol and wave):
        return {"status": "미점검", "reason": "패널 ID·시간키 근거 필요"}
    if (
        df[[idcol, wave]].isna().any().any()
        or df[[idcol, wave]].eq("").any().any()
        or df.duplicated([idcol, wave]).any()
    ):
        raise ValueError("패널 ID×차수가 유일하지 않거나 결측입니다.")
    waves = sorted(
        df[wave].dropna().unique(),
        key=lambda x: (0, float(x)) if str(x).isdecimal() else (1, str(x)),
    )
    prev, history = None, []
    for w in waves:
        ids = set(df.loc[df[wave] == w, idcol])
        history.append(
            {
                "wave": str(w),
                "n": len(ids),
                "entered_since_previous": len(ids - prev) if prev is not None else None,
                "absent_since_previous": len(prev - ids) if prev is not None else None,
            }
        )
        prev = ids
    switches = {}
    for k in ("household_key", "area_key"):
        if design.get(k):
            switches[k] = int((df.groupby(idcol)[design[k]].nunique() > 1).sum())
    return {
        "status": "checked",
        "waves": history,
        "id_with_changed_codes": switches,
        "note": "진입·미관측 및 가구·지역 코드 변경 지표. 보충표본·분가·이사의 원인은 조사 지침으로 판별.",
    }


def assess(root, s, item, rec):
    d = dataset(item["id"])
    df, meta, r, tokens = load_item(root, s, item, rec)
    audit = profile(df, meta, r.get("keys", []), tokens)
    review = s.get("design_review", {}).get(d["id"], {})
    resolved = (
        review.get("resolved_notes", [])
        if isinstance(review, dict)
        and review.get("evidence")
        and review.get("explanation")
        else []
    )
    issues = [n for n in r.get("blocking_notes", []) if n not in resolved]
    missing_names = set(concepts(s, d["id"])) - set(df.columns)
    if missing_names:
        issues.append("변수사전과 수록 열 불일치 · " + ", ".join(sorted(missing_names)))
    keys = audit["key_check"]
    if audit["missing_keys"] or keys and (keys["missing_rows"] or keys["extra_rows"]):
        issues.append("식별키의 누락·결측·중복을 해소해야 합니다.")
    numeric = [
        v["name"] for v in concepts(s, d["id"]).values() if v.get("type") == "numeric"
    ]
    for k in numeric:
        if k not in df:
            issues.append(f"변수사전에 지정한 열이 없습니다 · {k}")
        elif (
            pd.to_numeric(
                df.loc[~missing_mask(df[k], k, meta, tokens), k], errors="coerce"
            )
            .isna()
            .any()
        ):
            issues.append(f"수치형 변환 불가능 · {k}")
    try:
        audit["selected_rows"] = int(sample_mask(df, item, r).sum())
        audit["panel"] = (
            panel_checks(df, r) if d["design"] == "panel" else {"status": "해당 없음"}
        )
        design = {}
        for role in ("weight_key", "strata_key", "cluster_key"):
            col = r.get(role)
            if col:
                series = df[col]
                design[role] = {
                    "name": col,
                    "levels": int(series.nunique()),
                    "missing": int(missing_mask(series, col, meta, tokens).sum()),
                }
                if role == "weight_key":
                    weight = pd.to_numeric(series, errors="coerce")
                    design[role]["nonpositive"] = int((weight <= 0).sum())
                    if weight.isna().any() or (weight <= 0).any():
                        issues.append("가중치의 결측 또는 비양수 값 검토 필요")
        if r.get("area_key") and (r.get("year_key") or r.get("wave_key")):
            t = r.get("year_key") or r["wave_key"]
            previous = None
            changes = []
            for period, group in df.groupby(t, sort=True):
                codes = set(group[r["area_key"]].dropna().astype(str))
                changes.append(
                    {
                        "period": str(period),
                        "code_count": len(codes),
                        "new_codes": sorted(codes - previous)
                        if previous is not None
                        else [],
                        "absent_codes": sorted(previous - codes)
                        if previous is not None
                        else [],
                    }
                )
                previous = codes
            audit["area_code_turnover"] = changes
        audit["sampling"] = design or {"status": "가중치·층화·집락 미지정"}
    except (KeyError, ValueError, TypeError) as e:
        issues.append(str(e))
    audit.update(
        status="blocked" if issues else "checked",
        issues=issues,
        sha256=rec["sha256"],
        code_changes=s.get("design_review", {}).get(
            d["id"], "경계·기관·선거구 코드 변경 검토 기록 없음"
        ),
        analysis_status="not_run",
    )
    return audit


def check_joins(root, s, man):
    out = []
    items = {v["id"]: v for v in s["datasets"]}
    for spec in s.get("join_checks", []):
        res = {
            "status": "blocked",
            "left": spec.get("left"),
            "right": spec.get("right"),
        }
        try:
            a, b = (items[spec[k]] for k in ("left", "right"))
            if (
                a["id"] == b["id"]
                or not spec.get("evidence")
                or not spec.get("key_definition")
            ):
                raise ValueError("서로 다른 표와 공통키 정의·출처를 지정하세요.")
            if any(dataset(t["id"])["unit"] in {"개인", "가구·개인"} for t in (a, b)):
                raise ValueError(
                    "서로 다른 조사의 개인·가구 ID 연결은 지원하지 않습니다."
                )
            keys = spec["keys"]
            if not isinstance(keys, list) or not keys:
                raise ValueError("결합키가 없습니다.")
            x, y = [load_item(root, s, t, man[t["id"]])[0][keys].copy() for t in (a, b)]
            if any(f.isna().any().any() or f.eq("").any().any() for f in (x, y)):
                raise ValueError("결측 결합키")
            mode = spec["cardinality"]
            if mode not in {"one_to_one", "one_to_many", "many_to_one"}:
                raise ValueError("many-to-many 결합을 차단했습니다.")
            z = x.merge(y, on=keys, how="outer", indicator=True, validate=mode)
            counts = z["_merge"].value_counts()
            left_miss = int(counts["left_only"]) / len(x)
            right_miss = int(counts["right_only"]) / len(y)
            res.update(
                left_rows=len(x),
                right_rows=len(y),
                outer_rows=len(z),
                matched_output_rows=int(counts["both"]),
                left_unmatched_rate=left_miss,
                right_unmatched_rate=right_miss,
            )
            limit = spec.get("max_unmatched", 0)
            if not 0 <= limit <= 1 or max(left_miss, right_miss) > limit:
                raise ValueError("사전에 지정한 미결합 허용비율을 초과했습니다.")
            res["status"] = "checked"
        except (KeyError, ValueError, TypeError) as e:
            res["reason"] = str(e)
        out.append(res)
    return out


def comparability(root, s):
    lines = [
        "# 국가·조사 간 비교가능성",
        "",
        "| " + " | ".join(COMP_COLS) + " |",
        "|" + "---|" * len(COMP_COLS),
    ]

    def row(vals):
        lines.append(
            "| "
            + " | ".join(
                str(x or "확인 필요").replace("|", "／").replace("\n", " ")
                for x in vals
            )
            + " |"
        )

    for item in s["datasets"]:
        i = item["id"]
        d, r = dataset(i), release(item)
        vars_ = concepts(s, i)
        groups = item.get("countries") or [
            "배포판 전체" if r["files"] else "배포판 미확정"
        ]
        for c in groups:
            for v in vars_.values() or [{}]:
                grade = "현재 자료로 직접 비교 불가"
                if (
                    i == "ert"
                    and c in r["countries"]
                    and v.get("name") in {"v2x_polyarchy", "v2x_regime", "dem_ep"}
                ):
                    grade = (
                        "동일 프로젝트의 공통지표로 비교 가능 · 공통 수록연도로 한정"
                    )
                row(
                    [
                        v.get("concept"),
                        d["name"] + " / " + c,
                        v.get("original_question"),
                        v.get("name"),
                        item.get("period") or r["years"] or r["waves"],
                        v.get("categories"),
                        d["population"],
                        r["mode"] or ("전문가·행정 지표" if i == "ert" else None),
                        r["weight"]
                        or ("해당 없음" if r["access"] == "direct" else None),
                        v.get("derivation") or "원척도",
                        grade,
                    ]
                )
    for c in s.get("comparisons", []):
        row(
            [
                c.get(k)
                for k in (
                    "concept",
                    "study_country",
                    "question",
                    "variable",
                    "time",
                    "scale",
                    "population",
                    "mode",
                    "weights",
                    "transform",
                    "grade",
                )
            ]
        )
    lines.extend(
        [
            "",
            "공통 문항과 번역, 표본틀·연령·시민권, 조사방식, 선거일과 실사시점, 척도 방향 및 가중치를 대조한다. 영국 자료에서는 Great Britain과 영국 전체의 범위를 따로 확인한다.",
            "서로 다른 연구의 응답자는 결합하지 않는다. 자료별 표본설계와 정치제도의 차이를 비교의 한계로 기록한다.",
        ]
    )
    write(root / "comparability_report.md", "\n".join(lines) + "\n", True)


def validate(root, s):
    man = read(root / "acquisition_manifest.json", {}).get("items", {})
    reports = {}
    variables = []
    for item in s["datasets"]:
        i = item["id"]
        variables.extend(concepts(s, i).values())
        rec = man.get(i, {})
        if rec.get("status") != "received":
            reports[i] = {
                "status": rec.get("status", "not_acquired"),
                "reason": rec.get("reason", "수집 전"),
            }
            continue
        try:
            reports[i] = assess(root, s, item, rec)
        except (ValueError, KeyError, OSError) as e:
            reports[i] = {"status": "blocked", "issues": [str(e)]}
    result = {
        "checked": now(),
        "spec_hash": fingerprint(s),
        "datasets": reports,
        "join_checks": check_joins(root, s, man),
    }
    write(root / "validation_report.json", result)
    table(root / "variable_map.csv", MAP_COLS, variables)
    comparability(root, s)
    return result
