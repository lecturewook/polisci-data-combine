"""Contract tests use a synthetic catalogue entry isolated from real study data."""

import csv
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pyreadstat

from polihub.__main__ import register_file, verify
from polihub.acquisition import acquire
from polihub.core import CAT, digest, study, write
from polihub.engines import executable
from polihub.formats import load_frame, unpack
from polihub.research import workflow
from polihub.validation import validate


def fixture_catalog(i):
    return (
        {
            **CAT["datasets"]["kgss"],
            "id": i,
            "name": "기능 시험용 가상 표",
            "default_release": i + "-v1",
        },
        {
            **CAT["releases"]["kgss-release"],
            "id": i + "-v1",
            "dataset": i,
            "version": "QA-1",
            "keys": ["id"],
            "countries": {"KR": [2020, 2020]},
            "years": [2020, 2020],
            "time_key": "year",
        },
    )


class StudyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.id = "qa-table"
        d, r = fixture_catalog(self.id)
        for k, records in [("datasets", {self.id: d}), ("releases", {r["id"]: r})]:
            p = patch.dict(CAT[k], records)
            p.start()
            self.addCleanup(p.stop)
        self.s = study([self.id])
        path = self.root / "fixture.csv"
        path.write_text(
            "id,x,y,year\n01,0,2,2020\n02,1,6,2020\n03,2,7,2020\n04,3,11,2020\n05,4,14,2020\n06,5,-99,2020\n"
        )
        self.s["variable_definitions"] = [
            {
                "dataset": self.id,
                "name": k,
                "concept": k,
                "type": "numeric",
                "evidence": "fixture dictionary",
                "location": "test_workflow.py / fixture.csv",
                "missing_codes": [-99] if k == "y" else [],
            }
            for k in ("x", "y", "year")
        ]
        self.spec = self.root / "study.json"
        write(self.spec, self.s)
        register_file(self.root, self.s, self.spec, self.id, path, "QA-1")

    def plan(self):
        self.s["question"] = "합성 시험표의 사전 지정 평균과 기울기가 계산되는가?"
        m = {
            "id": "avg",
            "dataset": self.id,
            "method": "mean",
            "outcome": "y",
            "filters": [],
            "weights": "none",
            "se": "classical",
            "sample_rule": "유효한 y",
            "coding": "-99 제외",
            "timing": "단일 시험 시점",
            "missing": "complete_case",
            "estimand": "산술평균",
            "unit": "점",
            "assumptions": "독립적인 합성 관측치",
            "design_basis": "인공 QA, 모집단 추론 없음",
        }
        self.s["analyses"] = [m]
        self.s["sensitivity"] = [
            {
                **m,
                "id": "slope",
                "method": "ols",
                "predictors": ["x"],
                "estimand": "OLS 기울기",
                "base_model": "avg",
                "rationale": "동일 표본으로 회귀 코드 경로를 점검하는 QA",
            }
        ]
        ref = {
            "source": "합성 산술 문제",
            "table": "QA",
            "model": "avg",
            "statistic": "mean",
            "original": 8,
            "tolerance": 1e-10,
            "tolerance_basis": "고정된 유리수 평균",
            "unit": "점",
            "expected_n": 5,
            "data_version": "QA-1",
            "sample": "유효한 y",
            "coding": "-99 제외",
            "weights": "none",
            "estimator": "mean",
            "se": "classical",
            "rounding": "무반올림",
            "original_code": "시험 생성기",
        }
        self.s["references"] = [ref, {**ref, "table": "QA 음성 대조", "original": 9}]
        write(self.spec, self.s)

    def test_cached_file_integrity_and_version(self):
        self.assertEqual(acquire(self.root, self.s)[self.id]["via"], "study_reuse")
        record = json.loads((self.root / "acquisition_manifest.json").read_text())[
            "items"
        ][self.id]
        (self.root / record["raw"]).write_text("changed")
        self.assertEqual(
            validate(self.root, self.s)["datasets"][self.id]["status"], "blocked"
        )

    def test_missing_country_and_unknown_variable(self):
        self.s["datasets"][0]["countries"] = ["ZZ"]
        self.assertEqual(
            validate(self.root, self.s)["datasets"][self.id]["status"], "blocked"
        )
        self.s["datasets"][0]["countries"] = []
        self.plan()
        self.s["analyses"][0]["outcome"] = "absent"
        self.s["sensitivity"] = []
        state = workflow(self.root, self.s, "r", True, False)
        self.assertIn("변수사전", state["models"][0]["reason"])

    def test_incomplete_comparison_and_individual_join(self):
        self.plan()
        self.s["sensitivity"] = []
        self.s["analyses"][0]["comparison"] = True
        state = workflow(self.root, self.s, "r", True, False)
        self.assertIn("국가코드", state["models"][0]["reason"])
        self.s["datasets"].append(study(["jgss"])["datasets"][0])
        self.s["join_checks"] = [
            {
                "left": self.id,
                "right": "jgss",
                "keys": ["id"],
                "cardinality": "one_to_one",
                "evidence": "QA",
                "key_definition": "local person",
            }
        ]
        self.assertIn(
            "개인·가구", validate(self.root, self.s)["join_checks"][0]["reason"]
        )

    def test_repeated_keys_and_missing_weights(self):
        i = self.s["datasets"][0]
        i["design"] = {"evidence": "fixture", "keys": ["year"], "weight_key": "absent"}
        self.assertEqual(
            validate(self.root, self.s)["datasets"][self.id]["status"], "blocked"
        )

    def test_many_to_many_and_match_counts(self):
        d, r = fixture_catalog("qa-other")
        with (
            patch.dict(
                CAT["datasets"],
                {
                    "qa-other": {**d, "unit": "기관"},
                    self.id: {**CAT["datasets"][self.id], "unit": "기관"},
                },
            ),
            patch.dict(CAT["releases"], {r["id"]: r}),
        ):
            self.s["datasets"].append(study(["qa-other"])["datasets"][0])
            register_file(
                self.root,
                self.s,
                self.spec,
                "qa-other",
                self.root / "fixture.csv",
                "QA-1",
            )
            plan = {
                "left": self.id,
                "right": "qa-other",
                "keys": ["id"],
                "key_definition": "same public institution",
                "evidence": "fixture dictionary",
                "cardinality": "one_to_one",
                "max_unmatched": 0,
            }
            self.s["join_checks"] = [plan]
            row = validate(self.root, self.s)["join_checks"][0]
            self.assertEqual(
                (
                    row["left_rows"],
                    row["right_rows"],
                    row["outer_rows"],
                    row["left_unmatched_rate"],
                ),
                (6, 6, 6, 0),
            )
            plan.update(keys=["year"], cardinality="many_to_one")
            self.assertEqual(
                validate(self.root, self.s)["join_checks"][0]["status"], "blocked"
            )

    @unittest.skipUnless(executable("r"), "Rscript unavailable")
    def test_reviewed_script_is_checked_and_cli_only(self):
        self.plan()
        self.s["sensitivity"] = []
        source = self.root / "scripts" / "author.R"
        source.write_text(
            'd <- readRDS("data/processed/qa-table/import.rds")\nrows <- read.csv("scripts/sample-avg.csv")$row\ny <- d$y[rows]; y <- y[y != -99]\nwrite.csv(data.frame(statistic="mean",estimate=mean(y),std_error=sd(y)/sqrt(length(y)),n=length(y)),"outputs/avg-estimates.csv",row.names=FALSE)\n'
        )
        model = self.s["analyses"][0]
        model.update(
            method="script",
            method_detail="mean",
            required_variables=["y"],
            statistics=["mean"],
            script="scripts/author.R",
            script_sha256=digest(source),
        )
        blocked = workflow(self.root, self.s, "r", True)
        self.assertEqual(blocked["models"][0]["status"], "blocked")
        state = workflow(self.root, self.s, "r", True, local_scripts=True)
        self.assertEqual(state["models"][0]["status"], "passed")
        self.assertEqual(state["reproduction"], "부분 재현")
        model["script_sha256"] = "0" * 64
        state = workflow(self.root, self.s, "r", True, local_scripts=True)
        self.assertEqual(state["models"][0]["status"], "blocked")

    def test_zip_and_html(self):
        a = self.root / "x.zip"
        with zipfile.ZipFile(a, "w") as z:
            z.writestr("nested/k.csv", "id,x\n01,2\n")
            z.writestr("extra.csv", "a,b\n1,2\n")
        with self.assertRaises(ValueError):
            unpack(a, self.root / "out")
        tab = unpack(a, self.root / "out", "nested/k.csv")
        self.assertEqual(load_frame(tab)[0].iloc[0, 0], "01")
        with zipfile.ZipFile(a, "w") as z:
            z.writestr("../escape.csv", "a,b\n1,2\n")
        with self.assertRaises(ValueError):
            unpack(a, self.root / "out")
        tab.write_text("<!doctype html><title>login</title>")
        with self.assertRaises(ValueError):
            load_frame(tab)

    @unittest.skipUnless(executable("r"), "Rscript unavailable")
    def test_run_and_verify_preregistered_results(self):
        self.plan()
        state = workflow(self.root, self.s, "r", True)
        self.assertEqual([m["status"] for m in state["models"]], ["passed", "passed"])
        self.assertEqual(state["reproduction"], "부분 재현")
        self.assertEqual(verify(self.root, self.s)["decision"], "부분 재현")
        with (self.root / "outputs/results.csv").open() as f:
            rows = list(csv.DictReader(f))
        self.assertAlmostEqual(
            float(next(x for x in rows if x["statistic"] == "x")["estimate"]), 2.9
        )
        self.assertTrue((self.root / "outputs/figures/avg.png").is_file())
        self.s["references"][0]["tolerance"] = 1
        with self.assertRaises(ValueError):
            verify(self.root, self.s)

    @unittest.skipUnless(executable("r"), "Rscript unavailable")
    def test_native_labels_and_missing_values(self):
        frame = pd.DataFrame(
            {
                "id": ["01", "02", "03"],
                "x": [1.0, 2.0, 99.0],
                "y": [2.0, 4.0, 6.0],
                "year": [2020.0, 2020.0, 2020.0],
            }
        )
        self.s["variable_definitions"] = []
        for ext in ("dta", "sav"):
            with self.subTest(ext=ext):
                file = self.root / ("native." + ext)
                opts = {
                    "column_labels": {"x": "response"},
                    "variable_value_labels": {
                        "x": {1.0: "yes", 2.0: "no", 99.0: "missing"}
                    },
                }
                if ext == "dta":
                    pyreadstat.write_dta(frame, str(file), **opts)
                else:
                    pyreadstat.write_sav(
                        frame, str(file), missing_ranges={"x": [99.0]}, **opts
                    )
                register_file(self.root, self.s, self.spec, self.id, file, "QA-1")
                state = workflow(self.root, self.s, "r")
                self.assertEqual(state["datasets"][self.id]["import"], "passed")
                self.assertEqual(state["analysis_status"], "미실행")
                audit = validate(self.root, self.s)["datasets"][self.id]
                self.assertEqual(audit["columns"]["x"]["label"], "response")
                self.assertEqual(
                    audit["columns"]["x"]["missing"], 0 if ext == "dta" else 1
                )
                self.assertIn("x", audit["format_metadata"]["value_labels"])


if __name__ == "__main__":
    unittest.main()
