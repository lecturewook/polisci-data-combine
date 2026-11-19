"""GitHub 실행 요약 및 Release 본문."""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from polihub.journals import INDEX, read_index
from polihub.journal_records import counted

state = read_index(INDEX)
run = state["runs"][-1]
counts = Counter(j["status"] for j in state["coverage"].values())
print(
    f"최초 수집 {state['start']} ~ {state['initial_end']} · 이번 수집 {run['date']} (한국 시간)\n"
)
print(
    f"누적 발행 기록 {run['total']:,}건 · 신규 {run['added']:,} · 기존 보완 {run['updated']:,} · 중복 수신 {run['duplicates']:,}\n"
)
print(
    f"Crossref 성공 {counts['success']}종 · 부분 성공 {counts['partial']}종 · 실패 {counts['failed']}종"
)
if run["primary_incomplete"]:
    print("\nCrossref 미완료 저널 " + ", ".join(run["primary_incomplete"]))
rows = [a for a in state["articles"].values() if counted(a)]
eligible = sum(a.get("kind") != "notice" for a in rows)
print(f"\n방법론 집계 대상 {eligible:,}편 · 서평·정정 등 {len(rows) - eligible:,}건")
print(
    f"\n같은 논문으로 연결한 원기록 {run.get('linked', 0)}건 · 기간 밖 원기록 {run.get('outside', 0)}건"
)
audit = run.get("package_audit", {})
if audit.get("matched"):
    print(
        f"\nHarvard Dataverse 서지에서 이번에 대조한 재현 패키지 {audit['matched']:,}건"
    )
if audit.get("error"):
    print("\nHarvard Dataverse 서지 확인 실패 · " + audit["error"])
abstracts = run.get("abstract_audit") or {}
if abstracts:
    print(
        f"\n방법 집계 대상 {abstracts['eligible']:,}편 중 초록 확인 {abstracts['after']:,}편 · 이번에 처음 확인 {abstracts['gained']:,}편"
    )
print(
    "\n출판사 목차 대조 범위와 저널·연도별 분모는 HTML의 저널별 확보 현황에서 확인할 수 있습니다."
)
