"use strict";

let journalLoad, journalData;
const jf = {
  q: "",
  interest: "",
  field: "",
  journal: "",
  year: "",
  level: "",
  abstract: "",
  method: "",
  packages: false,
  page: 1,
  directory: "",
};
const journalLevels = {
  direct: "본문·코드 직접 확인",
  stated: "초록·재현 설명에 적용 명시",
  clue: "방법명 단서",
  unknown: "미확인",
};
const levelOrder = { direct: 3, stated: 2, clue: 1, unknown: 0 };
const journalEditions = {
  v7: "v7에서 추가한 저널",
  original: "최초 수집 저널",
};
const statuses = {
  success: "수집 성공",
  partial: "부분 성공",
  failed: "수집 실패",
};
const num = (n) => Number(n).toLocaleString("ko-KR");
const jnorm = (v) =>
  String(v || "")
    .normalize("NFKC")
    .toLowerCase();
const jpdate = (a) => {
  if (a.verified_publication) return a.verified_publication;
  const values = [a.online, a.issue_date, a.published].filter(Boolean);
  return (
    values
      .filter((v) => !values.some((o) => o !== v && o.startsWith(v)))
      .sort()[0] || ""
  );
};
const jdate = (v) =>
  v
    ? esc(v) + (v.length === 4 ? " (연도)" : v.length === 7 ? " (월)" : "")
    : "미확인";
const jurl = (u, t) =>
  link(typeof u === "string" ? u.replace(/^http:\/\//, "https://") : "", t);
const jbutton = (key, value, label, cls = "jlink") =>
  '<button type="button" class="' +
  cls +
  '" data-jour="' +
  key +
  '" data-value="' +
  esc(value) +
  '">' +
  label +
  "</button>";
const percent = (a, b) => (b ? ((100 * a) / b).toFixed(1) + "%" : "—");
const eligible = (a) => a.kind !== "notice";

async function jsonResource(name) {
  if (window.POLIHUB_OFFLINE) return window.POLIHUB_OFFLINE[name];
  const r = await fetch("catalog/" + name + ".json");
  if (!r.ok) throw Error(name + " HTTP " + r.status);
  return r.json();
}
async function journalIndex() {
  let bytes;
  const embedded = document.getElementById("journal-data");
  if (embedded) {
    const str = atob(embedded.textContent.trim());
    bytes = Uint8Array.from(str, (c) => c.charCodeAt(0));
  } else {
    const r = await fetch("catalog/journal-index.json.gz");
    if (!r.ok) throw Error("논문 색인 HTTP " + r.status);
    bytes = new Uint8Array(await r.arrayBuffer());
  }
  let stream = new Blob([bytes]).stream();
  if (bytes[0] === 31 && bytes[1] === 139)
    stream = stream.pipeThrough(new DecompressionStream("gzip"));
  return new Response(stream).json();
}
async function loadJournal() {
  const [registry, nodes, sources, state] = await Promise.all([
    jsonResource("journals"),
    jsonResource("methods"),
    jsonResource("journal-sources"),
    journalIndex(),
  ]);
  const journals = new Map(registry.journals.map((j) => [j.id, j]));
  const interests = new Map(registry.interest_groups.map((g) => [g.id, g]));
  const editions = new Set(
    registry.journals.filter((j) => j.introduced === 7).map((j) => j.id),
  );
  const interestJournals = new Map(
    [...interests].map(([id, group]) => [id, new Set(group.journals)]),
  );
  interestJournals.set("v7", editions);
  interestJournals.set(
    "original",
    new Set(
      registry.journals
        .filter((j) => !j.introduced || j.introduced <= 5)
        .map((j) => j.id),
    ),
  );
  const methods = new Map(nodes.map((m) => [m.id, m]));
  const paths = new Map();
  for (const node of nodes) {
    const path = [];
    for (let m = node; m; m = methods.get(m.parent)) path.unshift(m.id);
    paths.set(node.id, path);
  }
  const archived = new Map();
  const rows = Object.entries(state.articles)
    .filter(([key, a]) => {
      if (!a.linked_to && !a.outside_window) return true;
      if (!archived.has(a.journal)) archived.set(a.journal, []);
      archived.get(a.journal).push([key, a]);
      return false;
    })
    .map(([key, a]) => {
      const levels = new Map();
      for (const e of a.methods || []) {
        for (const id of paths.get(e.method) || [])
          levels.set(id, Math.max(levels.get(id) || 0, levelOrder[e.level]));
      }
      const best = Math.max(0, ...levels.values());
      const j = journals.get(a.journal);
      const terms = [...levels.keys()].map((id) =>
        [methods.get(id).label, ...methods.get(id).aliases].join(" "),
      );
      const search = jnorm(
        [
          a.title,
          ...(a.other_titles || []),
          ...(a.authors || []),
          ...(a.other_authors || []),
          a.doi,
          ...(a.keywords || []),
          ...terms,
          ...(a.methods || []).map((e) => e.term),
          j.name,
          j.abbr,
          j.field,
        ]
          .filter(Boolean)
          .join(" "),
      );
      return {
        key,
        a,
        j,
        levels,
        best,
        search,
        date: jpdate(a),
        proof: Boolean(
          a.has_abstract ||
          (a.methods || []).some(
            (e) => e.level === "direct" || e.level === "stated",
          ),
        ),
      };
    })
    .sort((x, y) => y.date.localeCompare(x.date) || x.key.localeCompare(y.key));
  return {
    registry,
    journals,
    interests,
    interestJournals,
    methods,
    paths,
    sources,
    state,
    rows,
    archived,
    byKey: new Map(rows.map((r) => [r.key, r])),
  };
}
async function journalPage() {
  const main = document.getElementById("main");
  try {
    if (!journalData) {
      main.innerHTML =
        '<p class="loading" role="status">저널 색인을 여는 중입니다.</p>';
      journalLoad ||= loadJournal();
      journalData = await journalLoad;
    }
    if (view !== "journals") return;
    const d = journalData,
      s = d.state,
      run = s.runs[s.runs.length - 1];
    const monthGain = s.runs
      .filter((v) => v.date.slice(0, 7) === run.date.slice(0, 7))
      .reduce(
        (sum, v) =>
          sum +
          (v.abstract_audit
            ? (v.abstract_audit.after || 0) - (v.abstract_audit.before || 0)
            : 0),
        0,
      );
    const complete = Object.values(s.coverage).filter(
      (c) => c.sources.Crossref?.complete,
    ).length;
    const select = (k, label, choices) =>
      "<label>" +
      label +
      '<select aria-label="' +
      esc(label) +
      '" data-jfilter="' +
      k +
      '">' +
      options(choices, jf[k]) +
      "</select></label>";
    const fields = Object.fromEntries(
      [...new Set([...d.journals.values()].map((j) => j.field))].map((f) => [
        f,
        f,
      ]),
    );
    const cohort = d.interestJournals.get(jf.interest);
    const available = [...d.journals].filter(
      ([id]) => !cohort || cohort.has(id),
    );
    main.innerHTML =
      '<div class="journal-page"><div class="page-intro"><h1 class="page-title">저널·방법론 색인</h1>' +
      "<p>분야별 대표 저널과 인접 분야 저널 " +
      d.journals.size +
      "종의 발행 기록을 탐색합니다.</p>" +
      "<p>최초 수집 " +
      esc(s.start) +
      " ~ " +
      esc(s.initial_end) +
      " · 최근 수집 " +
      esc(s.as_of) +
      " (한국 시간) · 누적 발행 기록 " +
      num(run.total) +
      "건 · 방법 집계 대상 " +
      num(d.rows.filter((r) => eligible(r.a)).length) +
      "편 · 서평·정정 등 " +
      num(d.rows.filter((r) => !eligible(r.a)).length) +
      "건</p><p>Crossref 마지막 페이지 확인 " +
      complete +
      " / " +
      d.journals.size +
      "종 · 제목 확보 " +
      num(d.rows.filter((r) => r.a.title).length) +
      "건 · 초록 확보 " +
      num(d.rows.filter((r) => r.a.has_abstract).length) +
      "건 · " +
      jbutton("directory", "", "저널별 확보 현황") +
      "</p>" +
      (run.abstract_audit?.eligible
        ? "<details class='jabstract-guide'><summary>초록 확보 현황과 미확인 사유</summary><p>방법 집계 대상 " +
          num(run.abstract_audit.eligible) +
          "편 중 " +
          num(run.abstract_audit.after) +
          "편의 초록을 확인했습니다. 이달의 보완 실행을 거친 확인 편수의 순증은 " +
          num(monthGain) +
          "편입니다.</p><p>출판사 페이지에 보이는 초록이 Crossref 기탁 자료에서는 빠질 수 있습니다. 공식 저널의 초록란과 DOI·표제가 일치하는 학술 색인을 대조합니다. 각 논문에서 확인 출처와 Google Scholar 제목 검색을 열 수 있습니다.</p></details>"
        : "") +
      "</div>" +
      '<form class="surface jfilters" id="journal-search"><label class="jquery">논문 검색<input name="q" type="search" placeholder="제목, 저자, DOI, 저자 키워드, 방법명" value="' +
      esc(jf.q) +
      '"></label><button class="primary">검색</button>' +
      select("interest", "관심사별 저널", {
        ...Object.fromEntries(
          Object.entries(journalEditions).map(([id, label]) => [
            id,
            label + " (" + d.interestJournals.get(id).size + "종)",
          ]),
        ),
        ...Object.fromEntries(
          [...d.interests].map(([id, g]) => [
            id,
            g.label + " (" + g.journals.length + "종)",
          ]),
        ),
      }) +
      select("field", "분야", fields) +
      select(
        "journal",
        "저널",
        Object.fromEntries(available.map(([k, j]) => [k, j.name])),
      ) +
      select(
        "year",
        "발행연도",
        Object.fromEntries(
          [...new Set(d.rows.map((r) => r.date.slice(0, 4)))]
            .filter(Boolean)
            .sort()
            .reverse()
            .map((y) => [y, y]),
        ),
      ) +
      select("level", "논문별 최고 근거", journalLevels) +
      select("abstract", "초록 상태", {
        yes: "확인",
        no: "미확인 전체",
        unavailable: "출판사가 미제공으로 표시",
      }) +
      '<label class="jcheck"><input type="checkbox" data-jfilter="packages" ' +
      (jf.packages ? "checked" : "") +
      "> 재현 패키지 연결</label>" +
      jbutton("reset", "", "조건 초기화") +
      "</form>" +
      (cohort
        ? '<p class="muted">' +
          (journalEditions[jf.interest] ||
            esc(d.interests.get(jf.interest).label) + " 관련 저널") +
          " " +
          cohort.size +
          "종의 전체 발행 목록입니다. 개별 논문의 주제나 연구 대상 국가는 제목과 초록에서 확인할 수 있습니다. " +
          jbutton("directory", "", "선택한 저널과 선정 근거 보기") +
          "</p>"
        : "") +
      '<div id="jresults"></div>' +
      '<details class="surface" id="jdir"><summary>저널 ' +
      available.length +
      '종 · 선정 근거와 확보 현황</summary><label class="jdir-search">저널명·약칭·분야·관심사 검색 <input type="search" id="jdir-q" value="' +
      esc(jf.directory) +
      '"></label><div id="jdir-list"></div></details>' +
      '<p class="muted">논문·재현 패키지·외부 데이터셋은 별도로 표시합니다. 원문과 배포처 링크를 열려면 인터넷 연결이 필요합니다.</p></div>';
    renderJournalResults();
    renderDirectory();
  } catch (err) {
    journalLoad = undefined;
    if (view === "journals")
      main.innerHTML =
        '<div class="empty"><h2>저널 색인을 열 수 없습니다.</h2><p>' +
        esc(err.message) +
        "</p></div>";
  }
}
function methodBars(rows) {
  const d = journalData,
    stats = new Map();
  for (const r of rows.filter((r) => eligible(r.a))) {
    for (const [key, n] of r.levels) {
      const value = stats.get(key) || [0, 0];
      value[n >= 2 ? 0 : 1]++;
      stats.set(key, value);
    }
  }
  const nodes = [...d.methods.values()].filter(
    (m) => (m.parent || "") === (jf.method || ""),
  );
  nodes.sort(
    (x, y) => (stats.get(y.id)?.[0] || 0) - (stats.get(x.id)?.[0] || 0),
  );
  const max = Math.max(
    1,
    ...nodes.map((m) => Math.max(...(stats.get(m.id) || [0, 0]))),
  );
  const bars = nodes
    .map((m) => {
      const [n, c] = stats.get(m.id) || [0, 0];
      return (
        "<li>" +
        jbutton(
          "method",
          m.id,
          esc(m.label) +
            '<span class="jtracks" aria-hidden="true"><i style="width:' +
            (100 * n) / max +
            '%"></i><i style="width:' +
            (100 * c) / max +
            '%"></i></span><span class="jvalues"><span>확인 ' +
            num(n) +
            "</span><span>단서 " +
            num(c) +
            "</span></span>",
          "jbar",
        ) +
        "</li>"
      );
    })
    .join("");
  const crumb = [
    "전체 방법",
    ...(d.paths.get(jf.method) || []).map((k) => d.methods.get(k).label),
  ]
    .map(esc)
    .join(" › ");
  return (
    '<section class="surface"><h2>방법별 논문 수</h2><p>' +
    crumb +
    "</p>" +
    (jf.method
      ? jbutton(
          "method",
          d.methods.get(jf.method)?.parent || "",
          "상위 분류로",
        ) +
        " · " +
        jbutton("list", "", "선택한 방법의 논문 보기")
      : "") +
    (bars
      ? '<dl class="jlegend" aria-label="방법별 막대 범례"><div><dt><i class="applied" aria-hidden="true"></i>초록색 위 막대 · 적용 확인</dt><dd>본문·재현 코드에서 직접 확인했거나, 초록·공식 재현자료 설명에 저자가 사용했다고 명시한 논문</dd></div><div><dt><i class="clue" aria-hidden="true"></i>황토색 아래 막대 · 단서만 발견</dt><dd>방법명이 등장하지만 이 논문에서 실제로 사용했는지는 미확인인 논문</dd></div></dl><p class="muted">각 방법 분류에서 한 논문은 가장 강한 근거로 한 번 셉니다.</p><ul class="jbars">' +
        bars +
        "</ul>"
      : '<p class="muted">이 방법에 연결된 논문은 아래 목록에서 열 수 있습니다.</p>') +
    "</section>"
  );
}
function trends(base) {
  const years = new Map(),
    months = new Map();
  for (const r of base) {
    const month = r.a.first_seen.slice(0, 7);
    months.set(month, (months.get(month) || 0) + 1);
    if (!eligible(r.a)) continue;
    const y = r.date.slice(0, 4),
      v = years.get(y) || [0, 0, 0, 0];
    const rank = jf.method ? r.levels.get(jf.method) || 0 : r.best;
    v[0]++;
    if (rank >= 2) v[1]++;
    if (rank === 1) v[2]++;
    if (r.proof) v[3]++;
    years.set(y, v);
  }
  const yrows = [...years]
    .sort()
    .map(
      ([y, [n, ap, cl, ev]]) =>
        "<tr><th>" +
        esc(y) +
        "</th><td>" +
        num(n) +
        "</td><td>" +
        num(ap) +
        " / " +
        num(cl) +
        "</td><td>" +
        percent(ap, n) +
        '<meter min="0" max="1" value="' +
        ap / n +
        '"></meter></td><td>' +
        num(ev) +
        " (" +
        percent(ev, n) +
        ")</td></tr>",
    )
    .join("");
  let total = 0;
  const mrows = [...months]
    .sort()
    .map(([m, n]) => {
      total += n;
      return (
        "<tr><th>" +
        esc(m) +
        "</th><td>" +
        num(n) +
        "</td><td>" +
        num(total) +
        "</td></tr>"
      );
    })
    .join("");
  return (
    '<section class="surface"><h2>연도별 방법 적용</h2><p class="muted">분모는 검색·필터 후 서평·정정 등 식별된 비연구물을 뺀 발행 기록입니다. 근거 확보는 초록 또는 적용 근거를 확인한 논문입니다.</p>' +
    '<div class="table-wrap"><table class="data-table"><thead><tr><th>발행연도</th><th>분모</th><th>적용 / 단서</th><th>적용 비중</th><th>근거 확보</th></tr></thead><tbody>' +
    yrows +
    "</tbody></table></div>" +
    '<details><summary>최초 발견월별 누적</summary><table class="data-table"><thead><tr><th>발견월</th><th>신규</th><th>누적</th></tr></thead><tbody>' +
    mrows +
    "</tbody></table></details></section>"
  );
}
function renderJournalResults() {
  const d = journalData,
    words = jnorm(jf.q.trim())
      .replace(/^(?:https?:\/\/(?:dx\.)?doi\.org\/|doi:\s*)/i, "")
      .split(/\s+/)
      .filter(Boolean);
  const base = d.rows.filter(
    (r) =>
      (!jf.interest || d.interestJournals.get(jf.interest).has(r.j.id)) &&
      (!jf.field || r.j.field === jf.field) &&
      (!jf.journal || r.j.id === jf.journal) &&
      (!jf.year || r.date.startsWith(jf.year)) &&
      (!jf.level || r.best === levelOrder[jf.level]) &&
      (!jf.abstract ||
        (jf.abstract === "unavailable"
          ? !r.a.has_abstract && Boolean(r.a.publisher_statement)
          : Boolean(r.a.has_abstract) === (jf.abstract === "yes"))) &&
      (!jf.packages ||
        (r.a.resources || []).some((v) => v.kind === "재현 패키지")) &&
      words.every((w) => r.search.includes(w)),
  );
  const filtered = jf.method
    ? base.filter((r) => eligible(r.a) && r.levels.has(jf.method))
    : base;
  const pages = Math.max(1, Math.ceil(filtered.length / 25));
  jf.page = Math.min(pages, jf.page);
  const list = filtered
    .slice((jf.page - 1) * 25, jf.page * 25)
    .map((r) => {
      const a = r.a;
      return (
        "<li>" +
        jbutton("paper", r.key, esc(a.title || a.doi || "제목 미확인")) +
        "<div>" +
        esc(r.j.abbr) +
        " · " +
        jdate(r.date) +
        " · " +
        esc((a.authors || []).join(", ") || "저자 미확인") +
        "</div></li>"
      );
    })
    .join("");
  const research = base.filter((r) => eligible(r.a));
  const counts = [
    research.filter((r) => r.best >= 2).length,
    research.filter((r) => r.best === 1).length,
    research.filter((r) => !r.best).length,
  ];
  const run = d.state.runs.at(-1);
  document.getElementById("jresults").innerHTML =
    '<div class="jstats">' +
    counts
      .map(
        (n, i) =>
          "<span>" +
          ["적용 확인", "단서만 발견", "방법 미확인"][i] +
          " <b>" +
          num(n) +
          "</b>편</span>",
      )
      .join("") +
    "</div>" +
    '<div class="jpanels">' +
    methodBars(base) +
    trends(base) +
    '</div><section class="surface" id="jpapers"><h2>' +
    esc(jf.method ? d.methods.get(jf.method).label + " · " : "") +
    "발행 목록 <small>" +
    num(filtered.length) +
    "건</small></h2>" +
    (list
      ? '<ol class="jpapers" start="' +
        ((jf.page - 1) * 25 + 1) +
        '">' +
        list +
        "</ol>"
      : "<p>조건에 맞는 발행 기록이 없습니다.</p>") +
    '<div class="jpaging">' +
    (jf.page > 1 ? jbutton("page", jf.page - 1, "이전") : "") +
    "<span>" +
    jf.page +
    " / " +
    num(pages) +
    "</span>" +
    (jf.page < pages ? jbutton("page", jf.page + 1, "다음") : "") +
    "</div></section>" +
    '<details class="surface"><summary>월별 수집 기록 · 최근 신규 ' +
    num(run.added) +
    '건</summary><div class="table-wrap"><table class="data-table"><thead><tr><th>실행일</th><th>신규</th><th>기존 보완</th><th>새로 확인한 초록</th><th>근거 재검토로 미확인 전환</th><th>방법 집계에서 재분류</th><th>중복 수신</th><th>부분 / 실패 저널</th><th>두 API 모두 실패</th><th>누적</th></tr></thead><tbody>' +
    [...d.state.runs]
      .reverse()
      .map(
        (s) =>
          "<tr>" +
          [
            s.date,
            num(s.added),
            num(s.updated),
            s.abstract_audit?.gained ?? "미기록",
            s.abstract_audit?.unconfirmed_after_review ?? "미기록",
            s.abstract_audit?.reclassified ?? "미기록",
            num(s.duplicates),
            s.partial.length + " / " + s.failed.length,
            s.api_failed?.length ?? "미기록",
            num(s.total),
          ]
            .map((v) => "<td>" + esc(v) + "</td>")
            .join("") +
          "</tr>",
      )
      .join("") +
    "</tbody></table></div><p>최근 점검에서 같은 논문으로 연결한 원기록 " +
    num(run.linked || 0) +
    "건 · 발행일이 수집 구간 밖으로 확인된 원기록 " +
    num(run.outside || 0) +
    "건</p></details>";
}
function renderDirectory() {
  const d = journalData,
    groups = new Map(),
    q = jnorm(jf.directory);
  for (const j of d.journals.values()) {
    if (jf.interest && !d.interestJournals.get(jf.interest).has(j.id)) continue;
    const groupsForJournal = [...d.interests.values()].filter((g) =>
      g.journals.includes(j.id),
    );
    if (
      !jnorm(
        [
          j.name,
          j.abbr,
          j.field,
          j.print_issn,
          j.online_issn,
          ...groupsForJournal.flatMap((g) => [g.label, ...g.aliases]),
        ].join(" "),
      ).includes(q)
    )
      continue;
    if (!groups.has(j.field)) groups.set(j.field, []);
    const c = d.state.coverage[j.id];
    const selection =
      (j.selection
        ? "<p>" +
          esc(j.selection.reason) +
          " " +
          jurl(j.selection.source, "선정 근거") +
          "</p>"
        : "") +
      (groupsForJournal.length
        ? "<p>관련 관심사 · " +
          groupsForJournal.map((g) => esc(g.label)).join(" / ") +
          "</p>"
        : "");
    if (!c) {
      groups
        .get(j.field)
        .push(
          "<details><summary>" +
            esc(j.name) +
            " · 미수집</summary>" +
            selection +
            jurl(j.url, "공식 저널") +
            "</details>",
        );
      continue;
    }
    const stats = Object.entries(c.sources)
      .map(
        ([n, s]) =>
          "<li>" +
          esc(n) +
          " · 보고 " +
          (s.reported == null ? "미확인" : num(s.reported)) +
          " · 수신 " +
          num(s.received) +
          " · " +
          num(s.pages) +
          "쪽 · " +
          (s.complete ? "API 끝 확인" : "API 끝 미확인") +
          (s.matched == null
            ? ""
            : " · 정식 저널명 일치 " +
              num(s.matched) +
              "건 · ISSN 조회에서 빠진 DOI " +
              num(s.outside_issn) +
              "건") +
          (s.errors.length ? " · " + esc(s.errors.join(" / ")) : "") +
          "</li>",
      )
      .join("");
    const audit = c.openalex_years || {};
    const kci = c.kci_years || j.kci?.snapshot;
    const publisherChecks = Object.entries(c.abstract_review?.publisher || {})
      .sort((a, b) => b[1] - a[1])
      .map(([reason, n]) => "<li>" + esc(reason) + " · " + num(n) + "편</li>")
      .join("");
    const sourceOnly = [
      ...new Set(Object.values(c.years).flatMap((y) => y.examples || [])),
    ];
    const archived = (d.archived.get(j.id) || [])
      .map(
        ([key, a]) =>
          "<li>" +
          esc(a.title || a.doi || key) +
          " · " +
          (a.linked_to
            ? jbutton("paper", a.linked_to, "연결한 정식 게재본")
            : jdate(jpdate(a)) +
              " · " +
              jurl("https://doi.org/" + a.doi, "수집 구간 밖 · 서지 확인")) +
          "</li>",
      )
      .join("");
    groups.get(j.field).push(
      "<details><summary>" +
        esc(j.name) +
        " <small>" +
        statuses[c.status] +
        " · " +
        num(c.total) +
        "건</small></summary>" +
        "<p>" +
        jurl(j.url, "공식 저널") +
        " · " +
        jurl(j.issn_evidence || d.registry.issn_source, "ISSN 근거") +
        " · 인쇄 " +
        esc(j.print_issn || "해당 없음") +
        " / 온라인 " +
        esc(j.online_issn || "미확인") +
        "</p>" +
        selection +
        (kci?.issues?.length
          ? "<details><summary>KCI 권호 목록 대조 · " +
            kci.issues.length +
            "개 호</summary><p>해당 권호의 KCI 목록과 PoliHub에 같은 권·호로 연결된 발행 기록 수를 비교합니다.</p><ul>" +
            kci.issues
              .map(
                (t) =>
                  "<li>" +
                  jurl(
                    t.url,
                    t.year + "년 " + t.volume + "권 " + t.issue + "호",
                  ) +
                  " · KCI " +
                  num(t.count) +
                  "건 / PoliHub " +
                  num(t.indexed) +
                  "건</li>",
              )
              .join("") +
            "</ul></details>"
          : "") +
        (kci
          ? "<p>" +
            jurl(j.kci.url, "한국연구재단 KCI 발행연도별 집계") +
            " · 확인 " +
            esc(kci.checked) +
            " · " +
            Object.entries(kci.counts)
              .filter(([y]) => c.years[y])
              .map(([y, n]) => esc(y) + "년 " + num(n) + "건")
              .join(" / ") +
            ". KCI는 권호 발행연도 전체를 집계합니다. 아래 발행 목록은 온라인 선출판일도 반영하며 첫해와 올해는 수집 구간에 해당하는 기간만 담습니다." +
            (kci.error
              ? " 최근 조회 " + esc(kci.attempted) + " · " + esc(kci.error)
              : "") +
            (kci.recent
              ? " 이번에 확인한 KCI 검색 첫 화면의 논문 링크 " +
                num(kci.recent.listed) +
                "건 · 개별 서지 수신 " +
                num(kci.recent.received) +
                "건 · 이미 대조한 KCI ID " +
                num(kci.recent.known) +
                "건." +
                (kci.recent.errors.length
                  ? " " + esc(kci.recent.errors.join(" / "))
                  : "")
              : "") +
            "</p>"
          : "") +
        "<p>최근 점검 " +
        esc(c.checked) +
        " · Crossref 마지막 성공 " +
        esc(c.last_success || "없음") +
        " · 출판사 전체 목차 대조 " +
        (c.publisher_toc.complete ? "완료" : "미완료") +
        "</p><ul>" +
        stats +
        "</ul>" +
        '<div class="table-wrap"><table class="data-table"><thead><tr><th>연도</th><th>전체 발행 기록</th><th>초록이 있는 발행 기록</th><th>Crossref DOI</th><th>OpenAlex DOI</th><th>공통 DOI</th><th>OpenAlex에만</th><th>목차 연결</th><th>OpenAlex 연도별 집계</th><th>방법 집계 대상의 초록 확인 / 대상 논문</th></tr></thead><tbody>' +
        Object.entries(c.years)
          .map(
            ([y, t]) =>
              "<tr>" +
              [
                y,
                t.total,
                t.abstracts,
                t.crossref,
                t.openalex,
                t.both,
                t.only_openalex,
                c.publisher_toc.counts[y] || 0,
                audit.counts?.[y] ??
                  (audit.scope?.startsWith("선택 기간") ? 0 : "미확인"),
                t.eligible == null
                  ? "미집계"
                  : num(t.eligible_abstracts) +
                    " / " +
                    num(t.eligible) +
                    (t.eligible
                      ? " (" + percent(t.eligible_abstracts, t.eligible) + ")"
                      : ""),
              ]
                .map(
                  (v, i) =>
                    "<td>" +
                    (i >= 3 &&
                    i <= 6 &&
                    !(i === 3
                      ? c.sources.Crossref.complete
                      : i === 4
                        ? c.sources.OpenAlex.complete
                        : c.sources.Crossref.complete &&
                          c.sources.OpenAlex.complete)
                      ? "미완료 · "
                      : "") +
                    esc(v) +
                    "</td>",
                )
                .join("") +
              "</tr>",
          )
          .join("") +
        "</tbody></table></div>" +
        (c.abstract_review
          ? "<p>이번에 초록을 새로 확인한 논문 " +
            num(c.abstract_review.gained || 0) +
            "편</p>"
          : "") +
        (publisherChecks
          ? "<details><summary>공식 저널 초록 조회 결과</summary>" +
            (c.abstract_review.publisher_reused
              ? "<p>이 중 " +
                num(c.abstract_review.publisher_reused) +
                "편은 같은 날 확인한 게재처의 응답을 사용했습니다.</p>"
              : "") +
            "<ul>" +
            publisherChecks +
            "</ul></details>"
          : "") +
        '<p class="muted">OpenAlex 연도별 집계 범위 · ' +
        esc(audit.scope || "별도 조회 없음") +
        (audit.errors?.length ? " · " + esc(audit.errors.join(" / ")) : "") +
        " · 한 출처에서만 수신한 DOI는 대조 후보입니다. 출판사 누락 규모는 미확인입니다.</p>" +
        (sourceOnly.length
          ? "<p>OpenAlex 차집합 표본 · " +
            sourceOnly
              .slice(0, 5)
              .map((doi) => jurl("https://doi.org/" + doi, doi))
              .join(" · ") +
            "</p>"
          : "") +
        (archived
          ? "<details><summary>같은 논문으로 연결했거나 기간 밖으로 확인한 기록</summary><ul>" +
            archived +
            "</ul></details>"
          : "") +
        jbutton("journal", j.id, "이 저널의 발행 목록") +
        "</details>",
    );
  }
  document.getElementById("jdir-list").innerHTML =
    [...groups]
      .map(
        ([f, rows]) =>
          "<section><h3>" +
          esc(f) +
          " · " +
          rows.length +
          "종</h3>" +
          rows.join("") +
          "</section>",
      )
      .join("") || "<p>일치하는 저널이 없습니다.</p>";
}
function journalPaper(key) {
  const d = journalData,
    r = d.byKey.get(key);
  if (!r) return;
  const a = r.a;
  const proof = a.abstract_proof;
  const scholar = a.title
    ? "https://scholar.google.com/scholar?q=" +
      encodeURIComponent('"' + a.title.replace(/["“”]/g, "") + '"')
    : "";
  const abstract =
    "<h3>논문 초록</h3><p>" +
    (a.has_abstract
      ? proof?.url
        ? jurl(proof.url, proof.source + "에서 초록 보기") +
          " · " +
          esc(proof.location) +
          (proof.checked ? " · " + esc(proof.checked) + " 확인" : "")
        : "기존 수집에서 확인 · 초록의 세부 출처 미기록"
      : a.publisher_statement
        ? "출판사가 별도 초록을 제공하지 않는다고 표시했습니다."
        : "초록 미확인") +
    "</p>" +
    (a.publisher_statement
      ? "<p>" +
        jurl(a.publisher_statement.url, a.publisher_statement.label) +
        "</p>"
      : "") +
    (a.superseded_abstract && !a.has_abstract
      ? "<p>이전에 수록한 메타데이터는 출판사의 초록란과 대조한 뒤 확인 집계에서 제외했습니다.</p>"
      : "") +
    (a.abstract_summary
      ? "<p><b>초록 요약</b> · " + esc(a.abstract_summary) + "</p>"
      : "") +
    (!a.has_abstract && a.abstract_check
      ? "<details><summary>" +
        esc(a.abstract_check.date) +
        " 조회 기록</summary><ul>" +
        Object.entries(a.abstract_check.sources)
          .map(
            ([name, reason]) =>
              "<li>" + esc(name) + " · " + esc(reason) + "</li>",
          )
          .join("") +
        "</ul></details>"
      : "") +
    (scholar
      ? "<p>" + jurl(scholar, "Google Scholar에서 제목 검색") + "</p>"
      : "");
  const facts = [
    ["저널", jurl(r.j.url, r.j.name)],
    ["저자", esc((a.authors || []).join(", ") || "미확인")],
    ...(a.other_authors?.length
      ? [["저자의 다른 언어 표기", esc(a.other_authors.join(", "))]]
      : []),
    ...(a.other_titles?.some((t) => t !== a.title)
      ? [
          [
            "다른 언어 표제",
            a.other_titles
              .filter((t) => t !== a.title)
              .map(esc)
              .join(" · "),
          ],
        ]
      : []),
    ["발행일 · 정밀도", jdate(r.date)],
    ["온라인 선출판", jdate(a.online)],
    ["권호 발행", jdate(a.issue_date)],
    [
      "권 · 호 · 면",
      esc([a.volume, a.issue, a.pages].map((v) => v || "—").join(" · ")),
    ],
    ["최초 발견", esc(a.first_seen)],
    [
      "발행 유형",
      esc(
        a.kind === "notice"
          ? (a.notice_type || "비연구물") + " · 방법 집계 제외"
          : a.kind === "journal-article"
            ? "저널 발행물 · 세부 유형 미확인"
            : a.kind,
      ) +
        (a.genre_proof
          ? " · " + jurl(a.genre_proof.url, a.genre_proof.location)
          : ""),
    ],
    ["DOI", a.doi ? jurl("https://doi.org/" + a.doi, a.doi) : "미확인"],
  ];
  const ev = (a.methods || [])
    .map(
      (e) =>
        "<li><b>" +
        esc(
          d.paths
            .get(e.method)
            .map((k) => d.methods.get(k).label)
            .join(" › "),
        ) +
        "</b><p>" +
        esc(e.term) +
        " · " +
        esc(e.role) +
        " · " +
        journalLevels[e.level] +
        "</p><p>" +
        esc(e.evidence) +
        " · " +
        jurl(e.url, e.location) +
        "</p></li>",
    )
    .join("");
  const refs = (a.resources || [])
    .map(
      (x) =>
        "<li>" +
        jurl(x.url, x.kind) +
        (x.registered ? " · 자료 등록 " + esc(x.registered) : "") +
        " · " +
        esc(x.source || "") +
        "</li>",
    )
    .join("");
  openDialog(
    a.title || a.doi || "제목 미확인",
    "<p>" +
      [
        a.publisher_url || a.url
          ? jurl(a.publisher_url || a.url, "논문 원문·출판사")
          : "",
        a.toc_url ? jurl(a.toc_url, "권호 목차") : "",
      ]
        .filter(Boolean)
        .join(" · ") +
      '</p><dl class="facts">' +
      facts.map(([k, v]) => "<dt>" + k + "</dt><dd>" + v + "</dd>").join("") +
      "</dl>" +
      abstract +
      "<h3>저자 키워드</h3><p>" +
      ((a.keywords || []).map((t) => "#" + esc(t)).join(" ") || "미확인") +
      "</p><h3>방법론 근거</h3>" +
      (ev ? "<ul>" + ev + "</ul>" : "<p>미확인</p>") +
      "<h3>재현 패키지·관련 자료</h3>" +
      (refs ? "<ul>" + refs + "</ul>" : "<p>확인된 링크 없음</p>") +
      "<h3>서지 출처</h3><p>" +
      (a.sources || [])
        .map((n) =>
          jurl(
            n === "Crossref" && a.doi
              ? "https://api.crossref.org/works/" + a.doi
              : n === "OpenAlex"
                ? a.source_ids?.find((x) =>
                    x.startsWith("https://openalex.org/"),
                  )
                : n === "KCI · 공개 서지"
                  ? a.kci_url ||
                    (a.abstract_proof?.source?.includes("KCI")
                      ? a.abstract_proof.url
                      : a.url)
                  : n.includes("공식 목차")
                    ? a.toc_url
                    : d.sources[n],
            n,
          ),
        )
        .join(" · ") +
      "</p>",
  );
}
document.addEventListener("submit", (e) => {
  if (e.target.id !== "journal-search") return;
  e.preventDefault();
  jf.q = new FormData(e.target).get("q").trim();
  jf.page = 1;
  renderJournalResults();
});
document.addEventListener("input", (e) => {
  if (e.target.id === "jdir-q") {
    jf.directory = e.target.value;
    renderDirectory();
  }
});
document.addEventListener("change", (e) => {
  const k = e.target.dataset.jfilter;
  if (!k) return;
  jf[k] = k === "packages" ? e.target.checked : e.target.value;
  jf.page = 1;
  if (k === "interest") {
    const members = journalData.interestJournals.get(jf.interest);
    if (members && !members.has(jf.journal)) jf.journal = "";
    jf.directory = "";
    journalPage();
    return;
  }
  renderJournalResults();
});
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-jour]");
  if (!b) return;
  const k = b.dataset.jour,
    v = b.dataset.value;
  if (k === "paper") return journalPaper(v);
  if (k === "directory") {
    const el = document.getElementById("jdir");
    el.open = true;
    el.scrollIntoView();
    return;
  }
  if (k === "list") return document.getElementById("jpapers").scrollIntoView();
  if (k === "reset") {
    Object.assign(jf, {
      q: "",
      interest: "",
      directory: "",
      field: "",
      journal: "",
      year: "",
      level: "",
      abstract: "",
      method: "",
      packages: false,
      page: 1,
    });
    journalPage();
    return;
  }
  if (k === "journal") {
    jf.journal = v;
    jf.field = "";
    jf.page = 1;
    journalPage();
    document.getElementById("main").scrollIntoView();
    return;
  }
  if (k === "method") {
    jf.method = v;
    jf.page = 1;
  }
  if (k === "page") jf.page = Number(v);
  renderJournalResults();
  if (k === "page") document.getElementById("jpapers").scrollIntoView();
});
