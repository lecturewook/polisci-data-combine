import copy
import io
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from polihub.journal_records import (
    ArticleIndex,
    Methods,
    doi,
    counted,
    notice_type,
    methods_by_level,
    publication,
)
from polihub.journal_sources import link_dataverse, openalex_years, record_cr, walk
from polihub.journals import (
    CATALOG,
    initial_start,
    read_index,
    refresh,
    validate_config,
)


class JournalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "index.gz"
        self.journals = json.loads((CATALOG / "journals.json").read_text())["journals"]
        self.j = self.journals[0]
        self.tax = Methods(json.loads((CATALOG / "methods.json").read_text()))
        self.items = [self.item(1), self.item(2), self.item(3)]

    def item(self, n):
        return {
            "DOI": "10.1234/test." + str(n),
            "title": ["A sufficiently long publication title " + str(n)],
            "ISSN": [self.j["print_issn"]],
            "type": "journal-article",
            "published": {"date-parts": [[2024, 2, n]]},
        }

    def get(self, url):
        if "openalex" in url:
            return {"meta": {"count": 0, "next_cursor": None}, "results": []}
        q = parse_qs(urlsplit(url).query)
        start = 0 if q["cursor"] == ["*"] else int(q["cursor"][0])
        page = self.items[start : start + 2]
        return {
            "message": {
                "total-results": len(self.items),
                "items": page,
                "next-cursor": str(start + len(page)),
            }
        }

    def run_day(self, day=date(2026, 11, 19), get=None, registry=None):
        with redirect_stdout(io.StringIO()):
            return refresh(
                self.path,
                day=day,
                registry=registry or [self.j],
                taxonomy=self.tax,
                get=get or self.get,
                reviews=[],
                size=2,
            )

    def test_registry(self):
        groups = json.loads((CATALOG / "journals.json").read_text())["interest_groups"]
        validate_config(self.journals, groups)
        initial = [j for j in self.journals if not j.get("introduced")]
        self.assertEqual(51, len(initial))
        self.assertEqual(
            [9, 3, 11, 10, 5, 3, 7, 3],
            [
                sum(j["field"] == f for j in initial)
                for f in dict.fromkeys(j["field"] for j in initial)
            ],
        )
        new = {j["id"] for j in self.journals if j.get("introduced") == 7}
        self.assertEqual(18, len(new))
        self.assertTrue(new <= {jid for g in groups for jid in g["journals"]})
        self.assertTrue(
            all(j["selection"]["source"] for j in self.journals if j["id"] in new)
        )
        with self.assertRaises(ValueError):
            validate_config(
                self.journals, [dict(id="broken", label="오류", journals=["typo"])]
            )
        self.assertEqual(initial_start(date(2024, 2, 29)), date(2019, 2, 28))

    def test_journal_added_after_first_run(self):
        self.run_day()
        original = read_index(self.path)
        new_journal = next(j for j in self.journals if j["id"] == "demog")
        item = self.item(4)
        item.update(
            DOI="10.1234/population",
            ISSN=[new_journal["online_issn"]],
            published={"date-parts": [[2022, 1, 15]]},
        )

        def expanded(url):
            args = parse_qs(urlsplit(url).query)
            if (
                "crossref" in url
                and new_journal["print_issn"] in args.get("filter", [""])[0]
            ):
                self.assertIn("from-pub-date:2021-11-19", args["filter"][0])
                return {"message": {"total-results": 1, "items": [item]}}
            return self.get(url)

        for day in (20, 21):
            self.run_day(date(2026, 11, day), expanded, [self.j, new_journal])
            state = read_index(self.path)
            self.assertEqual(original["start"], state["start"])
            self.assertEqual(len(original["articles"]) + 1, len(state["articles"]))
            self.assertTrue(original["articles"].keys() <= state["articles"].keys())
            self.assertEqual(
                "2026-11-20", state["articles"]["10.1234/population"]["first_seen"]
            )

    def test_kci_results_and_refresh_failure(self):
        from polihub.journal_kci import annual_counts, compare_kci

        html = "<ul><li>한국인구학 (490)</li><li><input> 2025 (21)</li><li>2024 (18)</li></ul>"
        self.assertEqual({"2025": 21, "2024": 18}, annual_counts(html, "한국인구학"))
        with self.assertRaises(ValueError):
            annual_counts(html, "한국정치학회보")
        from unittest.mock import Mock

        host = Mock(stop="robots 정책에 따라 보류")
        spec = next(j["kci"] for j in self.journals if j["id"] == "kjps")
        audit = compare_kci(spec, date(2026, 12, 1), host=host)
        self.assertEqual(spec["snapshot"]["counts"], audit["counts"])
        self.assertEqual(spec["snapshot"]["checked"], audit["checked"])
        self.assertIn("robots", audit["error"])
        host.request.assert_not_called()

    def test_cursor_last_page_and_exact_multiple(self):
        for n in (3, 4):
            self.items = [self.item(i) for i in range(1, n + 1)]
            seen = []
            s = walk(
                self.j,
                date(2021, 11, 19),
                date(2026, 11, 19),
                "Crossref",
                seen.append,
                self.get,
                2,
            )
            self.assertTrue(s["complete"])
            self.assertEqual(n // 2 + 1, s["pages"])
            self.assertEqual(n, len(seen))

    def test_repeating_cursor_changing_pages_and_loop(self):
        pages = iter([[self.item(1)], [self.item(2)], []])

        def get(url):
            return {
                "message": {
                    "total-results": 2,
                    "items": next(pages),
                    "next-cursor": "constant",
                }
            }

        self.assertTrue(
            walk(
                self.j, date.today(), date.today(), "Crossref", lambda a: None, get, 1
            )["complete"]
        )

        def repeated(url):
            return {
                "message": {
                    "total-results": 3,
                    "items": [self.item(1)],
                    "next-cursor": "constant",
                }
            }

        stat = walk(
            self.j, date.today(), date.today(), "Crossref", lambda a: None, repeated, 1
        )
        self.assertFalse(stat["complete"])
        self.assertIn("동일 페이지 반복", stat["errors"])

    def test_repeat_normalized_doi_and_late_deposit(self):
        self.items.append({**self.items[0], "DOI": "HTTPS://DOI.ORG/10.1234/TEST.1"})
        self.assertEqual(self.run_day()["added"], 3)
        self.assertEqual(self.run_day()["added"], 0)
        self.items.append({**self.item(4), "published": {"date-parts": [[2022, 3, 5]]}})
        self.items[0]["author"] = [{"family": "Kim", "given": "J."}]
        r = self.run_day(date(2026, 12, 1))
        self.assertEqual((1, 1, 4), (r["added"], r["updated"], r["total"]))
        s = read_index(self.path)
        self.assertEqual(s["start"], "2021-11-19")
        self.assertEqual(s["articles"]["10.1234/test.4"]["first_seen"], "2026-12-01")

    def test_source_errors_preserve_records_and_other_journals(self):
        self.run_day()

        def get(url):
            if "0003-0554" in url or "openalex" in url:
                raise ValueError("HTTP 403")
            a = {**self.item(4), "ISSN": [self.journals[1]["print_issn"]]}
            return {"message": {"items": [a], "total-results": 1}}

        r = self.run_day(get=get, registry=self.journals[:2])
        self.assertEqual(r["total"], 4)
        s = read_index(self.path)
        self.assertIn(
            "HTTP 403", s["coverage"]["apsr"]["sources"]["Crossref"]["errors"]
        )
        self.assertEqual(s["coverage"]["apsr"]["last_success"], "2026-11-19")
        self.assertEqual(s["coverage"]["ajps"]["status"], "success")

        def broken(url):
            raise ValueError("HTTP 429")

        before = copy.deepcopy(s["articles"])
        self.run_day(get=broken, registry=self.journals[:2])
        self.assertEqual(read_index(self.path)["articles"], before)

    def test_interruption_and_concurrent_runs(self):
        self.run_day()
        before = self.path.read_bytes()

        def interrupt(url):
            raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            self.run_day(get=interrupt)
        self.assertEqual(before, self.path.read_bytes())
        with ThreadPoolExecutor(2) as ex:
            runs = list(
                ex.map(
                    lambda _: refresh(
                        self.path,
                        day=date(2026, 11, 19),
                        registry=[self.j],
                        taxonomy=self.tax,
                        get=self.get,
                        reviews=[],
                        size=2,
                    ),
                    [1, 2],
                )
            )
        self.assertEqual([0, 0], [s["added"] for s in runs])
        self.assertEqual(3, len(read_index(self.path)["runs"]))

    def test_bibliographic_identity_and_direct_evidence(self):
        idx = ArticleIndex({})
        a = dict(
            journal="apsr",
            title="An article about political parties",
            authors=["Kim"],
            published="2024",
            volume="1",
            issue="2",
            methods=[dict(method="ols", level="direct", role="주 추정")],
            resources=[dict(kind="재현 패키지", url="https://example.org/1")],
        )
        idx.put(copy.deepcopy(a), "2026-11-19")
        idx.put(
            {
                **a,
                "doi": "10.1234/x",
                "published": "2024-02-01",
                "resources": [],
                "methods": [],
            },
            "2026-12-01",
        )
        row = next(iter(idx.rows.values()))
        self.assertEqual(1, len(idx.rows))
        self.assertEqual(row["first_seen"], "2026-11-19")
        self.assertEqual(row["methods"], a["methods"])
        self.assertEqual(row["resources"], a["resources"])
        idx.put({**a, "doi": "10.1234/y", "issue": "3"}, "2026-12-01")
        self.assertEqual(2, len(idx.rows))
        self.assertEqual(doi("doi:10.1234/ABC"), "10.1234/abc")

    def test_method_evidence_and_hierarchy(self):
        text = "We use adaptive LASSO. We use pairwise fused lasso. Prior work used OLS. We did not use a random forest."
        ev = self.tax.classify("Conjoint in prior work", text, "https://example.org")
        ranks = methods_by_level({"methods": ev}, self.tax)
        self.assertEqual(ranks["penalty"], 2)
        self.assertEqual(sum(k == "penalty" for k in ranks), 1)
        self.assertEqual(ranks["ols"], 1)
        self.assertEqual(ranks["rf"], 1)
        self.assertEqual(ranks["conjoint"], 1)
        self.assertFalse(methods_by_level({"kind": "notice", "methods": ev}, self.tax))

    def test_dates_exclude_repository_deposit_and_subjects(self):
        a = record_cr(
            {
                **self.item(1),
                "published-online": {"date-parts": [[2023, 12]]},
                "created": {"date-parts": [[2021, 1, 1]]},
                "subject": ["Computer science"],
                "relation": {"is-supplemented-by": [{"id": "10.9999/resources"}]},
            },
            self.j,
            self.tax,
        )
        self.assertEqual(publication(a), "2023-12")
        self.assertEqual(a["keywords"], [])
        self.assertEqual(a["resources"][0]["url"], "https://doi.org/10.9999/resources")

    def test_verified_alias_retains_original_and_counts_one(self):
        idx = ArticleIndex({})
        a = dict(
            journal="apsr", doi="10.1234/x", title="Published version", published="2022"
        )
        idx.put(a, "2026-11-20")
        idx.put({**a, "doi": "10.2139/ssrn.123456", "published": "2019"}, "2026-11-19")
        links = [
            dict(
                record="10.2139/ssrn.123456",
                doi="10.1234/x",
                relation="사전공개본",
                evidence="https://doi.org/10.2139/ssrn.123456",
            )
        ]
        self.assertEqual(2, len(idx.link_reviewed(links)))
        self.assertFalse(idx.link_reviewed(links))
        self.assertEqual(2, len(idx.rows))
        self.assertEqual(1, sum(counted(v) for v in idx.rows.values()))
        self.assertEqual("2022", publication(a))
        self.assertEqual("2026-11-19", a["first_seen"])
        self.assertEqual("사전공개본", a["resources"][0]["kind"])

    def test_supplement_outside_window_becomes_eligible_later(self):
        d = "10.1234/future"
        record = dict(journal="apsr", doi=d, published="2026", sources=["권호 서지"])

        def get(url):
            if url.endswith("/works/" + d):
                return {
                    "message": {
                        **self.item(1),
                        "DOI": d,
                        "published": {"date-parts": [[2026, 12, 1]]},
                    }
                }
            return self.get(url)

        with redirect_stdout(io.StringIO()):
            res = refresh(
                self.path,
                day=date(2026, 11, 19),
                registry=[self.j],
                get=get,
                reviews=[],
                supplements=[record],
                size=2,
            )
        self.assertEqual((3, 1), (res["total"], res["outside"]))
        self.assertIn(d, read_index(self.path)["articles"])
        res = self.run_day(date(2026, 12, 1), get=get)
        self.assertEqual((4, 1, 0), (res["total"], res["added"], res["outside"]))

    def test_year_audit_and_notices(self):
        counts = openalex_years(
            self.j,
            date(2021, 11, 19),
            date(2026, 11, 19),
            lambda u: {"meta": {"count": 3}, "group_by": [{"key": "2025", "count": 3}]},
        )
        self.assertEqual(counts["counts"], {"2025": 3})
        self.assertEqual(
            notice_type("A Democracy. By J. Smith. Cambridge, 2021. 210p."), "서평"
        )
        self.assertFalse(notice_type("A systematic review of democracy and conflict"))
        self.assertEqual(notice_type("Democracy – ADDENDUM"), "정정·추가·철회")
        self.assertEqual(
            doi("https://doi.org/10.1234/X?utm_source=journal#page"), "10.1234/x"
        )

    def test_dataverse_only_links_known_papers(self):
        idx = ArticleIndex({})
        known = dict(
            journal="apsr",
            doi="10.1234/paper",
            title="A publication about voting",
            authors=["Min Kim"],
        )
        idx.put(known, "2026-11-19")
        record = dict(
            journal=self.j["name"],
            doi="10.7910/DVN/SAMPLE",
            title="Replication Data for: A publication about voting",
            related_publication_citation="",
            authors="Kim, Min",
            description="We conduct a survey experiment.",
            published_at="2025-08-03",
        )
        rows = [record, {**record, "title": "An unrelated work"}]
        changes, n = link_dataverse(idx, [self.j], self.tax, rows, "2026-11-20")
        self.assertEqual((1, 1), (len(changes), n))
        self.assertEqual(1, len(idx.rows))
        self.assertEqual("2025-08-03", known["resources"][0]["registered"])
        self.assertFalse(publication(known))
        self.assertFalse(link_dataverse(idx, [self.j], self.tax, rows, "2026-12-01")[0])


if __name__ == "__main__":
    unittest.main()
