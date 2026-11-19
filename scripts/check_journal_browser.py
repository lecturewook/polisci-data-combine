"""Release에서 내려받은 독립 HTML을 데스크톱·모바일 Chrome에서 검사한다."""

import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit

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
    page.locator('[data-jour="directory"]').click()
    assert page.locator("#jdir-list section > details").count() == 51
    page.locator("#jdir-q").fill("APSR")
    page.locator("#jdir-list summary").first.click()
    assert "0003-0554" in page.locator("#jdir-list").inner_text()
    page.locator('#jdir-list [data-jour="journal"]').click()
    assert page.locator('[data-jfilter="journal"]').input_value() == "apsr"
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
                f"{'모바일' if mobile else '데스크톱'} Chrome에서 저널, 그래프, 논문, 원문·재현 링크, 자료 탐색 확인",
                flush=True,
            )
        browser.close()
