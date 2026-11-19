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
):
    missing = {
        key: a
        for key, a in index.rows.items()
        if counted(a) and a.get("kind") != "notice" and not a.get("has_abstract")
    }
    found = set()
    checks = defaultdict(dict)
    summary = {}
    hosts = {}
    progress = Counter()
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
        for e in evidence:
            if e not in article.setdefault("methods", []):
                article["methods"].append(e)
        article.pop("abstract_check", None)
        found.add(key)
        return True

    def visit(keys):
        groups = defaultdict(list)
        for key in keys:
            url = publisher_url(missing[key])
            if url:
                groups[urlsplit(url).hostname].append((key, url))
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
                    ok = keep(key, text, "공식 저널", url, location) if text else False
                    checks[key]["공식 저널"] = "확인" if ok else location

    # Test the platform for every represented journal/year before using bulk metadata.
    samples = {}
    for key in sorted(missing):
        a = missing[key]
        samples.setdefault((a["journal"], publication(a)[:4]), key)
    visit(samples.values())
    print(
        f"공식 저널 우선 대조 {len(samples)}편 · 초록 {len(found)}편 확인", flush=True
    )

    for source, size, lookup in providers:
        candidates = [k for k in missing if k not in found and missing[k].get("doi")]
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
        )
        print(
            f"{source} DOI 대조 · {requests}회 요청 · 보완 {summary[source]['recovered']}편 · {error}",
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
        if key in found:
            data["sources"][a["abstract_proof"]["source"]] += 1
        else:
            a["abstract_check"] = dict(date=day, sources=checks[key])
    summary["공식 저널"] = dict(
        outcomes=dict(sum((row["publisher"] for row in by_journal.values()), Counter()))
    )
    return found, dict(
        targeted=len(missing),
        recovered=len(found),
        providers=summary,
        journals=by_journal,
    )
