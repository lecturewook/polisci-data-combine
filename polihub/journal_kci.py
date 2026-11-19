"""한국연구재단 공개 저널 목록과 논문별 RIS 서지를 대조한다."""

import re
from collections import defaultdict
from http.client import HTTPException
from urllib.error import HTTPError

from bs4 import BeautifulSoup

from .journal_publisher import AGENT, Publisher
from .journal_records import abstract_text, doi, pub_type

ARTICLE = "https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId="
KCI_ERRORS = (OSError, HTTPException, ValueError)


def annual_counts(page, name):
    soup = BeautifulSoup(page, "html.parser")
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


def search_ids(page):
    soup = BeautifulSoup(page, "html.parser")
    result = {}
    for link in soup.select("a[href],a[onclick]"):
        attrs = str(link.get("href", "")) + " " + str(link.get("onclick", ""))
        aid = re.search(r"\bART\d{8,12}\b", attrs)
        if aid and len(link.get_text(strip=True)) > 2:
            result[aid[0]] = None
    return list(result)


def record_kci(page, aid, journal, methods):
    soup = BeautifulSoup(page, "html.parser")
    # KCI offers Korean and English RIS citations, including explicitly marked author keywords and abstracts.
    lines = re.sub(r"<br\s*/?>", "\n", soup.get_text("\n"), flags=re.I)
    bib_ids = set(re.findall(r"@article\s*\{\s*(ART\d+)\s*,", lines, re.I))
    canonical = soup.select_one("link[rel='canonical'], meta[property='og:url']")
    canonical_url = (
        (canonical.get("href") or canonical.get("content") or "") if canonical else ""
    )
    if aid not in bib_ids and not re.search(
        r"artiId=" + re.escape(aid) + r"(?:&|$)", canonical_url
    ):
        raise ValueError("KCI 논문 고유 ID 불일치")
    records = []
    for block in re.findall(r"(?ms)^\s*TY\s+-\s*JOUR\s*\n(.*?)^\s*ER\s*-", lines):
        tags = defaultdict(list)
        key = ""
        for line in block.splitlines():
            part = re.match(r"^([A-Z][A-Z0-9])\s+-\s*(.*)$", line.strip())
            if part:
                key = part[1]
                tags[key].append(part[2].strip())
            elif key and line.strip():
                tags[key][-1] += " " + line.strip()
        if tags["TI"] and set(tags["SN"]).intersection(
            {journal["print_issn"], journal["online_issn"]}
        ):
            records.append(tags)
    if not records:
        raise ValueError("KCI 논문 RIS 또는 저널 ISSN 미확인")
    r = records[0]
    year = next((x for x in r["PY"] if re.fullmatch(r"20\d{2}", x)), "")
    if not year:
        raise ValueError("KCI 발행연도 미확인")
    identifiers = {d for t in records for v in t["DO"] if (d := doi(v))}
    if len(identifiers) > 1:
        raise ValueError("KCI 국·영문 DOI 불일치")
    titles = list(dict.fromkeys(t["TI"][0] for t in records))
    title = titles[0]
    url = ARTICLE + aid
    keywords = list(
        dict.fromkeys(
            k.strip()
            for t in records
            for values in t["KW"]
            for k in values.split(",")
            if k.strip()
        )
    )
    result = dict(
        journal=journal["id"],
        title=title,
        other_titles=titles,
        authors=r["AU"],
        doi=next(iter(identifiers), ""),
        published=year,
        volume=next(iter(r["VL"]), ""),
        issue=next(iter(r["IS"]), ""),
        pages="–".join([*r["SP"], *r["EP"]]),
        url=url,
        kci_url=url,
        source_ids=["KCI:" + aid],
        sources=["KCI · 공개 서지"],
        kind=pub_type(title, "journal-article"),
        keywords=keywords,
    )
    abstracts = [
        ab for t in records for value in t["AB"] if (ab := abstract_text(value))
    ]
    if abstracts:
        result.update(
            has_abstract=True,
            abstract_proof=dict(
                source="한국학술지인용색인(KCI)",
                url=url,
                location="KCI 논문별 RIS 초록",
            ),
        )
    result["methods"] = methods.classify(
        titles[-1], abstracts[-1] if abstracts else "", url
    )
    return result


def public_page(host, url):
    host.robots()
    if host.stop or not host.policy.can_fetch(url, AGENT):
        raise ValueError(host.stop or "robots 정책에 따라 보류")
    try:
        body, mime = host.request(url, 2_000_000)
    except HTTPError as exc:
        if exc.code in (401, 403, 429, 451):
            host.stop = f"KCI HTTP {exc.code}"
        raise ValueError(f"HTTP {exc.code}") from None
    if mime not in ("text/html", "application/xhtml+xml"):
        raise ValueError("KCI HTML 응답 미확인")
    return body


def compare_kci(spec, day, prior=None, host=None, *, receive=None, known=()):
    audit = {**(prior or spec["snapshot"]), "url": spec["url"], "attempted": str(day)}
    recent = {"listed": 0, "received": 0, "known": 0, "errors": []}
    audit["recent"] = recent
    host = host or Publisher("www.kci.go.kr")
    try:
        page = public_page(host, spec["url"])
        try:
            audit.update(
                counts=annual_counts(page, spec["name"]), checked=str(day), error=""
            )
        except ValueError as exc:
            audit["error"] = str(exc)
        if receive:
            ids = search_ids(page)
            recent["listed"] = len(ids)
            if not ids:
                recent["errors"].append("공개 검색 첫 화면에서 논문 링크 미확인")
            for aid in ids:
                if "KCI:" + aid in known:
                    recent["known"] += 1
                    continue
                try:
                    receive(public_page(host, ARTICLE + aid), aid)
                    recent["received"] += 1
                except KCI_ERRORS as exc:
                    if len(recent["errors"]) < 5:
                        recent["errors"].append(f"{aid} · {str(exc)[:100]}")
                    if host.stop or isinstance(exc, (OSError, HTTPException)):
                        break
    except KCI_ERRORS as exc:
        audit["error"] = str(exc)[:140]
    return audit
