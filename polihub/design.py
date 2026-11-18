"""Pre-analysis design checks for international comparisons and researcher-written models."""

import re
from pathlib import Path

from .core import concepts, digest, ident
from .engines import rq
from .validation import load_item, sample_mask


def comparison_review(s, item, model):
    requested = model.get("comparison") or len(item.get("countries", [])) > 1
    if not requested:
        return
    countries = item.get("countries", [])
    if len(countries) < 2:
        raise ValueError("비교할 배포판 국가코드를 둘 이상 지정하세요.")
    if item["id"] == "ert" and item.get("period"):
        return  # Same indicator definition and a common, independently checked period.
    fields = (
        "concept",
        "study_country",
        "country",
        "question",
        "variable",
        "time",
        "scale",
        "population",
        "mode",
        "weights",
        "transform",
        "grade",
        "evidence",
        "translation",
        "fieldwork",
        "institutions",
        "geography",
    )
    reviewed = set()
    for row in s.get("comparisons", []):
        if row.get("dataset") != item["id"]:
            continue
        if any(not row.get(k) for k in fields):
            raise ValueError(
                "비교 대응표의 문항·번역·모집단·조사방식·시점·제도·영토·근거를 완성하세요."
            )
        if row["grade"] not in (
            "동일 프로젝트의 공통문항으로 비교 가능",
            "추가 검토와 가정하에 제한적 비교 가능",
        ):
            raise ValueError("현재 자료로 직접 비교 불가")
        if row["variable"] not in concepts(s, item["id"]):
            raise ValueError("대응표 변수가 변수사전에 없습니다.")
        reviewed.add(row["country"])
    if not set(countries) <= reviewed:
        raise ValueError("선택한 국가별 문항 대응표가 필요합니다.")


def reviewed_script(root, s, model, engine, rec, execute_local):
    """Only the CLI can execute an explicitly pinned study author's script."""
    if not execute_local:
        raise ValueError("연구자가 작성한 스크립트는 연구 폴더에서 CLI로 실행하세요.")
    name = ident(model["id"])
    d = model["dataset"]
    it = next(x for x in s["datasets"] if x["id"] == d)
    fields = (
        "script",
        "script_sha256",
        "statistics",
        "unit",
        "estimand",
        "sample_rule",
        "timing",
        "missing",
        "weights",
        "se",
        "assumptions",
        "method_detail",
        "required_variables",
    )
    if any(not model.get(k) for k in fields):
        raise ValueError(
            "사용자 추정 코드의 해시·설계·필수 변수·출력 통계량이 필요합니다."
        )
    path = Path(model["script"])
    full = (root / path).resolve()
    if (
        path.is_absolute()
        or not full.is_relative_to((root / "scripts").resolve())
        or not re.fullmatch(r"scripts/[A-Za-z0-9_-]+\.(R|do)", path.as_posix())
        or path.suffix != (".R" if engine == "r" else ".do")
        or digest(full) != model["script_sha256"]
    ):
        raise ValueError("스크립트의 연구 경로·확장자·사전 SHA-256 불일치")
    df, meta, design, tokens = load_item(root, s, it, rec)
    book = concepts(s, d)
    sample = sample_mask(df, it, design)
    required = set(model["required_variables"])
    if not required <= set(df.columns) & set(book):
        raise ValueError("사용자 추정에 필요한 열 또는 변수사전 근거가 없습니다.")
    detail = model["method_detail"]
    if detail in {"panel_fe", "did", "event_study"}:
        keys = [model.get("id_key"), model.get("time_key")]
        if (
            not all(k in required for k in keys)
            or df[keys].isna().any().any()
            or df[keys].eq("").any().any()
            or df.duplicated(keys).any()
        ):
            raise ValueError("패널 ID와 시간키의 결측·중복·코드북을 확인하세요.")
    if detail in {"did", "event_study"} and (
        model.get("treatment_time") not in required
        or not model.get("comparison_group")
        or not model.get("identification")
    ):
        raise ValueError("처치시점·비교집단·식별가정이 필요합니다.")
    if detail == "survey" and not all(
        model.get(k) in required for k in ("weight_key", "strata_key", "cluster_key")
    ):
        raise ValueError("복합표본 가중치·층화·집락의 실제 키가 필요합니다.")
    if not isinstance(model["statistics"], list) or not all(
        isinstance(k, str) and k for k in model["statistics"]
    ):
        raise ValueError("예상 통계량 이름을 목록으로 지정하세요.")
    # Reviewable contract. The script reads the approved row list and writes this result file.
    from .core import table, write

    table(
        root / "scripts" / ("sample-" + name + ".csv"),
        ["row"],
        ({"row": n} for n, yes in enumerate(sample, 1) if yes),
    )
    wrapper = Path("scripts") / ("execute-" + name + (".R" if engine == "r" else ".do"))
    if full == (root / wrapper).resolve():
        raise ValueError("생성 실행기와 연구 코드의 경로가 겹칩니다.")
    body = (
        "set.seed("
        + str(int(s["seed"]))
        + ")\nsource("
        + rq(path.as_posix())
        + ',encoding="UTF-8")\nprint(sessionInfo())\ncat(tail(commandArgs(TRUE),1),"\\n",sep="")\n'
        if engine == "r"
        else "set seed " + str(int(s["seed"])) + '\ndo "' + path.as_posix() + '"\n'
    )
    write(root / wrapper, body, True)
    return (
        wrapper,
        root / "outputs" / (name + "-estimates.csv"),
        set(model["statistics"]),
        int(sample.sum()),
    )
