import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from polihub.journal_abstracts import alex_batch, openaire_batch, recover_abstracts
from polihub.journal_publisher import Publisher, publisher_abstract, publisher_url
from polihub.journal_records import ArticleIndex, Methods, abstract_text
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
