import json
import tempfile
import unittest
from types import SimpleNamespace
from datetime import date
from pathlib import Path
from unittest.mock import patch

from polihub.journal_abstracts import alex_batch, openaire_batch, recover_abstracts
from polihub.journal_publisher import Publisher, publisher_abstract, publisher_url
from polihub.journal_records import ArticleIndex, Methods, abstract_text, notice_type
from polihub.journal_repec import repec_batch
from polihub.journals import CATALOG, read_index, refresh


class AbstractTest(unittest.TestCase):
    def setUp(self):
        self.a = dict(
            journal="jop",
            doi="10.1000/paper",
            title="A comparison of political attitudes",
            published="2025",
            publisher_url="https://www.journals.uchicago.edu/doi/10.1000/paper",
        )
        self.text = "Drawing on a UK-based survey experiment, we examine how citizens respond to political choices."
        self.tax = Methods(json.loads((CATALOG / "methods.json").read_text()))

    def test_labelled_section_and_identity(self):
        head = "<meta name='citation_doi' content='10.1000/paper'>"
        markup = (
            "<section id='abstract'><h2>Abstract</h2><p>"
            + self.text
            + "</p></section><p>We also use OLS in the reference list.</p>"
        )
        self.assertEqual(publisher_abstract(head + markup, self.a)[0], self.text)
        self.assertFalse(
            publisher_abstract(head.replace("/paper", "/other") + markup, self.a)[0]
        )
        self.assertFalse(
            publisher_abstract(
                "<meta name='description' content='" + self.text + "'>" + head, self.a
            )[0]
        )
        self.assertFalse(
            publisher_abstract("<h1>Other article</h1>" + markup, self.a)[0]
        )
        self.assertEqual(
            publisher_abstract("<h1>" + self.a["title"] + "</h1>" + markup, self.a)[0],
            self.text,
        )
        self.assertFalse(
            abstract_text(
                "No abstract available for this article. The full text can be accessed through your library."
            )
        )
        self.assertFalse(abstract_text(self.text + "…"))

    def test_explicit_publisher_statement_and_paratext(self):
        head = "<meta name='citation_doi' content='10.1000/paper'>"
        page = (
            "<p>An abstract is not available for this content so a preview has been provided.</p>"
            "<meta name='citation_abstract' content='" + self.text + "'>"
        )
        self.assertEqual(
            publisher_abstract(head + page, self.a),
            ("", "출판사가 초록 미제공으로 표시"),
        )
        self.assertEqual(
            publisher_abstract(head.replace("/paper", "/incorrect") + page, self.a)[1],
            "논문 식별 불일치",
        )
        for title in (
            "CQY volume 264 Cover and Front matter",
            "PSR volume 116 issue 2 Cover and Back matter",
            "Acknowledgements",
            "Editors’ Note",
        ):
            self.assertEqual(notice_type(title, "journal-article"), "편집자료")
        for title in (
            "Acknowledgements in political science",
            "Ruling Divisions: The Politics of Brexit",
        ):
            self.assertFalse(notice_type(title, "journal-article"))

        a = dict(self.a)

        class PublisherWithStatement:
            def __init__(self, host):
                pass

            def read(self, url, article):
                return "", url, "출판사가 초록 미제공으로 표시"

        found, _ = recover_abstracts(
            ArticleIndex({a["doi"]: a}),
            self.tax,
            "2026-11-19",
            providers=[
                (
                    "색인",
                    50,
                    lambda rows: {
                        a["doi"]: (
                            a["title"],
                            self.text,
                            "https://example.org/paper",
                            "abstract",
                        )
                    },
                )
            ],
            publisher_factory=PublisherWithStatement,
        )
        self.assertFalse(found)
        self.assertNotIn("has_abstract", a)
        self.assertEqual(a["publisher_statement"]["url"], a["publisher_url"])
        self.assertIn("대조 필요", a["abstract_check"]["sources"]["색인"])

    def test_robots_and_access_denial(self):
        p = Publisher("www.journals.uchicago.edu")
        url = publisher_url(self.a)
        with patch.object(
            p,
            "request",
            return_value=("User-agent: *\nDisallow: /doi/\n", "text/plain"),
        ) as req:
            self.assertIn("robots", p.read(url, self.a)[2])
            self.assertEqual(req.call_count, 1)
        from urllib.error import HTTPError

        p = Publisher("www.journals.uchicago.edu")
        with patch.object(
            p,
            "request",
            side_effect=[
                ("", "text/plain"),
                HTTPError(url, 403, "Forbidden", {}, None),
            ],
        ) as req:
            self.assertFalse(p.read(url, self.a)[0])
            self.assertIn("보류", p.read(url, self.a)[2])
            self.assertEqual(req.call_count, 2)

    def test_cambridge_preview_rechecked_and_old_evidence_retained(self):
        url = "https://www.cambridge.org/core/journals/example/article/abc"
        a = {**self.a, "publisher_url": url}
        head = (
            "<meta name='citation_doi' content='10.1000/paper'><meta name='citation_abstract' content='"
            + self.text
            + "'>"
        )
        self.assertFalse(publisher_abstract(head, a)[0])
        facts = {}
        no = (
            head
            + "<dl><dt>Type</dt><dd>Book Reviews</dd></dl><h2>Abstract</h2><p>An abstract is not available for this content.</p>"
        )
        self.assertEqual(
            publisher_abstract(no, a, facts), ("", "출판사가 초록 미제공으로 표시")
        )
        self.assertEqual(facts["notice_type"], "서평")
        self.assertEqual(
            publisher_abstract(head + "<h2>Abstract</h2><p>" + self.text + "</p>", a)[
                0
            ],
            self.text,
        )
        a.update(
            has_abstract=True,
            abstract_proof={
                "source": "공식 저널",
                "location": "citation_abstract",
                "url": url,
            },
            methods=[
                {"method": "ols", "level": "direct", "url": url},
                {
                    "method": "did",
                    "level": "clue",
                    "url": url,
                    "location": "공식 저널 · 초록",
                },
            ],
        )
        with patch.object(
            Publisher, "request", side_effect=[("", "text/plain"), (no, "text/html")]
        ):
            _, audit = recover_abstracts(
                ArticleIndex({a["doi"]: a}), self.tax, "2026-11-19", providers=[]
            )
        self.assertEqual(audit["revalidated"], 1)
        self.assertFalse(a["has_abstract"])
        self.assertEqual(a["superseded_abstract"]["methods"][0]["method"], "did")
        self.assertEqual(a["methods"][0]["level"], "direct")
        self.assertEqual(a["kind"], "notice")

    def test_repec_matches_publisher_series_and_identifier(self):
        def mirror(args, **kwargs):
            archive = Path(args[-1])
            (archive / "articles.redif").write_text(
                "Template-Type: ReDIF-Article 1.0\nTitle: Example One\nDOI: 10.1000/paper\nAbstract: "
                + self.text
                + "\nHandle: RePEc:eee:pubeco:example\nTemplate-Type: ReDIF-Article 1.0\nTitle: Example Two\nAbstract: "
                + self.text
                + "\nHandle: RePEc:eee:pubeco:v:200:y:2021:i:c:s0047272721000200\n"
            )
            return SimpleNamespace(returncode=0)

        second = {
            **self.a,
            "doi": "10.1000/other",
            "publisher_url": "https://linkinghub.elsevier.com/retrieve/pii/S0047272721000200",
        }
        with (
            patch("polihub.journal_repec.shutil.which", return_value="/usr/bin/rsync"),
            patch("polihub.journal_repec.subprocess.run", side_effect=mirror),
        ):
            match = repec_batch("jpe", [self.a, second])
            self.assertEqual(set(match), {self.a["doi"], second["doi"]})
            self.assertEqual(match[second["doi"]][1], self.text)

    def test_batch_validated_and_secondary_sources(self):
        data = {
            "results": [
                {
                    "id": "https://openalex.org/W1",
                    "doi": "https://doi.org/10.1000/paper",
                    "title": self.a["title"],
                    "abstract_inverted_index": {"We": [0], "use": [1], "OLS": [2]},
                }
            ]
        }
        with patch("polihub.journal_abstracts.fetch", return_value=data) as call:
            self.assertEqual(alex_batch([self.a])[self.a["doi"]][1], "We use OLS")
            self.assertIn("filter=doi%3A", call.call_args.args[0])
        entry = {
            "id": "doi_dedup___::abc",
            "mainTitle": self.a["title"],
            "pids": [{"scheme": "doi", "value": self.a["doi"]}],
            "descriptions": [self.text],
        }
        with patch(
            "polihub.journal_abstracts.fetch", return_value={"results": [entry]}
        ):
            self.assertEqual(openaire_batch([self.a])[self.a["doi"]][1], self.text)

    def test_publisher_reuses_same_day_denial_but_retries_on_request_or_new_day(self):
        clients = []
        queries = []

        def lookup(rows):
            queries.append([a["doi"] for a in rows])
            return {}

        class NotAvailable:
            def __init__(self, host):
                clients.append(host)

            def read(self, url, article):
                return "", url, "출판사 HTTP 403"

        index = ArticleIndex({self.a["doi"]: self.a})
        args = dict(
            providers=[("학술 색인", 50, lookup)], publisher_factory=NotAvailable
        )
        recover_abstracts(index, self.tax, "2026-11-19", **args)
        _, audit = recover_abstracts(index, self.tax, "2026-11-19", **args)
        self.assertEqual(audit["providers"]["공식 저널"]["reused_same_day"], 1)
        self.assertEqual(len(clients), 1)
        self.assertEqual(audit["providers"]["학술 색인"]["reused_same_day"], 1)
        self.assertEqual(len(queries), 1)
        recover_abstracts(index, self.tax, "2026-11-19", recheck_abstracts=True, **args)
        recover_abstracts(index, self.tax, "2026-11-20", **args)
        self.assertEqual(len(clients), 3)
        self.assertEqual(len(queries), 3)

    def test_recover_preserves_previous_records_and_ranks_new_evidence(self):
        a = self.a
        known = {
            **a,
            "doi": "10.1000/known",
            "has_abstract": True,
            "methods": [{"method": "ols", "level": "direct"}],
        }
        other = {**a, "doi": "10.1000/other"}
        notice = {**a, "doi": "10.1000/notice", "kind": "notice"}
        rows = {r["doi"]: r for r in [a, known, other, notice]}
        index = ArticleIndex(rows)

        class NoAccess:
            def __init__(self, host):
                pass

            def read(self, url, a):
                return "", url, "출판사 HTTP 403"

        def lookup(batch):
            return {
                a["doi"]: (
                    a["title"],
                    self.text,
                    "https://openalex.org/W1",
                    "abstract",
                ),
                other["doi"]: (
                    "Different paper entirely",
                    self.text,
                    "https://openalex.org/W2",
                    "abstract",
                ),
            }

        def recover():
            return recover_abstracts(
                index,
                self.tax,
                "2026-11-19",
                providers=[("OpenAlex", 50, lookup)],
                publisher_factory=NoAccess,
                pause=lambda _: None,
            )

        found, audit = recover()
        self.assertEqual(found, {a["doi"]})
        self.assertEqual(audit["targeted"], 2)
        self.assertEqual(a["abstract_proof"]["source"], "OpenAlex")
        self.assertEqual(a["methods"][0]["level"], "stated")
        self.assertIn("대조 보류", other["abstract_check"]["sources"]["OpenAlex"])
        self.assertFalse(recover()[0])
        self.assertEqual(known["methods"], [{"method": "ols", "level": "direct"}])
        self.assertNotIn("abstract_check", notice)
        self.assertNotIn(self.text, json.dumps(rows, ensure_ascii=False))
        ev = self.tax.classify(
            "",
            "Using a survey experiment, earlier authors reported a null effect.",
            "https://example.org",
        )
        self.assertEqual(ev[0]["level"], "clue")

    def test_coverage_after_recovery_and_repeat(self):
        j = next(
            j
            for j in json.loads((CATALOG / "journals.json").read_text())["journals"]
            if j["id"] == "jop"
        )

        def get(url):
            if "openalex" in url:
                return {"meta": {"count": 0}, "results": []}
            return {
                "message": {
                    "total-results": 1,
                    "items": [
                        {
                            "DOI": self.a["doi"],
                            "title": [self.a["title"]],
                            "ISSN": [j["print_issn"]],
                            "published": {"date-parts": [[2025, 1]]},
                        }
                    ],
                }
            }

        class Official:
            def __init__(self, host):
                pass

            def read(inner, url, a):
                return self.text, url, "공식 페이지 Abstract"

        def recover(index, tax, day):
            return recover_abstracts(
                index, tax, day, providers=[], publisher_factory=Official
            )

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "index.gz"
            args = dict(
                day=date(2026, 11, 19),
                registry=[j],
                get=get,
                taxonomy=self.tax,
                identities=[],
                reviews=[],
                supplements=[dict(self.a)],
                recover=recover,
            )
            first = refresh(path, **args)
            second = refresh(path, **args)
            self.assertEqual(
                (
                    first["abstract_audit"]["gained"],
                    second["abstract_audit"]["gained"],
                    second["total"],
                ),
                (1, 0, 1),
            )
            year = read_index(path)["coverage"]["jop"]["years"]["2025"]
            self.assertEqual(
                (year["abstracts"], year["eligible_abstracts"], year["eligible"]),
                (1, 1, 1),
            )
            third = refresh(
                path,
                **{
                    **args,
                    "reviews": [{**self.a, "kind": "notice", "notice_type": "서평"}],
                },
            )
            audit = third["abstract_audit"]
            self.assertEqual(third["total"], 1)
            self.assertEqual(
                (audit["before"], audit["before_same_cohort"], audit["after"]),
                (1, 0, 0),
            )
            self.assertEqual((audit["gained"], audit["reclassified"]), (0, 1))
