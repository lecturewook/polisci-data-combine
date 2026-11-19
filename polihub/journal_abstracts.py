"""누락된 초록을 공식 게재처 및 공개 학술 색인과 DOI 단위로 대조한다."""

import json
import os
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from .journal_publisher import AGENT, Publisher, publisher_url
from .journal_records import abstract_text, counted, doi, normalized, publication
from .journal_sources import fetch


def same_title(first, second):
    left, right = normalized(first or ""), normalized(second or "")
    return bool(
        left
        and right
        and (
            left == right
            or (
                min(len(left), len(right)) >= 32
                and SequenceMatcher(None, left, right, autojunk=False).ratio() >= 0.92
            )
        )
    )


def alex_batch(articles):
    query = {
        "filter": "doi:" + "|".join(a["doi"] for a in articles),
        "per_page": len(articles),
        "select": "id,doi,title,abstract_inverted_index",
    }
    if os.getenv("OPENALEX_API_KEY"):
        query["api_key"] = os.environ["OPENALEX_API_KEY"]
    rows = fetch("https://api.openalex.org/works?" + urlencode(query))["results"]
    hits = {}
    for r in rows:
        d = doi(r.get("doi"))
        inv = r.get("abstract_inverted_index") or {}
        positions = sorted(
            (pos, term) for term, places in inv.items() for pos in places
        )
        if positions and [p for p, _ in positions] == list(range(len(positions))):
            text = " ".join(word for _, word in positions)
            hits[d] = (r.get("title"), text, r["id"], "abstract_inverted_index")
    return hits


def semantic_batch(articles):
    url = "https://api.semanticscholar.org/graph/v1/paper/batch?fields=externalIds,title,abstract,url"
    req = Request(
        url,
        data=json.dumps({"ids": ["DOI:" + a["doi"] for a in articles]}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": AGENT},
    )
    try:
        with urlopen(req, timeout=40) as response:
            records = json.load(response)
    except HTTPError as e:
        raise ValueError(f"HTTP {e.code}") from None
    hits = {}
    for r in records:
        if isinstance(r, dict) and r.get("abstract") and r.get("url"):
            d = doi((r.get("externalIds") or {}).get("DOI"))
            hits[d] = (
                r.get("title"),
                r["abstract"],
                r["url"],
                "Academic Graph · abstract",
            )
    return hits


def openaire_batch(articles):
    query = {
        "filter": "type:publication,has_abstract:true,ids.doi:"
        + "|".join(a["doi"] for a in articles),
        "page_size": 100,
        "select": "id,mainTitle,pids,descriptions",
    }
    data = fetch(
        "https://api.openaire.eu/graph/v4/research-products?" + urlencode(query)
    )
    hits = {}
    for r in data["results"]:
        descriptions = {
            s for v in (r.get("descriptions") or []) if (s := abstract_text(v))
        }
        if len(descriptions) != 1:
            continue
        url = "https://explore.openaire.eu/search/result?id=" + quote(r["id"], safe=":")
        for pid in r.get("pids") or []:
            if pid.get("scheme", "").lower() == "doi":
                hits[doi(pid.get("value"))] = (
                    r.get("mainTitle"),
                    next(iter(descriptions)),
                    url,
                    "연구 출판물 · has_abstract",
                )
    return hits


BATCH_SOURCES = (
    ("OpenAlex", 50, alex_batch),
    ("Semantic Scholar", 100, semantic_batch),
    ("OpenAIRE", 40, openaire_batch),
)


def recover_abstracts(
    index,
    taxonomy,
    day,
    *,
    providers=BATCH_SOURCES,
    publisher_factory=Publisher,
    pause=time.sleep,
    recheck_abstracts=False,
    repec=None,
):
    # Older Cambridge search metadata sometimes contains a book citation or a preview.
    # Retain the superseded proof and only suspend method assignments derived from that field.
    revalidate = set()
    for key, a in index.rows.items():
        proof = a.get("abstract_proof") or {}
        if (
            not counted(a)
            or not a.get("has_abstract")
            or "개별 대조" in proof.get("location", "")
        ):
            continue
        suspect = (
            proof.get("source") == "공식 저널"
            and proof.get("location") == "citation_abstract"
            and urlsplit(proof.get("url", "")).hostname == "www.cambridge.org"
        )
        if not suspect and not a.get("publisher_statement"):
            continue
        revalidate.add(key)
        old = dict(proof)
        retired = [
            e
            for e in a.get("methods", [])
            if e.get("level") != "direct"
            and e.get("location") == "공식 저널 · 초록"
            and e.get("url") == proof.get("url")
        ]
        if retired:
            old["methods"] = retired
            a["methods"] = [e for e in a["methods"] if e not in retired]
        a.setdefault("superseded_abstract", old)
        a["has_abstract"] = False
        a.pop("abstract_proof", None)
    missing = {
        key: a
        for key, a in index.rows.items()
        if key in revalidate
        or (counted(a) and a.get("kind") != "notice" and not a.get("has_abstract"))
    }
    found = set()
    checks = defaultdict(dict)
    summary = {}
    hosts = {}
    reused = set()
    progress = Counter()
    prior_kinds = {k: a.get("kind") for k, a in missing.items()}
    for a in missing.values():
        progress[a["journal"]] += 1

    def keep(key, text, source, url, location):
        text = abstract_text(text)
        if not text or not url.startswith("https://"):
            return False
        article = missing[key]
        evidence = taxonomy.classify("", text, url)
        for e in evidence:
            e["location"] = source + " · 초록"
        proof = dict(source=source, url=url, location=location, checked=day)
        # The paper has already been identified. Nothing in this pass creates a bibliographic record.
        article["has_abstract"] = True
        article["abstract_proof"] = proof
        if source == "공식 저널" and article.get("publisher_statement"):
            article["previous_publisher_statement"] = article.pop("publisher_statement")
        for e in evidence:
            if e not in article.setdefault("methods", []):
                article["methods"].append(e)
        article.pop("abstract_check", None)
        found.add(key)
        return True

    def visit(keys):
        groups = defaultdict(list)
        for key in keys:
            a = missing[key]
            url = publisher_url(a)
            last = a.get("abstract_check") or {}
            reason = (last.get("sources") or {}).get("공식 저널", "")
            if (
                not recheck_abstracts
                and url
                and last.get("date") == day
                and last.get("publisher_url", url) == url
                and (last.get("publisher_url") or reason != "확인 가능한 초록란 없음")
                and reason.startswith(
                    (
                        "확인 가능한 초록란 없음",
                        "출판사가 초록 미제공",
                        "출판사 HTTP 403",
                        "robots 정책에 따라 보류",
                        "robots HTTP 403",
                        "출판사 접근 확인 화면",
                        "다른 주소로 이동 · 원문 확인 필요",
                        "HTML 초록 페이지 미확인",
                    )
                )
            ):
                checks[key]["공식 저널"] = reason
                reused.add(key)
                continue
            if url:
                host = urlsplit(url).hostname
                if key in revalidate:
                    canonical = publisher_url(
                        {
                            "publisher_url": (a.get("superseded_abstract") or {}).get(
                                "url"
                            )
                        }
                    )
                    if canonical and urlsplit(canonical).hostname == host:
                        url = canonical
                groups[host].append((key, url))
            else:
                checks[key]["공식 저널"] = "공식 게재처 주소 미확인"

        def one_host(group):
            host, papers = group
            if host not in hosts:
                hosts[host] = publisher_factory(host)
            client = hosts[host]
            results = []
            for key, url in papers:
                text, final, location = client.read(url, missing[key])
                results.append((key, text, final, location))
            return results

        with ThreadPoolExecutor(max_workers=6) as pool:
            for results in pool.map(one_host, groups.items()):
                for key, text, url, location in results:
                    if location == "출판사가 초록 미제공으로 표시":
                        missing[key].setdefault(
                            "publisher_statement",
                            {"url": url, "label": location, "checked": day},
                        )
                    ok = keep(key, text, "공식 저널", url, location) if text else False
                    checks[key]["공식 저널"] = "확인" if ok else location

    # Test the platform for every represented journal/year before using bulk metadata.
    samples = {}
    for key in sorted(missing):
        a = missing[key]
        samples.setdefault((a["journal"], publication(a)[:4]), key)
    first = list(dict.fromkeys([*sorted(revalidate), *samples.values()]))
    visit(first)
    print(f"공식 저널 우선 대조 {len(first)}편 · 초록 {len(found)}편 확인", flush=True)

    scoped = [(source, size, lookup, None) for source, size, lookup in providers]
    if repec:
        from functools import partial
        from .journal_repec import SERIES

        scoped.extend(
            ("RePEc · " + series, len(missing) or 1, partial(repec, jid), jid)
            for jid, series in SERIES.items()
        )
    for source, size, lookup, journal in scoped:
        candidates = []
        reused_dois = 0
        for key, a in missing.items():
            if (
                key in found
                or not a.get("doi")
                or (journal and a["journal"] != journal)
            ):
                continue
            previous = a.get("abstract_check") or {}
            outcome = (previous.get("sources") or {}).get(source)
            if (
                not recheck_abstracts
                and previous.get("date") == day
                and outcome in ("확인 가능한 초록 미수신", "DOI·표제 대조 보류")
            ):
                checks[key][source] = outcome
                reused_dois += 1
            else:
                candidates.append(key)
        requests = 0
        error = ""
        consecutive_errors = 0
        for i in range(0, len(candidates), size):
            keys = candidates[i : i + size]
            try:
                results = lookup([missing[key] for key in keys])
                consecutive_errors = 0
                requests += 1
            except (ValueError, TypeError, KeyError, OSError, HTTPException) as e:
                requests += 1
                error = str(e)[:130]
                consecutive_errors += 1
                for key in keys:
                    checks[key][source] = error
                if consecutive_errors >= 2 or any(
                    code in error for code in ("400", "401", "403", "429")
                ):
                    for key in candidates[i + size :]:
                        checks[key][source] = "후속 요청 보류"
                    break
                pause(2)
                continue
            for key in keys:
                article = missing[key]
                result = results.get(article["doi"])
                if not result:
                    checks[key][source] = "확인 가능한 초록 미수신"
                    continue
                title, text, url, location = result
                if article.get("publisher_statement"):
                    checks[key][source] = "출판사의 초록 미제공 표기와 대조 필요"
                    continue
                if not same_title(article.get("title"), title):
                    checks[key][source] = "DOI·표제 대조 보류"
                    continue
                checks[key][source] = (
                    "확인"
                    if keep(key, text, source, url, location)
                    else "확인 가능한 초록 미수신"
                )
            if i + size < len(candidates):
                pause(1)
        summary[source] = dict(
            requests=requests,
            errors=error,
            recovered=sum(v.get(source) == "확인" for v in checks.values()),
            reused_same_day=reused_dois,
        )
        print(
            f"{source} DOI 대조 · {requests}회 요청 · 같은 날 확인 기록 {reused_dois}편 · 보완 {summary[source]['recovered']}편 · {error}",
            flush=True,
        )

    visit(key for key in missing if key not in found and "공식 저널" not in checks[key])
    by_journal = {
        jid: {"targeted": n, "sources": Counter(), "publisher": Counter()}
        for jid, n in progress.items()
    }
    for key, a in missing.items():
        data = by_journal[a["journal"]]
        if "공식 저널" in checks[key]:
            data["publisher"][checks[key]["공식 저널"]] += 1
        if key in reused:
            data["publisher_reused"] = data.get("publisher_reused", 0) + 1
        if key in found:
            data["sources"][a["abstract_proof"]["source"]] += 1
        else:
            a["abstract_check"] = dict(
                date=day, sources=checks[key], publisher_url=publisher_url(a)
            )
    summary["공식 저널"] = dict(
        outcomes=dict(
            sum((row["publisher"] for row in by_journal.values()), Counter())
        ),
        reused_same_day=len(reused),
    )
    changed_kinds = {k for k, a in missing.items() if a.get("kind") != prior_kinds[k]}
    return found | revalidate | changed_kinds, dict(
        targeted=len(missing),
        recovered=len(found),
        revalidated=len(revalidate),
        providers=summary,
        journals=by_journal,
    )
