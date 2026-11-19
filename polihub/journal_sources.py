"""Crossref 발행목록과 OpenAlex 독립 대조. 응답 원문 및 유료 콘텐츠는 보관하지 않는다."""

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from .journal_records import clean, doi, parts, pub_type


def fetch(url):
    for attempt in range(4):
        try:
            req = Request(
                url,
                headers={
                    "User-Agent": "PoliHub/5 (https://github.com/lecturewook/polisci-data-combine)",
                    "Accept": "application/json",
                },
            )
            with urlopen(req, timeout=55) as response:
                return json.load(response)
        except HTTPError as e:
            if e.code not in (408, 429, 500, 502, 503, 504) or attempt == 3:
                raise ValueError(f"HTTP {e.code}") from None
            delay = e.headers.get("Retry-After", "")
            if delay and not delay.isdigit():
                try:
                    delay = max(
                        0,
                        (
                            parsedate_to_datetime(delay) - datetime.now(timezone.utc)
                        ).total_seconds(),
                    )
                except (TypeError, ValueError):
                    delay = 0
            seconds = max(2**attempt, float(delay or 0))
            if seconds > 120:
                raise ValueError(
                    f"HTTP {e.code} · Retry-After {seconds:.0f}초, 다음 실행에서 재개"
                ) from None
        except (URLError, OSError, HTTPException, json.JSONDecodeError) as e:
            reason = str(getattr(e, "reason", e))
            if "403" in reason or "407" in reason or attempt == 3:
                raise ValueError(reason[:180]) from None
            seconds = 2**attempt
        time.sleep(seconds)


def issns(j):
    return {v for v in (j["print_issn"], j["online_issn"]) if v}


def record_cr(item, j, methods):
    observed = set(item.get("ISSN") or [])
    if observed and not observed.intersection(issns(j)):
        raise ValueError("ISSN 불일치")
    title = clean(next(iter(item.get("title") or []), ""))
    d = doi(item.get("DOI"))
    if not title and not d:
        raise ValueError("제목·DOI 누락")
    url = (
        item.get("resource", {}).get("primary", {}).get("URL")
        or item.get("URL")
        or ("https://doi.org/" + d if d else "")
    )
    abstract = clean(item.get("abstract"))
    authors = [
        clean(
            r.get("name") or " ".join(x for x in (r.get("given"), r.get("family")) if x)
        )
        for r in item.get("author", [])
    ]
    issue = item.get("journal-issue") or {}
    kind = item.get("subtype") or item.get("type") or "journal-article"
    if item.get("update-to"):
        types = [r.get("type", "") for r in item["update-to"]]
        kind = next(
            (t for t in types if t in ("correction", "erratum", "retraction")), kind
        )
    resources = []
    for relation, records in (item.get("relation") or {}).items():
        if relation not in (
            "is-supplemented-by",
            "has-supplement",
            "requires",
            "is-documented-by",
        ):
            continue
        for rel in records:
            target = rel.get("id", "")
            dest = "https://doi.org/" + doi(target) if doi(target) else target
            if urlsplit(dest).scheme in ("https", "http"):
                resources.append(
                    dict(kind="관련 자료", url=dest, source="Crossref · " + relation)
                )
    return dict(
        journal=j["id"],
        title=title,
        authors=list(filter(None, authors)),
        doi=d,
        url=url,
        published=parts(item.get("published") or item.get("issued")),
        online=parts(item.get("published-online")),
        issue_date=parts(
            item.get("published-print")
            or issue.get("published-print")
            or issue.get("published-online")
        ),
        volume=item.get("volume", ""),
        issue=item.get("issue", ""),
        pages=item.get("page", ""),
        kind=pub_type(title, kind),
        source_type=kind,
        keywords=[],
        resources=resources,
        has_abstract=bool(abstract),
        methods=methods.classify(title, abstract, url),
        sources=["Crossref"],
        publisher_url=(item.get("resource") or {}).get("primary", {}).get("URL", ""),
        source_indexed=(item.get("indexed") or {}).get("date-time", ""),
    )


def record_oa(item, j, methods):
    title = clean(item.get("display_name"))
    if not title:
        raise ValueError("제목 누락")
    at = item.get("primary_location") or {}
    src = at.get("source") or {}
    if not set(src.get("issn") or []).intersection(issns(j)):
        raise ValueError("ISSN 불일치")
    d = doi(item.get("doi"))
    url = at.get("landing_page_url") or ("https://doi.org/" + d if d else item["id"])
    b = item.get("biblio") or {}
    kind = item.get("type") or "article"
    if kind in {
        "dataset",
        "software",
        "preprint",
        "dissertation",
        "book",
        "book-chapter",
        "peer-review",
        "grant",
    }:
        return None
    inv = item.get("abstract_inverted_index") or {}
    tokens = sorted((pos, word) for word, positions in inv.items() for pos in positions)
    abstract = " ".join(word for _, word in tokens)
    return dict(
        journal=j["id"],
        title=title,
        doi=d,
        authors=[
            x["author"]["display_name"]
            for x in item.get("authorships", [])
            if (x.get("author") or {}).get("display_name")
        ],
        published=item.get("publication_date")
        or str(item.get("publication_year") or ""),
        volume=b.get("volume") or "",
        issue=b.get("issue") or "",
        pages=b.get("first_page") or "",
        url=url,
        sources=["OpenAlex"],
        source_ids=[item["id"]],
        source_type=kind,
        kind=pub_type(title, kind),
        has_abstract=bool(abstract),
        methods=methods.classify(title, abstract, url),
    )


OA_FIELDS = "id,doi,display_name,authorships,publication_date,publication_year,type,primary_location,biblio,abstract_inverted_index"


def walk(j, start, end, provider, receive, get=fetch, size=None):
    stat = dict(
        reported=None, received=0, pages=0, complete=False, errors=[], skipped=0
    )
    seen = set()
    cr = provider == "Crossref"
    limit = size or (250 if cr else 100)
    scope = f"from-pub-date:{start},until-pub-date:{end},type:journal-article"
    try:
        if cr:
            # Repeated ISSN filters are ORed by Crossref; this includes electronic-only deposits.
            filters = ",".join("issn:" + i for i in sorted(issns(j))) + "," + scope
            base = "https://api.crossref.org/works"
            params = {"filter": filters, "rows": limit}
            if os.getenv("CROSSREF_MAILTO"):
                params["mailto"] = os.environ["CROSSREF_MAILTO"]
        else:
            base = "https://api.openalex.org/works"
            params = {
                "filter": "primary_location.source.issn:"
                + "|".join(sorted(issns(j)))
                + f",from_publication_date:{start},to_publication_date:{end}",
                "per_page": limit,
                "select": OA_FIELDS,
            }
            if os.getenv("OPENALEX_API_KEY"):
                params["api_key"] = os.environ["OPENALEX_API_KEY"]
        cursor = "*"
        while True:
            body = get(base + "?" + urlencode({**params, "cursor": cursor}))
            info = body["message"] if cr else body["meta"]
            items = info["items"] if cr else body["results"]
            total = info["total-results" if cr else "count"]
            nxt = info.get("next-cursor" if cr else "next_cursor")
            if not isinstance(total, int) or not isinstance(items, list):
                raise ValueError("페이지네이션 응답 형식 오류")
            if stat["reported"] is None:
                stat["reported"] = total
            stat["pages"] += 1
            stat["received"] += len(items)
            if items:
                # Crossref can return the same cursor for a different page.
                signature = hashlib.sha256(
                    json.dumps(
                        [r.get("DOI") or r.get("id") or r.get("title") for r in items],
                        ensure_ascii=False,
                    ).encode()
                ).digest()
                if signature in seen:
                    raise ValueError("동일 페이지 반복")
                seen.add(signature)
                for item in items:
                    try:
                        receive(item)
                    except (
                        ValueError,
                        KeyError,
                        TypeError,
                        IndexError,
                        AttributeError,
                    ) as e:
                        stat["skipped"] += 1
                        detail = str(e)[:140]
                        if detail not in stat["errors"] and len(stat["errors"]) < 8:
                            stat["errors"].append(detail)
            if not items or (cr and len(items) < limit) or (not cr and not nxt):
                if stat["received"] != stat["reported"]:
                    raise ValueError("보고 건수와 수신 건수 불일치")
                stat["complete"] = True
                break
            if not nxt:
                raise ValueError("다음 커서 누락")
            cursor = nxt
    except (
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        OSError,
        HTTPException,
    ) as e:
        stat["errors"].append(str(e)[:200])
    return stat
