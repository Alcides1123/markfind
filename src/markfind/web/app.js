// markfind web interface. No dependencies, no build step.
//
// Snippets, titles and folders come from arbitrary web pages, so they are
// always inserted as text (textContent / text nodes), never as HTML.
"use strict";

const MARK_START = "\u0002";
const MARK_END = "\u0003";
const DEBOUNCE_MS = 150;

const $ = (id) => document.getElementById(id);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// Only http(s) links are ever rendered as clickable.
function safeHref(url) {
  try {
    const u = new URL(url);
    return u.protocol === "http:" || u.protocol === "https:" ? u.href : null;
  } catch {
    return null;
  }
}

function link(url, text) {
  const href = safeHref(url);
  if (!href) return el("span", "link", text);
  const a = el("a", "link", text);
  a.href = href;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  return a;
}

// Turn "a \u0002match\u0003 b" into text nodes and <mark> elements.
function highlighted(text) {
  const frag = document.createDocumentFragment();
  let inMark = false;
  for (const part of text.split(/([\u0002\u0003])/)) {
    if (part === MARK_START) inMark = true;
    else if (part === MARK_END) inMark = false;
    else if (part) frag.append(inMark ? el("mark", null, part) : document.createTextNode(part));
  }
  return frag;
}

async function getJSON(path, signal) {
  const response = await fetch(path, { signal, headers: { Accept: "application/json" } });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

// --- stats -----------------------------------------------------------------

async function loadStats() {
  try {
    const s = await getJSON("/api/stats");
    const parts = [`${s.total} bookmarks`, `${s.indexed} indexed`];
    if (s.failed) parts.push(`${s.failed} failed`);
    if (s.pending) parts.push(`${s.pending} not fetched yet`);
    $("stats").textContent = parts.join(" · ");
  } catch (err) {
    $("stats").textContent = `Could not load stats: ${err.message}`;
  }
}

// --- search ----------------------------------------------------------------

let searchTimer = null;
let searchController = null;

function renderResults(results) {
  const list = $("results");
  list.replaceChildren();
  for (const r of results) {
    const item = el("li", "result");
    const title = link(r.url, r.title || r.url);
    title.classList.add("title");
    item.append(title);

    const meta = el("div", "meta");
    meta.append(el("span", "url", r.url));
    if (r.folder) meta.append(el("span", "folder", r.folder));
    item.append(meta);

    if (r.snippet) {
      const snippet = el("p", "snippet");
      snippet.append(highlighted(r.snippet));
      item.append(snippet);
    }
    list.append(item);
  }
}

async function runSearch() {
  const q = $("q").value.trim();
  const raw = $("raw").checked;

  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (raw) params.set("raw", "1");
  const qs = params.toString();
  history.replaceState(null, "", qs ? `?${qs}` : location.pathname);

  if (searchController) searchController.abort();
  if (!q) {
    renderResults([]);
    $("search-status").textContent = "";
    return;
  }

  searchController = new AbortController();
  const status = $("search-status");
  try {
    const data = await getJSON(`/api/search?${qs}`, searchController.signal);
    renderResults(data.results);
    status.classList.remove("error");
    status.textContent =
      data.results.length === 0 ? "No matches." :
      data.results.length === 1 ? "1 match" : `${data.results.length} matches`;
  } catch (err) {
    if (err.name === "AbortError") return;
    renderResults([]);
    status.classList.add("error");
    status.textContent = err.message;
  }
}

function scheduleSearch() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(runSearch, DEBOUNCE_MS);
}

// --- dead links --------------------------------------------------------------

async function loadBroken() {
  const container = $("broken");
  const status = $("broken-status");
  const showAll = $("show-all").checked;
  try {
    const data = await getJSON(`/api/broken${showAll ? "?all=1" : ""}`);
    container.replaceChildren();
    status.classList.remove("error");
    if (data.failures.length === 0) {
      status.textContent = showAll ? "No failed bookmarks." : "No dead links found.";
      return;
    }
    status.textContent = `${data.failures.length} bookmark${data.failures.length === 1 ? "" : "s"}`;

    const groups = new Map();
    for (const f of data.failures) {
      if (!groups.has(f.kind)) groups.set(f.kind, []);
      groups.get(f.kind).push(f);
    }
    for (const [kind, failures] of groups) {
      const section = el("section", "group");
      section.append(el("h2", null, `${kind} (${failures.length})`));
      const list = el("ul", "failures");
      for (const f of failures) {
        const item = el("li");
        const title = link(f.url, f.title || f.url);
        title.classList.add("title");
        item.append(title);
        const meta = el("div", "meta");
        meta.append(el("span", "error", f.error));
        meta.append(el("span", null, `checked ${f.checked.slice(0, 10)}`));
        if (f.folder) meta.append(el("span", "folder", f.folder));
        item.append(meta);
        list.append(item);
      }
      section.append(list);
      container.append(section);
    }
  } catch (err) {
    status.classList.add("error");
    status.textContent = `Could not load dead links: ${err.message}`;
  }
}

// --- views and keyboard ----------------------------------------------------

function showView(name) {
  const search = name === "search";
  $("view-search").hidden = !search;
  $("view-broken").hidden = search;
  $("tab-search").setAttribute("aria-pressed", String(search));
  $("tab-broken").setAttribute("aria-pressed", String(!search));
  if (search) $("q").focus();
  else loadBroken();
}

function init() {
  const params = new URLSearchParams(location.search);
  $("q").value = params.get("q") || "";
  $("raw").checked = params.get("raw") === "1";

  $("q").addEventListener("input", scheduleSearch);
  $("raw").addEventListener("change", runSearch);
  $("show-all").addEventListener("change", loadBroken);
  $("tab-search").addEventListener("click", () => showView("search"));
  $("tab-broken").addEventListener("click", () => showView("broken"));

  document.addEventListener("keydown", (event) => {
    const typing = event.target instanceof HTMLInputElement;
    if (event.key === "/" && !typing) {
      event.preventDefault();
      showView("search");
    } else if (event.key === "Escape" && event.target === $("q")) {
      $("q").value = "";
      runSearch();
    }
  });

  loadStats();
  $("q").focus();
  if ($("q").value) runSearch();
}

document.addEventListener("DOMContentLoaded", init);
