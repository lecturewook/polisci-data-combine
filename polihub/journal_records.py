"""저널 논문 식별, 날짜, 방법론 근거. 데이터·코드의 DOI는 별도로 취급한다."""

import calendar
import hashlib
import html
import re
import unicodedata
from datetime import date
from urllib.parse import unquote


def clean(value):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", str(value or ""))).split())


def normalized(value):
    return "".join(
        c for c in unicodedata.normalize("NFKC", value).casefold() if c.isalnum()
    )


def doi(value):
    value = re.sub(
        r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)",
        "",
        str(value or "").strip(),
        flags=re.I,
    )
    value = unquote(value).strip().lower()
    return value if re.fullmatch(r"10\.\d{4,9}/[^\s<>\x00-\x1f]+", value) else ""


def parts(raw):
    values = (raw or {}).get("date-parts") or [[]]
    return "-".join(f"{n:02d}" for n in values[0]) if values[0] else ""


def bounds(value):
    try:
        if not re.fullmatch(r"\d{4}(-\d{2}){0,2}", value):
            return None
        p = [int(x) for x in value.split("-")]
        y, m = p[0], p[1] if len(p) >= 2 else 1
        last_m = m if len(p) >= 2 else 12
        return (
            date(y, m, p[2] if len(p) == 3 else 1),
            date(y, last_m, p[2] if len(p) == 3 else calendar.monthrange(y, last_m)[1]),
        )
    except (ValueError, TypeError):
        return None


def publication(a):
    # Specific online/issue dates take precedence over less precise versions of the same date.
    values = [
        v
        for k in ("online", "issue_date", "published")
        if (v := a.get(k)) and bounds(v)
    ]
    precise = [v for v in values if not any(o != v and o.startswith(v) for o in values)]
    return min(precise, default="")


def in_window(a, start, end):
    pair = bounds(publication(a))
    return pair and pair[1] >= start and pair[0] <= end


NONRESEARCH = re.compile(
    r"^(?:book reviews?\b|corrigendum\b|erratum\b|correction(?:\s+(?:to|notice)\b|\s*[:–—])|retraction(?: notice)?\b|editorial(?: introduction| note)?\s*[:–—]|editorial$|front matter\b|back matter\b|table of contents\b|reviewers? acknowledg|books received\b)",
    re.I,
)


def pub_type(title, source_type="journal-article"):
    if NONRESEARCH.search(title) or re.search(
        r"\s[-–—]\s(?:corrigendum|erratum|correction)$", title, re.I
    ):
        return "notice"
    if re.search(
        r"book.review|editorial|erratum|correction|retraction|paratext|front.matter|back.matter",
        source_type,
        re.I,
    ):
        return "notice"
    return source_type or "journal-article"


LEVELS = {"clue": 1, "stated": 2, "direct": 3}
# Only explicit author actions immediately governing the method qualify.
ACTION = re.compile(
    r"\b(?:we|I)\s+(?:(?:also|then|first|further)\s+)?(?:use[ds]?|employ(?:ed)?|conduct(?:ed)?|fit(?:ted)?|implement(?:ed)?|apply|applied|estimate[d]?|ran|run|perform(?:ed)?)\s+(?:(?:a|an|the|our|two|three|paired|preregistered|national|original|new|randomized|online|fielded|series of)\s+){0,4}$",
    re.I,
)
CONTEXT = re.compile(
    r"\b(?:prior|previous|earlier|literature|others|unlike|instead of|without|not|rather than|compared? (?:with|to)|comparison|benchmark|competing|alternatives?|sensitivity to|cannot|could|would|might|future|hypothetical|limitation|whether|if|should|propose to|do not|did not|suggest)\b",
    re.I,
)


class Methods:
    def __init__(self, nodes):
        self.nodes = {n["id"]: n for n in nodes}
        self.patterns = []
        for n in nodes:
            self.path(n["id"])
            for alias in sorted(n["aliases"], key=len, reverse=True):
                flags = 0 if alias in {"DiD", "IV", "FE", "SC"} else re.I
                self.patterns.append(
                    (
                        n["id"],
                        re.compile(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", flags),
                    )
                )

    def path(self, key):
        path = []
        while key is not None:
            if key in path:
                raise ValueError("방법 분류 순환")
            path.append(key)
            key = self.nodes[key]["parent"]
        return path

    def classify(self, title, abstract, url):
        best = {}
        for location, source in (("제목", title), ("초록", abstract)):
            for sentence in re.split(r"(?<=[.!?])\s+|[;\n]", source or ""):
                matches = sorted(
                    (
                        (m.start(), m.end(), key, m.group())
                        for key, pattern in self.patterns
                        for m in pattern.finditer(sentence)
                    ),
                    key=lambda item: (item[0], -item[1]),
                )
                taken = -1
                for begin, end, key, term in matches:
                    if begin < taken:
                        continue
                    taken = end
                    action = ACTION.search(sentence[:begin])
                    stated = (
                        location == "초록" and action and not CONTEXT.search(sentence)
                    )
                    level = "stated" if stated else "clue"
                    if key in best and LEVELS[best[key]["level"]] >= LEVELS[level]:
                        continue
                    # Store the short supporting phrase, not the full abstract.
                    evidence = sentence[action.start() : end] if stated else term
                    purpose = sentence[end:].lower()
                    role = "미확인"
                    if stated:
                        if re.match(
                            r" to (?:classify|label|measure|code|construct)", purpose
                        ):
                            role = "데이터 구축·측정"
                        elif re.match(
                            r" to estimate (?:the |a )?(?:effect|impact)", purpose
                        ):
                            role = "주 추정"
                        elif key in {"surveyexp", "conjoint", "fieldexp", "labexp"}:
                            role = "연구 설계"
                    best[key] = dict(
                        method=key,
                        term=term,
                        level=level,
                        role=role,
                        evidence=evidence,
                        location=location,
                        url=url,
                    )
        return list(best.values())


def fingerprint(a):
    title = normalized(a.get("title", ""))
    names = tuple(normalized(x) for x in a.get("authors", []) if x)
    year = publication(a)[:4]
    if not (len(title) >= 12 and names and year):
        return None
    return (
        a["journal"],
        title,
        names,
        year,
        str(a.get("volume") or ""),
        str(a.get("issue") or ""),
    )


def merge_list(old, new):
    for item in new:
        if item not in old:
            old.append(item)


class ArticleIndex:
    def __init__(self, articles):
        self.rows = articles
        self.dois = {}
        self.ids = {}
        self.fingerprints = {}
        for key, a in articles.items():
            self.index(key, a)

    def index(self, key, a):
        if a.get("doi"):
            if self.dois.setdefault(a["doi"], key) != key:
                raise ValueError("색인에 중복 DOI")
        for i in a.get("source_ids", []):
            if self.ids.setdefault(i, key) != key:
                raise ValueError("색인에 중복 출처 ID")
        if f := fingerprint(a):
            self.fingerprints.setdefault(f[:3], set()).add(key)

    def put(self, a, day):
        d = doi(a.get("doi"))
        a["doi"] = d
        f = fingerprint(a)
        ids = a.get("source_ids", [])
        keys = {self.ids[i] for i in ids if i in self.ids}
        if d in self.dois:
            keys.add(self.dois[d])
        if not keys and f:
            for k in self.fingerprints.get(f[:3], ()):
                other = self.rows[k]
                if d and other.get("doi"):
                    continue
                other_f = fingerprint(other)
                same_year = f[3] in {
                    str(other.get(field) or "")[:4]
                    for field in ("online", "published", "issue_date")
                }
                compatible = all(
                    not x or not y or x == y for x, y in zip(f[4:], other_f[4:])
                )
                if same_year and compatible:
                    keys.add(k)
        if len(keys) > 1 or (not d and not ids and not f):
            return "ambiguous", None
        if not keys:
            key = d or (
                ids[0]
                if ids
                else "bib:" + hashlib.sha256(repr(f).encode()).hexdigest()[:28]
            )
            a["first_seen"] = day
            self.rows[key] = a
            self.index(key, a)
            return "added", key
        key = next(iter(keys))
        old = self.rows[key]
        if old["journal"] != a["journal"] or (d and old.get("doi") and old["doi"] != d):
            return "ambiguous", None
        changed = False
        for k, v in a.items():
            if not v or k in ("first_seen", "journal"):
                continue
            if k in ("sources", "source_ids", "keywords", "methods", "resources"):
                bucket = old.setdefault(k, [])
                before = len(bucket)
                if k == "resources":
                    for item in v:
                        exists = next(
                            (x for x in bucket if x["url"] == item["url"]), None
                        )
                        if exists is None:
                            bucket.append(item)
                        else:
                            for field, value in item.items():
                                if value and not exists.get(field):
                                    exists[field] = value
                                    changed = True
                else:
                    merge_list(bucket, v)
                changed |= len(bucket) != before
            elif not old.get(k) or (
                k in ("published", "online", "issue_date")
                and v.startswith(old[k])
                and len(v) > len(old[k])
            ):
                old[k] = v
                changed = True
            elif k == "kind" and old[k] == "journal-article" and v != "journal-article":
                old[k] = v
                changed = True
        if changed:
            self.index(key, old)
        return ("updated" if changed else "duplicate"), key


def methods_by_level(article, taxonomy):
    groups = {}
    if article.get("kind") == "notice":
        return groups
    for e in article.get("methods", []):
        for key in taxonomy.path(e["method"]):
            groups[key] = max(groups.get(key, 0), LEVELS[e["level"]])
    return groups
