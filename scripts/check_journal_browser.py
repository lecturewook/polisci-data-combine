"""Release에서 내려받은 독립 HTML을 데스크톱·모바일 Chrome에서 검사한다."""

import json
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright


def check(browser, url, mobile):
    context = browser.new_context(
        viewport={"width": 390 if mobile else 1260, "height": 844},
        is_mobile=mobile,
        has_touch=mobile,
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    # Only the original document can be loaded. All UI assets and journal data must be embedded.
    context.route("https://**/*", lambda route: route.abort())
    page.goto(url)
    page.get_by_role("heading", name="저널·방법론 색인").wait_for()
    context.set_offline(True)
    legend = page.get_by_label("방법별 막대 범례")
    assert "초록색 위 막대 · 적용 확인" in legend.inner_text()
    assert "황토색 아래 막대 · 단서만 발견" in legend.inner_text()
    assert page.evaluate("""() => ['.applied', '.clue'].every((selector, i) =>
      getComputedStyle(document.querySelector('.jlegend ' + selector)).backgroundColor ===
      getComputedStyle(document.querySelectorAll('.jtracks i')[i]).backgroundColor)""")
    page.locator('[data-jfilter="abstract"]').select_option("yes")
    page.get_by_role("searchbox", name="논문 검색").fill("10.1086/730737")
    page.locator("#journal-search").get_by_role(
        "button", name="검색", exact=True
    ).click()
    page.locator("#jpapers .jlink").click()
    check_modal = page.locator("#modal[open]")
    assert (
        check_modal.get_by_role("link", name="공식 저널에서 초록 보기").get_attribute(
            "href"
        )
        == "https://www.journals.uchicago.edu/doi/10.1086/730737"
    )
    assert "XLM-RoBERTa" in check_modal.inner_text()
    scholar = urlsplit(
        check_modal.get_by_role(
            "link", name="Google Scholar에서 제목 검색"
        ).get_attribute("href")
    )
    assert (
        scholar.hostname == "scholar.google.com"
        and "BERT, RoBERTa, or DeBERTa?" in parse_qs(scholar.query)["q"][0]
    )
    check_modal.get_by_role("button", name="닫기").click()
    page.locator('[data-jfilter="abstract"]').select_option("unavailable")
    page.get_by_role("searchbox", name="논문 검색").fill("10.1017/s0305741025101768")
    page.locator("#journal-search").get_by_role(
        "button", name="검색", exact=True
    ).click()
    page.locator("#jpapers .jlink").click()
    assert (
        "출판사가 별도 초록을 제공하지 않는다고 표시했습니다."
        in check_modal.inner_text()
    )
    assert "편집자료 · 방법 집계 제외" in check_modal.inner_text()
    check_modal.get_by_role("button", name="닫기").click()
    page.locator('[data-jour="reset"]').click()
    page.locator('[data-jour="directory"]').click()
    registry = json.loads(
        (Path(__file__).resolve().parents[1] / "catalog/journals.json").read_text()
    )
    cards = page.locator("#jdir-list section > details")
    assert cards.count() == len(registry["journals"])
    page.locator("#jdir-q").fill("APSR")
    journal = page.locator("#jdir-list section > details").filter(
        has=page.locator("summary", has_text="American Political Science Review")
    )
    journal.locator(":scope > summary").click()
    table = journal.locator("table")
    table.locator(
        "thead th", has_text="방법 집계 대상의 초록 확인 / 대상 논문"
    ).wait_for(state="visible")
    assert "0003-0554" in journal.inner_text()
    assert any(
        re.search(r"\d+\s*/\s*[\d,]+\s*\([\d.]+%\)", value)
        for value in table.locator("tbody tr td:last-child").all_text_contents()
    )
    page.locator('#jdir-list [data-jour="journal"]').click()
    assert page.locator('[data-jfilter="journal"]').input_value() == "apsr"
    page.locator('[data-jour="reset"]').click()
    interests = page.get_by_label("관심사별 저널")
    interests.select_option("v7")
    page.get_by_role("button", name="선택한 저널과 선정 근거 보기").click()
    assert cards.count() == len(
        [j for j in registry["journals"] if j.get("introduced") == 7]
    )
    page.locator("#jdir-q").fill("Journal of Population Economics")
    assert cards.count() == 1
    cards.locator(":scope > summary").click()
    assert "50종 중 7위" in cards.inner_text()
    assert (
        cards.get_by_role("link", name="선정 근거").get_attribute("href")
        == "https://glabor.org/general-rankings/"
    )
    interests.select_option("korea")
    options = page.locator('[data-jfilter="journal"] option')
    assert options.filter(has_text="Journal of East Asian Studies").count() == 1
    assert options.filter(has_text="American Political Science Review").count() == 0
    page.get_by_label("저널", exact=True).select_option("kpsr")
    page.get_by_role("searchbox", name="논문 검색").fill(
        "Revisiting the Ideological Foundations"
    )
    page.locator("#journal-search").get_by_role(
        "button", name="검색", exact=True
    ).click()
    page.locator("#jpapers .jlink").click()
    assert "교차지연 패널 모형" in check_modal.inner_text()
    assert "10.18854/kpsr.2026.60.3.003" in check_modal.inner_text()
    assert "ART003386437" in check_modal.get_by_role(
        "link", name="한국학술지인용색인(KCI)에서 초록 보기"
    ).get_attribute("href")
    check_modal.get_by_role("button", name="닫기").click()
    page.locator('[data-jour="reset"]').click()
    for method in ["exp", "surveyexp", "conjoint"]:
        page.locator(f'[data-jour="method"][data-value="{method}"]').click()
    page.locator('[data-jour="list"]').click()
    page.get_by_role("searchbox", name="논문 검색").fill("Eady")
    page.locator("#journal-search").get_by_role(
        "button", name="검색", exact=True
    ).click()
    page.locator("#jpapers").get_by_role(
        "button", name=re.compile("Gendered Perceptions")
    ).click()
    modal = page.locator("#modal[open]")
    assert "본문·코드 직접 확인" in modal.inner_text()
    assert "Average Marginal Component Effects" in modal.inner_text()
    origin = modal.get_by_role("link", name="논문 원문·출판사")
    assert urlsplit(origin.get_attribute("href")).hostname in {
        "doi.org",
        "www.cambridge.org",
        "cambridge.org",
        "dx.doi.org",
    }
    assert modal.locator('a[href*="B3UBXR" i]').count() >= 1
    with page.expect_popup() as popup:
        origin.click()
    popup.value.close()
    modal.get_by_role("button", name="닫기").click()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    page.get_by_role("link", name="자료 탐색", exact=True).click()
    page.get_by_role("heading", name="연구분야로 탐색").wait_for()
    assert not errors, errors
    context.close()


if __name__ == "__main__":
    source = sys.argv[1]
    url = (
        source
        if source.startswith("http://127.0.0.1:")
        else Path(source).resolve().as_uri()
    )
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=shutil.which("google-chrome") or shutil.which("chromium"),
            args=["--no-sandbox"],
        )
        for mobile in (False, True):
            check(browser, url, mobile)
            print(
                f"{'모바일' if mobile else '데스크톱'} Chrome에서 관심사별 저널, 한국 논문 국·영문 검색과 KCI 출처, 색상 범례, 방법 그래프와 원문·재현 링크 확인",
                flush=True,
            )
        browser.close()
