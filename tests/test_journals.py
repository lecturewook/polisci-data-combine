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
    methods_by_level,
    publication,
)
from polihub.journal_sources import record_cr, walk
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
        validate_config(self.journals)
        self.assertEqual(51, len(self.journals))
        self.assertEqual(
            [9, 3, 11, 10, 5, 3, 7, 3],
            [
                sum(j["field"] == f for j in self.journals)
                for f in dict.fromkeys(j["field"] for j in self.journals)
            ],
        )
        self.assertEqual(initial_start(date(2024, 2, 29)), date(2019, 2, 28))

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


if __name__ == "__main__":
    unittest.main()
