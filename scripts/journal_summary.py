"""GitHub 실행 요약 및 Release 본문."""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from polihub.journals import INDEX, read_index

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
print(
    "\n출판사 목차 대조 범위와 저널·연도별 분모는 HTML의 저널별 확보 현황에서 확인할 수 있습니다."
)
