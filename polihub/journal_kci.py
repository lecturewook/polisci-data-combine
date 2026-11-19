"""한국연구재단 공개 학술지 검색의 발행연도별 집계 대조."""

import re
from http.client import HTTPException
from urllib.error import HTTPError

from bs4 import BeautifulSoup

from .journal_publisher import AGENT, Publisher


def annual_counts(page, name):
    soup = BeautifulSoup(page, "html.parser")

    # Each facet label contains the year and the number of results for this journal.
    words = {
        node.get_text(" ", strip=True) for node in soup.select("label, li, a, span")
    }
    words.update(soup.stripped_strings)

    def labels(pattern):
        return [m.groups() for s in words if (m := pattern.fullmatch(s.strip()))]

    if not labels(re.compile(re.escape(name) + r"\s*\([\d,]+\)")):
        raise ValueError("KCI 저널별 검색 결과의 명칭 미확인")
    years = labels(re.compile(r"(20\d\d)\s*\(([\d,]+)\)"))
    if not years:
        raise ValueError("KCI 발행연도별 집계 미확인")
    return {year: int(n.replace(",", "")) for year, n in years}


def compare_kci(spec, day, prior=None, host=None):
    audit = dict(prior or spec["snapshot"])
    audit.update(url=spec["url"], attempted=str(day))
    host = host or Publisher("www.kci.go.kr")
    try:
        host.robots()
        if host.stop or not host.policy.can_fetch(spec["url"], AGENT):
            raise ValueError(host.stop or "robots 정책에 따라 보류")
        html, _ = host.request(spec["url"], 2_000_000)
        audit.update(
            counts=annual_counts(html, spec["name"]), checked=str(day), error=""
        )
    except (OSError, HTTPException, ValueError) as exc:
        audit["error"] = (
            f"HTTP {exc.code}" if isinstance(exc, HTTPError) else str(exc)[:140]
        )
    return audit
