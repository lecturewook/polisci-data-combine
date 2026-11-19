"""공식 논문 페이지의 식별자와 명시적인 초록 영역을 확인한다."""

import json
import re
import time
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from bs4 import BeautifulSoup
from protego import Protego

from .journal_records import abstract_text, doi, normalized

AGENT = "PoliHub/6 (+https://github.com/lecturewook/polisci-data-combine)"
HOSTS = {
    "www.sciencedirect.com",
    "www.journals.uchicago.edu",
    "www.tandfonline.com",
    "www.cambridge.org",
    "academic.oup.com",
    "link.springer.com",
    "onlinelibrary.wiley.com",
    "journals.sagepub.com",
    "direct.mit.edu",
    "www.ingentaconnect.com",
    "muse.jhu.edu",
}
SECTIONS = (
    ".abstractSection",
    ".abstractInFull",
    ".article-section__abstract",
    ".abstract-group",
    ".abstractInFullHeading + div",
    "#Abs1",
    "#abstract",
    "#abstracts .abstract.author",
    "section.abstract",
    "div.abstract",
    "[data-title='Abstract']",
    "[aria-label='Abstract']",
)


def publisher_url(a):
    url = a.get("publisher_url") or a.get("url") or ""
    parts = urlsplit(url)
    if parts.hostname == "linkinghub.elsevier.com":
        match = re.fullmatch(r"/retrieve/pii/([a-z0-9]+)", parts.path, re.I)
        if match:
            return "https://www.sciencedirect.com/science/article/pii/" + match[1]
    if parts.scheme in ("http", "https") and parts.netloc.lower() in HOSTS:
        return parts._replace(scheme="https", fragment="").geturl()
    return ""


def publisher_abstract(html, article, facts=None):
    soup = BeautifulSoup(html, "html.parser")
    meta = {}
    for tag in soup.find_all("meta", content=True):
        name = (tag.get("name") or tag.get("property") or "").lower()
        meta.setdefault(name, []).append(tag["content"].strip())
    identifiers = {
        doi(v)
        for name in ("citation_doi", "dc.identifier", "dc.identifier.doi", "prism.doi")
        for v in meta.get(name, [])
        if doi(v)
    }
    titles = meta.get("citation_title", []) + meta.get("dc.title", [])
    if h := soup.find("h1"):
        titles.append(h.get_text(" ", strip=True))
    canonical = doi(article.get("doi"))
    if identifiers and canonical not in identifiers:
        return "", "논문 식별 불일치"
    if not identifiers and normalized(article.get("title", "")) not in {
        normalized(t) for t in titles if t
    }:
        return "", "논문 식별 미확인"
    cambridge = urlsplit(publisher_url(article)).hostname == "www.cambridge.org"
    if cambridge and facts is not None:
        genres = {
            "book review": "서평",
            "book reviews": "서평",
            "corrigendum": "정정·추가·철회",
            "erratum": "정정·추가·철회",
            "editorial": "편집자료",
        }
        for label in soup.find_all(string=re.compile(r"^\s*Type\s*$", re.I)):
            for node in (label.parent, label.parent.parent):
                if node is None or (value := node.find_next_sibling()) is None:
                    continue
                value = value.get_text(" ", strip=True)
                if value.casefold() in genres:
                    facts.update(
                        notice_type=genres[value.casefold()],
                        location="Cambridge Core · Information · " + value,
                    )
                    break
            if facts:
                break
    if soup.find(
        string=re.compile(
            r"^\s*(?:an abstract is not available for this (?:article|content)|no abstract (?:is )?available for this (?:article|content))\b",
            re.I,
        )
    ):
        return "", "출판사가 초록 미제공으로 표시"
    for field in (
        ()
        if cambridge
        else ("citation_abstract", "dcterms.abstract", "dc.description.abstract")
    ):
        for value in meta.get(field, []):
            if text := abstract_text(value):
                return text, field
    for tag in [] if cambridge else soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(tag.get_text())
        except (ValueError, TypeError):
            continue
        nodes = payload if isinstance(payload, list) else [payload]
        for node in nodes[:]:
            if isinstance(node, dict):
                nodes.extend(node.get("@graph") or [])
        for node in nodes:
            if not isinstance(node, dict) or "ScholarlyArticle" not in str(
                node.get("@type", "")
            ):
                continue
            if node.get("headline") and normalized(node["headline"]) != normalized(
                article.get("title", "")
            ):
                continue
            if text := abstract_text(node.get("abstract")):
                return text, "ScholarlyArticle.abstract"
    for selector in SECTIONS:
        for area in soup.select(selector):
            if area.name in ("h1", "h2", "h3", "h4"):
                continue
            # Headings, navigation and graphical abstracts are not author prose.
            if "graphical" in " ".join(area.get("class", [])).lower():
                continue
            for drop in area.select(
                "nav, aside, figure, script, style, button, h1, h2, h3, h4, .abstract-title"
            ):
                drop.decompose()
            paragraphs = [x.get_text(" ", strip=True) for x in area.select("p")]
            raw = " ".join(paragraphs) if paragraphs else area.get_text(" ", strip=True)
            if text := abstract_text(raw):
                return text, "공식 페이지 Abstract"
    if cambridge:
        for heading in soup.find_all(["h2", "h3", "h4"]):
            if heading.get_text(" ", strip=True).lower() != "abstract":
                continue
            body = heading.find_next_sibling()
            if (
                body
                and body.name in ("p", "div")
                and not body.find(["h1", "h2", "h3", "h4"])
            ):
                if text := abstract_text(body.get_text(" ", strip=True)):
                    return text, "공식 페이지 Abstract"
    return "", "확인 가능한 초록란 없음"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Publisher:
    """한 호스트에 한 작업자만 접근하며 거절·속도 제한을 다음 실행까지 유지한다."""

    def __init__(self, host):
        self.host = host
        self.opener = build_opener(NoRedirect())
        self.policy = None
        self.stop = ""
        self.interval = 1.0
        self.last = 0.0
        self.transient = 0

    def request(self, url, limit):
        time.sleep(max(0.0, self.interval - (time.monotonic() - self.last)))
        self.last = time.monotonic()
        req = Request(
            url,
            headers={
                "User-Agent": AGENT,
                "Accept": "text/html,text/plain,application/xhtml+xml",
            },
        )
        with self.opener.open(req, timeout=12) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            mime = response.headers.get_content_type()
            data = response.read(limit)
            try:
                return data.decode(charset, "replace"), mime
            except LookupError:
                return data.decode("utf-8", "replace"), mime

    def robots(self):
        if self.policy is not None or self.stop:
            return
        try:
            body, _ = self.request("https://" + self.host + "/robots.txt", 512000)
            if re.search(r"<(?:html|!doctype)\b", body[:500], re.I):
                self.stop = "robots 정책 미확인"
                return
        except HTTPError as e:
            if e.code not in (404, 410):
                self.stop = f"robots HTTP {e.code}"
                return
            body = ""
        except (OSError, HTTPException):
            self.stop = "robots 연결 실패"
            return
        self.policy = Protego.parse(body)
        delay = self.policy.crawl_delay(AGENT) or 0
        rate = self.policy.request_rate(AGENT)
        self.interval = max(
            1.0, delay, rate.seconds / rate.requests if rate and rate.requests else 0
        )
        if self.interval > 60:
            self.stop = "게시자 요청 간격에 따라 보류"

    def read(self, url, article):
        self.robots()
        if self.stop:
            return "", url, self.stop + " · 추가 요청 보류"
        for _ in range(5):
            if not self.policy.can_fetch(url, AGENT):
                return "", url, "robots 정책에 따라 보류"
            try:
                html, mime = self.request(url, 2_000_000)
                self.transient = 0
                if mime not in ("text/html", "application/xhtml+xml"):
                    return "", url, "HTML 초록 페이지 미확인"
                if re.search(
                    r"<title[^>]*>\s*(?:just a moment|access denied|robot check|captcha|checking your browser)",
                    html[:20000],
                    re.I,
                ):
                    self.stop = "출판사 접근 확인 화면"
                    return "", url, self.stop
                try:
                    facts = {}
                    text, location = publisher_abstract(html, article, facts)
                    if facts and not article.get("genre_proof"):
                        article.update(
                            kind="notice",
                            notice_type=facts["notice_type"],
                            genre_proof={"url": url, "location": facts["location"]},
                        )
                except (ValueError, TypeError, RecursionError):
                    return "", url, "초록란 형식 확인 보류"
                return text, url, location
            except HTTPError as e:
                if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                    target = urljoin(url, e.headers["Location"])
                    if (
                        urlsplit(target).scheme == "https"
                        and urlsplit(target).netloc == self.host
                        and target != url
                    ):
                        url = target
                        continue
                    return "", url, "다른 주소로 이동 · 원문 확인 필요"
                if e.code in (401, 403, 429, 451):
                    self.stop = f"출판사 HTTP {e.code}"
                if e.code >= 500:
                    self.transient += 1
                reason = f"출판사 HTTP {e.code}"
            except (OSError, HTTPException):
                self.transient += 1
                reason = "출판사 연결 실패"
            if self.transient >= 3:
                self.stop = reason
            return "", url, reason
        return "", url, "주소 이동 확인 보류"
