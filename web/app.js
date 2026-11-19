"use strict";
const $ = (s) => document.querySelector(s);
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const icons = {
  folder: '<path d="M3 6h6l2 2h10v11H3z"/>',
  search: '<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/>',
  election:
    '<path d="m8 6 5-4 5 6-5 4zM5 11l-2 4v6h18v-6l-2-4M3 15h18M8 12h3"/>',
  attitudes: '<path d="M3 4h15v10H8l-4 4v-4H3zM10 17h6l4 3v-4h1V8"/>',
  legislature:
    '<path d="m2 8 10-5 10 5zM3 20h18M5 17h14M6 10v5m4-5v5m4-5v5m4-5v5"/>',
  public:
    '<path d="M3 21h18M5 21V6l7-3 7 3v15M9 8h1m4 0h1m-6 4h1m4 0h1m-5 9v-5h4v5"/>',
  policy:
    '<path d="M3 20h18M5 17v-5h3v5m3 0V9h3v8m3 0V5h3v12M3 8l6-4 3 1 5-3"/>',
  world:
    '<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18M5 6.5q7 4 14 0M5 17.5q7-4 14 0"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  check: '<path d="m4 12 5 5L20 6"/>',
  arrow: '<path d="M4 12h15m-6-6 6 6-6 6"/>',
  external: '<path d="M8 5h11v11m0-11L5 19"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v4h16v-4"/>',
  filter:
    '<path d="M3 7h7m4 0h7M3 17h11m4 0h3"/><circle cx="12" cy="7" r="2"/><circle cx="16" cy="17" r="2"/>',
  close: '<path d="m5 5 14 14M19 5 5 19"/>',
  book: '<path d="M3 4q5-1 9 2 4-3 9-2v14q-5-1-9 2-4-3-9-2zM12 6v14"/>',
  compare: '<path d="M3 5h7v14H3zM14 5h7v14h-7z"/>',
};
const icon = (k) =>
  ' <svg class="icon" aria-hidden="true" viewBox="0 0 24 24">' +
  (icons[k] || "") +
  "</svg>";
function saved(k, def) {
  try {
    return JSON.parse(localStorage.getItem("polihub." + k)) ?? def;
  } catch {
    return def;
  }
}
function save(k, v) {
  try {
    localStorage.setItem("polihub." + k, JSON.stringify(v));
  } catch {
    /* private browsing */
  }
}
let C,
  S = [],
  live = false,
  active = saved("project", null),
  history = saved("history", []),
  selected = new Set(saved("selection", [])),
  busy = false;
let question = saved("question", ""),
  engine = saved("engine", "r"),
  view = "explore",
  filterOpen = false,
  showAll = false,
  limit = 7,
  query = "";
let filters = {
  category: "",
  region: "",
  panel: "",
  unit: "",
  design: "",
  access: "",
  format: "",
  from: "",
  to: "",
  comparison: "",
};
const release = (d) => C.releases[d.default_release];
const ds = (i) => C.datasets[i];
const accessText = {
  direct: "원자료 다운로드",
  api: "연결 후 다운로드",
  application: "등록·신청",
  review: "접근조건 확인",
};
const validUrl = (u) => typeof u === "string" && u.startsWith("https://");
const link = (u, name) =>
  validUrl(u)
    ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(name)} ${icon("external")}</a>`
    : esc(name);
const btn = (a, label, cls = "secondary", extra = "") =>
  `<button type="button" class="${cls}" data-action="${a}" ${extra}>${label}</button>`;
function announce(text) {
  $("#toast").textContent = text;
  clearTimeout(announce.timer);
  announce.timer = setTimeout(() => ($("#toast").textContent = ""), 4200);
}
function choose(i) {
  if (selected.has(i)) selected.delete(i);
  else selected.add(i);
  save("selection", [...selected]);
  repaint();
}
function count() {
  $("#selected-count").textContent = selected.size;
}
function openDialog(title, body, cls = "") {
  const dlg = $("#modal");
  $("#modal-body").innerHTML =
    `<div class="dialog-top"><h2>${esc(title)}</h2><button class="close-btn" data-action="close" aria-label="닫기">${icon("close")}</button></div><div class="${cls}">${body}</div>`;
  if (!dlg.open) dlg.showModal();
}
function toastError(e) {
  announce(e.message || String(e));
}
function actionLink(d) {
  const r = release(d),
    f = C.files[r.files[0]];
  if (r.access === "direct" && f)
    return btn(
      "download-data",
      icon("download") + "원자료 다운로드",
      "access direct",
      'data-id="' + d.id + '"',
    );
  return btn(
    "detail",
    esc(accessText[r.access]) + icon("external"),
    "access",
    'data-id="' + d.id + '"',
  );
}
function pick(d) {
  let on = selected.has(d.id);
  return btn(
    "pick",
    icon(on ? "check" : "plus") + (on ? "선택됨" : "선택"),
    "select-data" + (on ? " is-on" : ""),
    'aria-label="' +
      esc(d.name) +
      ' 선택" aria-pressed="' +
      on +
      '" data-id="' +
      d.id +
      '"',
  );
}
function regionText(d) {
  const r = release(d);
  if (d.scope === "international") return "국제비교";
  return r.regions.map((x) => C.regions[x]).join("·");
}
function card(d) {
  return `<article class="card"><div class="cardtop"><span class="category ${d.category}">${esc(C.categories[d.category])}</span>${pick(d)}</div>
 <h3><button data-action="detail" data-id="${d.id}">${esc(d.name)}</button></h3><p>${esc(d.summary)}</p>
 <div class="card-end"><div class="meta"><span>${esc(regionText(d))}</span><span>${esc(C.designs[d.design])}</span><span>${esc(d.unit)}</span></div>${actionLink(d)}</div></article>`;
}
function options(choices, v, all = "전체") {
  return (
    `<option value="">${esc(all)}</option>` +
    Object.entries(choices)
      .map(
        ([k, n]) =>
          `<option value="${esc(k)}" ${v === k ? "selected" : ""}>${esc(n)}</option>`,
      )
      .join("")
  );
}
function field(k, label, choices) {
  return `<label>${label}<select data-filter="${k}">${options(choices, filters[k])}</select></label>`;
}
function norm(v) {
  return String(v).toLowerCase().normalize("NFKC");
}
function tokens(s) {
  return (norm(s).match(/[a-z0-9가-힣]+/g) || []).filter((t) => t.length > 1);
}
function score(d) {
  let title = norm([d.name, ...d.aliases].join(" ")),
    txt = norm(
      [
        d.summary,
        ...d.topics,
        ...release(d).regions.map((k) => C.regions[k]),
        C.categories[d.category],
      ].join(" "),
    );
  let n = 0;
  for (const word of tokens(query)) {
    if (title.includes(word)) n += 8;
    if (txt.includes(word)) n += 5;
    if (/[가-힣]/.test(word) && word.length > 2) {
      for (let i = 0; i < word.length - 1; i++) {
        let p = word.slice(i, i + 2);
        if (title.includes(p) || txt.includes(p)) n += 1;
      }
    }
  }
  return n;
}
function resultList() {
  let activeFilter = Object.values(filters).some(Boolean),
    filtered = activeFilter || query || showAll;
  let rows = Object.values(C.datasets).filter((d) => {
    const r = release(d),
      f = filters;
    return (
      (!f.category || d.category === f.category) &&
      (!f.region || r.regions.includes(f.region)) &&
      (!f.design || f.design === d.design) &&
      (!f.unit || f.unit === d.unit) &&
      (!f.panel || (f.panel === "yes") === d.individual_panel) &&
      (!f.access || r.access === f.access) &&
      (!f.format ||
        (f.format === "native"
          ? r.formats.some((x) => ["dta", "rds", "rdata", "sav"].includes(x))
          : r.formats.includes(f.format))) &&
      (!f.from || (r.years && r.years[1] >= Number(f.from))) &&
      (!f.to || (r.years && r.years[0] <= Number(f.to))) &&
      (!f.comparison ||
        (f.comparison === "confirmed"
          ? d.id === "ert"
          : f.comparison === "common"
            ? ["wvs", "cses", "issp", "ert"].includes(d.id)
            : d.comparison_priority === 2)) &&
      (!query || score(d) > 0)
    );
  });
  if (!filtered) rows = C.featured.map(ds);
  else if (query) rows.sort((a, b) => score(b) - score(a));
  else if (filters.comparison)
    rows.sort((a, b) => (a.id === "ert" ? -1 : 0) - (b.id === "ert" ? -1 : 0));
  const title = query
    ? "연구질문에 맞는 자료"
    : filtered
      ? "탐색 결과"
      : "대표자료";
  return `<div class="section-title"><h2>${title}<small>${rows.length}개</small></h2>${filtered ? btn("reset", "조건 초기화", "text-btn") : btn("all", "전체 " + Object.keys(C.datasets).length + "개 보기 " + icon("arrow"), "text-btn")}</div>
 ${query ? '<p class="muted">자료명·연구분야·정치학 개념의 일치도를 기준으로 가까운 자료부터 표시합니다.</p>' : ""}
 ${rows.length ? '<div class="grid">' + rows.slice(0, limit).map(card).join("") + "</div>" : '<div class="empty"><h3>조건에 맞는 자료가 없습니다.</h3><p>기간이 확인되지 않은 배포판은 연도 검색에서 제외됩니다. 국가나 분야 조건을 조정해 보세요.</p></div>'}
 ${rows.length > limit ? '<div class="more-row">' + btn("more", "자료 더 보기 " + icon("plus")) + "</div>" : ""}`;
}
function explore() {
  const pins = [
    ["CN", "중국", 33.4, 42.4],
    ["KR", "한국", 55.2, 40.8],
    ["JP", "일본", 75.5, 49.7],
    ["TW", "대만", 42.9, 80.6],
  ];
  const regionButtons = [["", "전체"], ...Object.entries(C.regions)]
    .map(([v, n]) =>
      btn(
        "region",
        esc(n),
        "chip" + (filters.region === v ? " is-on" : ""),
        'data-region="' + v + '" aria-pressed="' + (filters.region === v) + '"',
      ),
    )
    .join("");
  return `<section class="hero" aria-label="연구자료 검색"><div class="hero-map" role="group" aria-label="동아시아 지도에서 지역 선택">${pins.map(([v, n, x, y]) => btn("region", n, "map-pin" + (filters.region === v ? " is-on" : ""), 'data-region="' + v + '" style="left:' + x + "%;top:" + y + '%" aria-label="' + n + ' 자료 보기"')).join("")}<span class="map-credit">Natural Earth · East Asia</span></div>
 <div class="hero-copy"><h1>정치학 비교연구<span>데이터 허브</span></h1><p>한국과 세계의 정치학 자료를 찾아, 수집부터 R·Stata 분석까지 연결하세요.</p>
 <form id="search-form" class="search">${icon("search")}<input name="query" aria-label="연구질문 또는 자료명" placeholder="연구질문, 자료명 또는 키워드" value="${esc(query)}"><button>검색</button></form></div></section>
 <section aria-labelledby="fields-title"><div class="section-title"><h2 id="fields-title">연구분야로 탐색</h2></div><div class="fields">${Object.entries(
   C.categories,
 )
   .map(([v, n]) =>
     btn(
       "category",
       icon(v) + "<span>" + esc(n) + "</span>",
       "field" + (filters.category === v ? " is-on" : ""),
       'data-field="' + v + '" aria-pressed="' + (filters.category === v) + '"',
     ),
   )
   .join("")}</div></section>
 <section class="filters" aria-label="검색 필터"><div class="filterline"><strong>대상 지역</strong>${regionButtons}${btn("filter", icon("filter") + "상세 필터", "morefilter", 'aria-expanded="' + filterOpen + '"')}</div>
 ${filterOpen ? `<div class="filtergrid">${field("panel", "개인 추적 패널", { yes: "포함", no: "그 외" })}${field("design", "자료설계", C.designs)}${field("unit", "분석단위", Object.fromEntries([...new Set(Object.values(C.datasets).map((d) => d.unit))].map((x) => [x, x])))}${field("access", "접근 방식", accessText)}${field("format", "배포 형식", { native: "R·Stata 등 통계 파일", dta: "Stata DTA", rds: "RDS", rdata: "RData", csv: "CSV" })}<label>시작 연도<input class="form-input" type="number" min="1700" max="2200" data-filter="from" value="${esc(filters.from)}"></label><label>종료 연도<input class="form-input" type="number" min="1700" max="2200" data-filter="to" value="${esc(filters.to)}"></label>${field("comparison", "동아시아–서양 비교", { confirmed: "양쪽 국가·지표 확인", common: "동일 프로젝트 우선", module: "공통 모듈 검토" })}</div><p class="muted">지역은 조사 대상 기준입니다. 국제자료의 개별 국가는 배포판에서 확인한 경우에 표시합니다.</p>` : ""}</section>
 <section id="results" aria-label="자료 목록">${resultList()}</section>`;
}
function details(i) {
  const d = ds(i),
    r = release(d),
    f = C.files[r.files[0]],
    curr = active?.status?.datasets?.[i],
    vars = C.variables.filter((v) => v.dataset === i);
  const countryList = Object.entries(r.countries)
    .map(([v, yr]) => v + " " + yr.join("–"))
    .join(" · ");
  const dt = (n, x) => "<dt>" + esc(n) + "</dt><dd>" + x + "</dd>";
  openDialog(
    d.name,
    `<p>${esc(d.summary)}</p><div class="actions">${pick(d)}${f ? actionLink(d) : link(d.source_url, accessText[r.access])}${r.documentation.map((doc) => link(doc.url, "코드북·지침")).join(" ")}</div>
 <dl class="facts">${dt("제작·배포", esc(d.producer) + (d.original_source ? " · 원출처 " + esc(d.original_source) : ""))}
 ${dt("자료설계", esc(C.designs[d.design] + " · " + d.unit))}${dt("조사대상", esc(d.population))}${dt("프로젝트 지역", esc(d.project_regions.map((k) => C.regions[k]).join(" · ")))}
 ${dt("배포판·공개일", esc((r.version?.length === 40 ? r.version.slice(0, 12) : r.version) || "배포판 확인 필요") + " · " + esc(r.release_date || "공개일 확인 필요"))}
 ${dt("시기·차수", esc([r.years?.join("–"), r.waves].filter(Boolean).join(" · ") || "확인 필요"))}${dt("형식·언어", esc([...r.formats.map((x) => x.toUpperCase()), ...r.languages, r.encoding].filter(Boolean).join(" · ") || "배포파일 확인 필요"))}
 ${dt("주요 주제", esc(d.topics.join(" · ")))}${dt("표본·가중치", esc([r.mode, r.weight, r.strata, r.cluster].filter(Boolean).join(" · ") || "배포판 설계 지침 확인 필요"))}
 ${dt("접근·이용조건", esc(r.access_note) + " " + esc(r.terms || "인용·재배포 조건은 배포처에서 확인."))}${dt("확인 단계", esc(C.stage_labels[r.download_stage]) + " · " + esc(r.checked))}
 ${dt("현재 연구 상태", esc(curr ? [curr.acquisition, curr.import, curr.validation].map(label).join(" / ") : "이 작업공간에서 미실행"))}</dl>
 <div class="notice">${esc(d.caution)}</div>
 <details><summary>실제 수록국과 기간</summary><p>${esc(r.geography_basis)}</p><p>${esc(countryList || "개별 배포판의 국가·기간 미확인")}</p></details>
 <details><summary>변수와 확인 근거</summary>${vars.map((v) => "<p><code>" + esc(v.name) + "</code> · " + esc(v.concept) + " · " + link(v.evidence, v.location) + "</p>").join("") || "<p>코드북에서 변수명을 확인해야 합니다.</p>"}
 <div class="chips">${r.evidence.map((x) => link(x, "공식 출처")).join("")}</div>${f ? "<p>" + esc(f.name) + " · " + f.bytes.toLocaleString() + " bytes</p><pre>SHA-256 " + esc(f.sha256) + "</pre>" : ""}</details>
 ${f ? "" : btn("import-nav", "내 파일 가져오기", "secondary", 'data-id="' + i + '"')}`,
  );
}
function compare() {
  if (selected.size < 2 || selected.size > 4) {
    announce("비교할 자료를 2~4개 선택하세요.");
    return;
  }
  const items = [...selected].map(ds),
    props = [
      ["조사대상", (d) => d.population],
      [
        "대상국·기간",
        (d) =>
          regionText(d) +
          " · " +
          (release(d).years?.join("–") || "기간 확인 필요"),
      ],
      ["분석단위", (d) => d.unit],
      ["정치학 개념", (d) => d.topics.join(" · ")],
      ["자료설계", (d) => C.designs[d.design]],
      ["표본·가중치", (d) => release(d).weight || "코드북 확인 필요"],
      ["접근조건", (d) => release(d).access_note],
      ["비교 시 주의", (d) => d.caution],
    ];
  openDialog(
    "선택한 자료 비교",
    `<div class="table-wrap"><table class="data-table"><thead><tr><th>항목</th>${items.map((d) => "<th>" + esc(d.name) + "</th>").join("")}</tr></thead><tbody>${props.map(([n, fn]) => "<tr><th>" + n + "</th>" + items.map((d) => "<td>" + esc(fn(d)) + "</td>").join("") + "</tr>").join("")}</tbody></table></div><p class="notice">서로 다른 조사 간 공통문항·모집단·번역·척도 대응은 연구별 비교 보고서에서 검토하세요.</p>`,
    "compare",
  );
}
function makeSpec() {
  return {
    schema_version: 1,
    question,
    objective: null,
    population: null,
    unit: null,
    engine,
    seed: 2026,
    datasets: [...selected].map((i) => ({
      id: i,
      release: ds(i).default_release,
      version: release(ds(i)).version,
      countries: [],
      period: null,
    })),
    analyses: [],
    references: [],
    variable_definitions: [],
    comparisons: [],
    design_review: {},
    join_checks: [],
    assumptions: [],
    sensitivity: [],
  };
}
function downloadJSON(name, v) {
  const b = new Blob([JSON.stringify(v, null, 2) + "\n"], {
    type: "application/json;charset=utf-8",
  });
  const u = URL.createObjectURL(b),
    a = document.createElement("a");
  a.href = u;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(u), 1500);
}
function label(v) {
  return (
    {
      received: "수집 완료",
      waiting: "대기",
      failed: "실패",
      passed: "가져오기 완료",
      checked: "점검 통과",
      blocked: "분석 차단",
      unavailable: "엔진 없음",
      generated: "스크립트 생성",
      not_acquired: "미수집",
      hash_mismatch: "해시 불일치",
    }[v] ||
    v ||
    "미실행"
  );
}
function current() {
  return active &&
    JSON.stringify(active.spec.datasets.map((x) => x.id)) ===
      JSON.stringify([...selected])
    ? active
    : null;
}
function artifact(key) {
  return `<a target="_blank" rel="noopener" href="/api/studies/${active.id}/file?name=${encodeURIComponent(key)}">${esc(key)}</a>`;
}
function work() {
  const a = current(),
    status = a?.status,
    rows = status?.collected || {},
    doctorTool = btn("doctor", "환경 확인");
  return `<div class="page-intro"><h2 class="page-title">연구 작업공간</h2><p>선택한 배포판을 한 작업으로 수집하고, 자료별 검증과 분석 상태를 확인하세요.</p></div>
 <div class="work-grid"><section class="surface"><div class="work-head spread"><h3>선택한 자료 · ${selected.size}</h3>${btn("compare", icon("compare") + " 자료 비교", "secondary", selected.size >= 2 && selected.size <= 4 ? "" : "disabled")}</div>
 <ul class="work-list">${[...selected].map((i) => `<li><div>${btn("detail", esc(ds(i).name), "text-btn", 'data-id="' + i + '"')}<br><small>${esc(regionText(ds(i)) + " · " + accessText[release(ds(i)).access])}</small></div>${btn("pick", "제거", "text-btn", 'data-id="' + i + '" aria-label="' + esc(ds(i).name) + ' 제거"')}</li>`).join("") || "<li>자료 탐색에서 연구에 사용할 자료를 선택하세요.</li>"}</ul>
 <label class="control">연구질문<textarea class="question-box" id="work-question" placeholder="수집·가져오기만 하려면 비워 두세요.">${esc(question)}</textarea></label><label class="control">통계 엔진<select id="engine"><option value="r">R</option><option value="stata" ${engine === "stata" ? "selected" : ""}>Stata</option></select></label>
 <div class="actions">${btn("pipeline", busy ? "처리 중…" : "일괄 수집·가져오기", "primary", !selected.size || !live || busy ? "disabled" : "")}${btn("spec", "연구명세 내려받기", "secondary", selected.size ? "" : "disabled")}${btn("compare", icon("compare") + " 비교표", "text-btn", selected.size >= 2 && selected.size <= 4 ? "" : "disabled")}</div>
 ${busy ? '<p class="busy" role="status">파일과 엔진 로그를 확인하고 있습니다.</p>' : ""}
 ${!live ? '<p class="notice">이 화면에서는 카탈로그를 탐색하고 연구명세를 내려받을 수 있습니다. 로컬 실행기를 시작하면 일괄 수집·파일 등록·엔진 실행이 활성화됩니다.</p>' : ""}
 </section><aside class="surface"><h3>Codex·CLI로 실행</h3><p>내려받은 <code>study.json</code>을 연구별 폴더에 저장한 뒤, 허브 폴더에서 실행하세요.</p><pre>python -m polihub pipeline \\\n  --spec studies/my/study.json \\\n  --engine ${engine}</pre><p>연구질문과 분석계획이 준비되면 같은 명세로 결과표와 검증표를 생성합니다.</p>${doctorTool}</aside></div>
 <section class="surface"><h3>실행 결과</h3>${
   status
     ? `<div class="table-wrap work-output"><table class="data-table"><thead><tr><th>자료</th><th>수집</th><th>가져오기</th><th>검증</th></tr></thead><tbody>${Object.entries(
         rows,
       )
         .map(
           ([i, v]) =>
             `<tr><td>${esc(ds(i).name)}</td><td>${esc(label(v.status))}${v.reason ? "<br><small>" + esc(v.reason) + "</small>" : ""}</td><td>${esc(label(status.datasets[i]?.import))}</td><td>${esc(label(status.validation[i]))}</td></tr>`,
         )
         .join(
           "",
         )}</tbody></table></div><p>분석 · ${esc(status.analysis)}　원문 대조 · ${esc(status.reproduction || "미실행")}</p><div class="chips">${[
         "study.json",
         "acquisition_manifest.json",
         "validation_report.json",
         "comparability_report.md",
         "variable_map.csv",
         "outputs/report_ko.md",
         "outputs/results.csv",
         "outputs/verification.csv",
         "outputs/artifact_manifest.json",
       ]
         .filter((n) => status.artifacts.includes(n) || n === "study.json")
         .map(artifact)
         .join("")}</div>`
     : '<p class="hint">작업을 실행하면 자료별 수집·가져오기·검증 결과가 표시됩니다.</p>'
 }</section>
 ${
   selected.size
     ? `<section class="surface"><details id="upload-details"><summary>내 파일 가져오기</summary><p>정식 절차로 받은 배포 파일을 원본 상태로 등록하세요. ZIP에 표가 여러 개 있으면 대상 파일의 경로를 입력하세요.</p><div class="filtergrid"><label>자료<select id="upload-id">${[...selected].map((i) => '<option value="' + i + '">' + esc(ds(i).name) + "</option>").join("")}</select></label><label>실제 배포 버전<input id="upload-version" class="form-input" placeholder="배포처의 버전"></label><label>ZIP 내부 파일 (선택)<input id="upload-member" class="form-input"></label><label>CSV 인코딩<select id="upload-encoding"><option>utf-8-sig</option><option>cp949</option><option>euc-kr</option><option>latin1</option></select></label></div><input id="upload" type="file" accept=".csv,.tsv,.sav,.dta,.zip,.rdata,.rds"> ${btn("upload", "파일 등록", "secondary", !live || busy ? "disabled" : "")}</details>
 <details><summary>연구명세·분석계획 편집</summary><p>변수사전 근거와 표본 규칙을 지정하세요. 원문값·표본 수·허용오차는 실행 전에 기록합니다.</p><textarea id="plan-edit" aria-label="연구명세 JSON">${esc(JSON.stringify(a?.spec || makeSpec(), null, 2))}</textarea><div class="actions">${btn("save-plan", "연구명세 저장", "secondary", !live || busy ? "disabled" : "")}${btn("run", "저장한 계획 실행", "primary", !a || !live || busy ? "disabled" : "")}</div></details></section>`
     : ""
 }
 ${history.length ? '<details class="surface"><summary>최근 연구 프로젝트</summary>' + history.map((p) => "<p>" + btn("restore", esc(p.title), "text-btn", 'data-project="' + esc(p.id) + '"') + "</p>").join("") + "</details>" : ""}`;
}
function guide() {
  const outcomes = {};
  for (const s of S) outcomes[s.status] = (outcomes[s.status] || 0) + 1;
  return `<div class="page-intro"><h2 class="page-title">이용 안내</h2><p>자료를 찾는 단계부터 원문 수치와 대조하는 단계까지, 근거와 실행 기록을 따라갈 수 있습니다.</p></div>
 <div class="guide-contents"><section class="surface"><h3>자료 선택과 실행</h3><ol class="flowsteps"><li>연구질문으로 검색하고 자료명에서 표본·기간·접근조건을 확인합니다.</li><li>2~4개 자료의 비교표를 보거나 작업공간에 여러 자료를 담습니다.</li><li>공개파일은 일괄 수집하고 신청 자료는 배포처에서 받은 파일을 등록합니다.</li><li>연구명세에 분석계획을 작성하면 엔진이 가용한 범위에서 실행합니다.</li></ol></section>
 <section class="surface"><h3>로컬 실행기</h3><pre>python -m pip install -r requirements.txt\npython -m polihub doctor\npython -m polihub serve</pre><p>로컬·원격·클라우드에서 실행기가 확인하는 R·Stata와 파일은 각 환경의 것입니다. Stata 배치 실행에는 해당 환경의 라이선스가 필요합니다.</p>${btn("doctor", "현재 환경 확인", "secondary")}</section>
 <section class="surface"><h3>코드북과 국제비교</h3><p>같은 국제조사의 공통문항, 국가별 공통 모듈, 다른 조사 간 대응 순으로 검토합니다. 사례별 모집단·번역·척도·시점을 비교 보고서에 남깁니다.</p><p>반복횡단면의 응답자는 매번 달라질 수 있습니다. 패널에서는 개인과 차수의 조합을 점검합니다.</p></section>
 <section class="surface"><h3>수집과 재현 판정</h3><p>공식 링크 발견, 응답 형식, 전체 수신, 데이터 읽기, 변수·표본 확인을 따로 기록합니다. 수치 재현은 사전 허용오차·단위·표본 수와 원자료 버전을 대조합니다.</p><p>이 판본의 직접 파일 ${Object.keys(C.files).length}개에는 SHA-256과 파일 크기가 있습니다.</p></section>
 <details class="surface full"><summary>출처 점검 기록 · ${S.length}개 경로</summary><p>열람 ${S.filter((s) => ["page_read", "document_read", "data_read"].includes(s.status)).length}개 · 네트워크 차단 ${outcomes.network_blocked || 0}개 · 링크 오류 ${outcomes.unavailable || 0}개. 접근 차단된 페이지의 배포판은 별도 확인이 필요합니다.</p><div class="table-wrap"><table class="data-table"><tbody>${S.map((s) => "<tr><td>" + link(s.url, s.title || s.url.split("/")[2]) + "</td><td>" + esc({ unavailable: "링크 오류", page_read: "페이지 열람", network_blocked: "네트워크 차단", data_read: "데이터 확인", document_read: "문서 열람" }[s.status] || "미확인") + "</td></tr>").join("")}</tbody></table></div></details>
 <details class="surface full"><summary>자료 접근 포털과 선별 보류 목록</summary><div class="chips">${C.portals.map((p) => link(p.url, p.name)).join("")}</div>${C.screening.map((r) => "<p><b>" + esc(r.name) + "</b> · " + esc(r.status) + "<br>" + esc(r.reason) + " " + link(r.url, "출처") + "</p>").join("")}</details></div>`;
}
function repaint() {
  if (!C) return;
  count();
  document
    .querySelectorAll("[data-nav]")
    .forEach((x) => x.classList.toggle("active", x.dataset.nav === view));
  if (view === "journals") {
    journalPage();
    return;
  }
  $("#main").innerHTML =
    view === "explore" ? explore() : view === "work" ? work() : guide();
}
function navigate() {
  const route = location.hash.slice(1) || document.body.dataset.start;
  view = ["work", "guide", "journals"].includes(route)
    ? route
    : "explore";
  repaint();
}
function filterChange() {
  limit = query ? 3 : 12;
  repaint();
}
async function api(path, body = {}, raw = false) {
  const res = await fetch(path, {
    method: "POST",
    headers: {
      "X-PoliHub": "1",
      ...(!raw ? { "Content-Type": "application/json" } : {}),
    },
    body: raw ? body : JSON.stringify(body),
  });
  const v = await res.json();
  if (!res.ok) throw Error(v.error || "실행기 요청 실패");
  return v;
}
function persist() {
  save("project", active);
  history = [
    {
      id: active.id,
      title:
        active.spec.question ||
        active.spec.datasets.map((x) => ds(x.id).name).join(" · "),
    },
    ...history.filter((x) => x.id !== active.id),
  ].slice(0, 8);
  save("history", history);
}
async function ensureProject() {
  if (!current()) {
    const v = await api("/api/studies", {
      ids: [...selected],
      question,
      engine,
    });
    active = { ...v, status: null };
    persist();
  }
  return "/api/studies/" + active.id + "/";
}
async function task(action, data) {
  if (busy) return;
  busy = true;
  repaint();
  try {
    const base = await ensureProject();
    if (action === "plan") {
      const v = await api(base + "plan", data);
      Object.assign(active, v);
      question = v.spec.question;
      engine = v.spec.engine;
      save("question", question);
      save("engine", engine);
    } else if (action === "register") {
      const v = await api(base + "register?" + data.params, data.file, true);
      Object.assign(active, v);
    } else {
      if (active.spec.question !== question || active.spec.engine !== engine) {
        active.spec = { ...active.spec, question, engine };
        await api(base + "plan", active.spec);
      }
      const v = await api(base + action);
      Object.assign(active, v);
    }
    persist();
    announce(
      action === "register"
        ? "파일을 등록했습니다."
        : action === "plan"
          ? "연구명세를 저장했습니다."
          : "실행 기록을 저장했습니다.",
    );
  } catch (e) {
    toastError(e);
  } finally {
    busy = false;
    repaint();
  }
}
async function handle(el) {
  const a = el.dataset.action,
    i = el.dataset.id;
  switch (a) {
    case "download-data": {
      const f = C.files[release(ds(i)).files[0]];
      const url = live ? "/api/download/" + encodeURIComponent(f.id) : f.url;
      try {
        const resp = await fetch(url);
        if (!resp.ok) throw Error("HTTP " + resp.status);
        const buf = await resp.arrayBuffer();
        if (buf.byteLength !== f.bytes) throw Error("파일 크기 불일치");
        const hash = [
          ...new Uint8Array(await crypto.subtle.digest("SHA-256", buf)),
        ]
          .map((n) => n.toString(16).padStart(2, "0"))
          .join("");
        if (hash !== f.sha256) throw Error("배포판 해시 불일치");
        const blob = URL.createObjectURL(
          new Blob([buf], { type: "application/octet-stream" }),
        );
        const a = document.createElement("a");
        a.href = blob;
        a.download = f.name;
        a.click();
        setTimeout(() => URL.revokeObjectURL(blob), 1500);
        announce("해시를 확인한 원자료 파일을 내려받습니다.");
      } catch (e) {
        openDialog(
          "원자료 다운로드 확인",
          "<p>브라우저에서 파일을 확인할 수 없습니다. " +
            esc(e.message) +
            "</p>" +
            link(f.url, "공식 배포파일 열기") +
            "<pre>SHA-256 " +
            esc(f.sha256) +
            "</pre>",
        );
      }
      break;
    }
    case "close":
      $("#modal").close();
      break;
    case "detail":
      details(i);
      break;
    case "pick":
      choose(i);
      if ($("#modal").open) details(i);
      break;
    case "region":
      filters.region =
        filters.region === el.dataset.region ? "" : el.dataset.region;
      filterChange();
      break;
    case "category":
      filters.category =
        filters.category === el.dataset.field ? "" : el.dataset.field;
      filterChange();
      break;
    case "filter":
      filterOpen = !filterOpen;
      repaint();
      break;
    case "reset":
      for (let k of Object.keys(filters)) filters[k] = "";
      query = "";
      showAll = false;
      limit = 7;
      repaint();
      break;
    case "all":
      showAll = true;
      limit = 12;
      repaint();
      break;
    case "more":
      limit += 12;
      repaint();
      break;
    case "compare":
      compare();
      break;
    case "spec":
      downloadJSON(
        "study.json",
        current() ? { ...active.spec, question, engine } : makeSpec(),
      );
      break;
    case "pipeline":
    case "run":
      await task(a);
      break;
    case "save-plan":
      await task("plan", JSON.parse($("#plan-edit").value));
      break;
    case "import-nav":
      if (!selected.has(i)) choose(i);
      $("#modal").close();
      location.hash = "work";
      setTimeout(() => {
        if ($("#upload-details")) {
          $("#upload-details").open = true;
          $("#upload-id").value = i;
          $("#upload-details").scrollIntoView();
        }
      }, 25);
      break;
    case "upload": {
      const file = $("#upload").files[0],
        version = $("#upload-version").value.trim();
      if (!file || !version) throw Error("파일과 실제 배포 버전을 입력하세요.");
      const params = new URLSearchParams({
        id: $("#upload-id").value,
        version,
        ext: file.name.split(".").pop(),
        encoding: $("#upload-encoding").value,
      });
      if ($("#upload-member").value)
        params.set("member", $("#upload-member").value);
      await task("register", { params, file });
      break;
    }
    case "restore": {
      const id = el.dataset.project,
        r = await fetch("/api/studies/" + id + "/state");
      if (!r.ok) throw Error("이 실행기에서 프로젝트를 열 수 없습니다.");
      const v = await r.json();
      active = { id, ...v };
      selected = new Set(active.spec.datasets.map((x) => x.id));
      question = active.spec.question;
      engine = active.spec.engine;
      persist();
      save("selection", [...selected]);
      save("question", question);
      save("engine", engine);
      repaint();
      break;
    }
    case "doctor": {
      if (!live) {
        openDialog(
          "환경 확인",
          "<p>현재 화면은 정적 탐색 모드입니다. 연구를 실행할 환경의 터미널에서 진단하세요.</p><pre>python -m polihub doctor</pre>",
        );
        break;
      }
      openDialog(
        "환경 확인",
        '<p role="status">엔진 배치 실행과 네트워크를 확인하고 있습니다.</p>',
      );
      const v = await api("/api/doctor");
      openDialog(
        "연구 실행환경",
        `<dl class="facts"><dt>환경·운영체제</dt><dd>${esc(v.context + " · " + v.os)}</dd><dt>Python</dt><dd>${esc(v.python_version + " · " + v.python)}</dd><dt>Rscript</dt><dd>${esc(v.r.path || "미설치")}<br>${esc((v.r.versions || []).join(" · "))}</dd><dt>Stata 배치</dt><dd>${esc(label(v.stata.status))} ${esc(v.stata.version || v.stata.reason || "")}</dd><dt>저장공간</dt><dd>${(v.disk_free / 2 ** 30).toFixed(1)} GiB · ${v.writable ? "쓰기 가능" : "쓰기 실패"}</dd><dt>인증 설정</dt><dd>MANIFESTO_API_KEY · ${v.credentials.MANIFESTO_API_KEY ? "설정됨" : "미설정"}</dd><dt>네트워크</dt><dd>${v.network.map((x) => esc(x.host) + " · " + (x.status === "reachable" ? "응답 확인" : "접근 실패")).join("<br>")}</dd></dl>`,
      );
      break;
    }
  }
}
document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-action]");
  if (t) {
    handle(t).catch(toastError);
    return;
  }
  if (e.target.closest("#tray")) {
    location.hash = "work";
  }
});
document.addEventListener("input", (e) => {
  if (e.target.id === "work-question") {
    question = e.target.value;
    save("question", question);
  }
});
document.addEventListener("change", (e) => {
  const k = e.target.dataset.filter;
  if (k) {
    filters[k] = e.target.value;
    filterChange();
  }
  if (e.target.id === "engine") {
    engine = e.target.value;
    save("engine", engine);
  }
});
document.addEventListener("submit", (e) => {
  if (e.target.id === "search-form") {
    e.preventDefault();
    query = new FormData(e.target).get("query").trim();
    if (query) {
      question = query;
      save("question", question);
    }
    limit = query ? 3 : 7;
    repaint();
    $("#results").scrollIntoView({ block: "start" });
  }
});
document
  .querySelectorAll("[data-icon]")
  .forEach((el) => (el.innerHTML = icon(el.dataset.icon)));
$("#modal").addEventListener("click", (e) => {
  if (e.target === $("#modal")) {
    const b = $("#modal").getBoundingClientRect();
    if (
      e.clientX < b.left ||
      e.clientX > b.right ||
      e.clientY < b.top ||
      e.clientY > b.bottom
    )
      $("#modal").close();
  }
});
window.addEventListener("hashchange", () => {
  navigate();
  window.scrollTo(0, 0);
});
(async () => {
  try {
    if (window.POLIHUB_OFFLINE) {
      C = window.POLIHUB_OFFLINE.catalog;
      S = window.POLIHUB_OFFLINE.sources;
      $("#catalog-download").onclick = (e) => {
        e.preventDefault();
        downloadJSON("catalog.json", C);
      };
    } else {
      [C, S] = await Promise.all(
        ["catalog/catalog.json", "catalog/sources.json"].map((u) =>
          fetch(u).then((r) => {
            if (!r.ok) throw Error("카탈로그 읽기 실패");
            return r.json();
          }),
        ),
      );
      try {
        live =
          (await (await fetch("/api/capabilities")).json()).execution === true;
      } catch {
        live = false;
      }
    }
    selected = new Set([...selected].filter((i) => ds(i)));
    navigate();
  } catch (e) {
    $("#main").innerHTML =
      '<div class="empty"><h2>카탈로그를 열 수 없습니다.</h2><p>프로젝트 폴더에서 <code>python -m polihub serve</code>를 실행하거나 독립 실행 HTML을 여세요.</p></div>';
  }
})();
