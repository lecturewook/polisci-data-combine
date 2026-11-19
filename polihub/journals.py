"""누적 저널 색인. python -m polihub.journals"""

import argparse
import gzip
import json
import os
import tempfile
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .core import ROOT, locked, read
from .journal_records import (
    ArticleIndex,
    Methods,
    counted,
    in_window,
    notice_type,
    publication,
)
from .journal_sources import (
    DATAVERSE_TABLE,
    dataverse_table,
    fetch,
    link_dataverse,
    openalex_years,
    record_cr,
    record_oa,
    walk,
)

CATALOG = ROOT / "catalog"
INDEX = CATALOG / "journal-index.json.gz"


def read_index(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        state = json.load(f)
    if (
        state.get("schema") != 1
        or not isinstance(state["articles"], dict)
        or not state["runs"]
    ):
        raise ValueError("누적 색인 형식 오류")
    if (
        sum(counted(a) for a in state["articles"].values())
        != state["runs"][-1]["total"]
    ):
        raise ValueError("누적 색인 합계 불일치")
    return state


def save_index(path, state):
    data = json.dumps(
        state, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        try:
            tmp.write(gzip.compress(data, compresslevel=6, mtime=0))
            tmp.flush()
            os.fsync(tmp.fileno())
        except BaseException:
            os.unlink(tmp.name)
            raise
    os.replace(tmp.name, path)


def initial_start(day):
    try:
        return day.replace(year=day.year - 5)
    except ValueError:
        return day.replace(year=day.year - 5, day=28)


def validate_config(registry):
    used = set()
    ids = set()
    for j in registry:
        if j["id"] in ids or not j["url"].startswith("https://"):
            raise ValueError("저널 ID 또는 URL 오류")
        ids.add(j["id"])
        codes = set(filter(None, (j["print_issn"], j["online_issn"])))
        if not codes or used.intersection(codes):
            raise ValueError("누락 또는 저널 간 중복 ISSN")
        for code in codes:
            s = code.replace("-", "")
            if (
                len(s) != 8
                or sum((10 if c == "X" else int(c)) * (8 - i) for i, c in enumerate(s))
                % 11
            ):
                raise ValueError("ISSN 검증 오류 " + code)
        used.update(codes)


def refresh(
    path=INDEX,
    *,
    day=None,
    registry=None,
    taxonomy=None,
    get=fetch,
    supplements=None,
    reviews=None,
    identities=None,
    package_table=None,
    size=None,
):
    day = day or datetime.now(ZoneInfo("Asia/Seoul")).date()
    registry = (
        registry
        if registry is not None
        else read(CATALOG / "journals.json")["journals"]
    )
    taxonomy = taxonomy or Methods(read(CATALOG / "methods.json"))
    validate_config(registry)
    reviews = read(CATALOG / "reviewed-papers.json", []) if reviews is None else reviews
    identities = (
        read(CATALOG / "journal-identities.json") if identities is None else identities
    )
    by_journal = defaultdict(list)
    for a in [*(supplements or []), *reviews]:
        by_journal[a["journal"]].append(a)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.parent):
        if path.exists():
            state = read_index(path)
        else:
            state = dict(
                schema=1,
                start=str(initial_start(day)),
                initial_end=str(day),
                as_of=str(day),
                articles={},
                coverage={},
                runs=[],
            )
        if day < date.fromisoformat(state["as_of"]):
            raise ValueError("기존 색인보다 이른 수집일")
        index = ArticleIndex(state["articles"])
        previous = set(index.rows)
        prior_counted = {k for k, a in index.rows.items() if counted(a)}
        changed = set()
        duplicates = 0
        start = date.fromisoformat(state["start"])
        for j in registry:
            jid = j["id"]
            prior = state["coverage"].get(jid, {})
            journal_stat = {}
            keys_by_year = defaultdict(lambda: defaultdict(set))

            def put(a, source):
                nonlocal duplicates
                if not publication(a):
                    raise ValueError("발행일 누락")
                if not in_window(a, start, day):
                    return
                outcome, key = index.put(a, str(day))
                if outcome == "ambiguous":
                    raise ValueError("서지 식별 충돌 또는 식별 정보 부족")
                if outcome == "updated" and key in previous:
                    changed.add(key)
                if outcome == "duplicate":
                    duplicates += 1
                if a.get("doi"):
                    keys_by_year[publication(a)[:4]][source].add(a["doi"])

            # Bibliographic snapshots and human reviews can be replayed after an interrupted initial run.
            for a in by_journal[jid]:
                put(dict(a), "보완")
            for source, convert in (("Crossref", record_cr), ("OpenAlex", record_oa)):

                def consume(raw):
                    article = convert(raw, j, taxonomy)
                    if article is not None:
                        put(article, source)

                journal_stat[source] = walk(j, start, day, source, consume, get, size)
            cr, oa = journal_stat["Crossref"], journal_stat["OpenAlex"]
            changes = index.link_reviewed(
                [
                    link
                    for link in identities
                    if index.rows.get(link["record"], {}).get("journal") == jid
                ]
            )
            changed.update(changes & previous)
            verified, lookup_errors = [], []
            journal_rows = [
                (k, a) for k, a in index.rows.items() if a["journal"] == jid
            ]
            # Supplementary records with no matching primary-list entry can fall on the other side of either date boundary.
            if cr["complete"]:
                for key, a in journal_rows:
                    if (
                        a.get("linked_to")
                        or not a.get("doi")
                        or "Crossref" in a.get("sources", [])
                    ):
                        continue
                    try:
                        raw = get("https://api.crossref.org/works/" + a["doi"])[
                            "message"
                        ]
                        detail = record_cr(raw, j, taxonomy)
                        if detail["doi"] != a["doi"] or not publication(detail):
                            raise ValueError("DOI·발행일 미확인")
                        detail["verified_publication"] = publication(detail)
                        outcome, _ = index.put(detail, str(day))
                        if outcome == "updated" and key in previous:
                            changed.add(key)
                        verified.append(a["doi"])
                    except (ValueError, KeyError, TypeError, OSError) as e:
                        lookup_errors.append({"doi": a["doi"], "error": str(e)[:140]})
            year_audit = (
                openalex_years(j, start, day, get) if not oa["complete"] else {}
            )
            totals = Counter()
            evidence = Counter()
            for key, a in journal_rows:
                if a.get("verified_publication"):
                    outside = not in_window(a, start, day)
                    if outside != bool(a.get("outside_window")):
                        a["outside_window"] = outside
                        if key in previous:
                            changed.add(key)
                if not counted(a):
                    continue
                subtype = notice_type(a.get("title"), a.get("source_type"))
                if subtype and (
                    a.get("kind") != "notice" or a.get("notice_type") != subtype
                ):
                    a["kind"] = "notice"
                    a["notice_type"] = subtype
                    if key in previous:
                        changed.add(key)
                y = publication(a)[:4]
                totals[y] += 1
                if a.get("has_abstract"):
                    evidence[y] += 1
            years = {}
            for y in map(str, range(start.year, day.year + 1)):
                cross = keys_by_year[y]["Crossref"]
                other = keys_by_year[y]["OpenAlex"]
                years[y] = dict(
                    total=totals[y],
                    abstracts=evidence[y],
                    crossref=len(cross),
                    openalex=len(other),
                    both=len(cross & other),
                    only_openalex=len(other - cross),
                    only_crossref=len(cross - other),
                    examples=sorted(other - cross)[:5],
                )
            supplemental = [
                a
                for a in index.rows.values()
                if a["journal"] == jid and a.get("toc_url") and counted(a)
            ]
            toc = Counter(
                (a.get("issue_date") or a.get("published", ""))[:4]
                for a in supplemental
            )
            status = (
                "success"
                if cr["complete"] and not cr["skipped"]
                else (
                    "partial"
                    if sum(totals.values()) or cr["received"] or oa["received"]
                    else "failed"
                )
            )
            state["coverage"][jid] = dict(
                status=status,
                sources=journal_stat,
                openalex_years=year_audit,
                doi_checks=dict(verified=verified, errors=lookup_errors),
                years=years,
                total=sum(totals.values()),
                last_success=str(day)
                if status == "success"
                else prior.get("last_success"),
                publisher_toc=dict(complete=False, counts=dict(toc)),
                checked=str(day),
            )
            print(
                f"{jid:6} {status:7} Crossref {cr['received']}/{cr['reported']} · OpenAlex {oa['received']}/{oa['reported']} · 누적 {sum(totals.values())}",
                flush=True,
            )
        packages = {}
        if package_table:
            try:
                data, digest = package_table()
                changes, matches = link_dataverse(
                    index, registry, taxonomy, data, str(day)
                )
                changed.update(changes & previous)
                packages = dict(
                    source=DATAVERSE_TABLE,
                    sha256=digest,
                    received=len(data),
                    matched=matches,
                )
            except (ValueError, OSError) as e:
                packages = dict(source=DATAVERSE_TABLE, error=str(e)[:140])
        for a in index.rows.values():
            pub = publication(a)
            a["date_precision"] = {4: "year", 7: "month", 10: "day"}.get(
                len(pub), "unknown"
            )
        active = {k for k, a in index.rows.items() if counted(a)}
        run = dict(
            date=str(day),
            added=len(active - prior_counted),
            updated=len(changed & active & prior_counted),
            duplicates=duplicates,
            total=len(active),
            linked=sum(bool(a.get("linked_to")) for a in index.rows.values()),
            outside=sum(bool(a.get("outside_window")) for a in index.rows.values()),
            package_audit=packages,
            failed=[j for j, c in state["coverage"].items() if c["status"] == "failed"],
            partial=[
                j for j, c in state["coverage"].items() if c["status"] == "partial"
            ],
            api_failed=[
                j
                for j, c in state["coverage"].items()
                if not any(
                    s["complete"] or s["received"] for s in c["sources"].values()
                )
            ],
            primary_incomplete=[
                j for j, c in state["coverage"].items() if c["status"] != "success"
            ],
        )
        if not previous.issubset(index.rows):
            raise ValueError("기존 식별자 손실")
        state["as_of"] = str(day)
        state["runs"].append(run)
        save_index(path, state)
    return run


def main():
    p = argparse.ArgumentParser(
        description="고정 시작일부터 저널별 Crossref 발행목록을 수집하고 OpenAlex와 대조합니다."
    )
    p.add_argument("--supplement", type=Path, help="출처가 있는 서지 보완 JSON")
    args = p.parse_args()
    run = refresh(
        supplements=read(args.supplement) if args.supplement else [],
        package_table=dataverse_table,
    )
    print(json.dumps(run, ensure_ascii=False))
    return 2 if run["primary_incomplete"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
