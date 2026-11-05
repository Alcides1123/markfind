// markfind web interface. No dependencies, no build step.
//
// Snippets, titles and folders come from arbitrary web pages, so they are
// always inserted as text (textContent / text nodes), never as HTML.
"use strict";

const MARK_START = "\u0002";
const MARK_END = "\u0003";
const DEBOUNCE_MS = 150;
// Bookmarks listed for a tag with an empty search box (the server's maximum).
const TAG_LIST_LIMIT = 100;

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
let similarTo = null; // URL whose similar bookmarks are shown, or null in normal search
let selected = -1; // index of the result picked with the arrow keys, or -1

// Keep the view in the address bar, so it can be reloaded or bookmarked.
function updateAddress() {
  const params = new URLSearchParams();
  const q = $("q").value.trim();
  if (q) params.set("q", q);
  if ($("raw").checked) params.set("raw", "1");
  if ($("folder").value) params.set("folder", $("folder").value);
  if ($("tag").value) params.set("tag", $("tag").value);
  if (similarTo) params.set("similar", similarTo);
  const qs = params.toString();
  history.replaceState(null, "", qs ? `?${qs}` : location.pathname);
}

function similarButton(url) {
  const button = el("button", "similar", "Similar");
  button.type = "button";
  button.title = "Find bookmarks like this one";
  button.addEventListener("click", () => showSimilar(url));
  return button;
}

function tagButtons(tags) {
  const box = el("span", "tags");
  for (const tag of tags) {
    const button = el("button", "tag", tag);
    button.type = "button";
    button.title = `Show bookmarks tagged ${tag}`;
    button.addEventListener("click", () => filterByTag(tag));
    box.append(button);
  }
  return box;
}

function renderResults(results) {
  const list = $("results");
  list.replaceChildren();
  selected = -1;
  for (const r of results) {
    const item = el("li", "result");
    const title = link(r.url, r.title || r.url);
    title.classList.add("title");
    item.append(title);

    const meta = el("div", "meta");
    meta.append(el("span", "url", r.url));
    if (r.folder) meta.append(el("span", "folder", r.folder));
    if (r.tags && r.tags.length) meta.append(tagButtons(r.tags));
    meta.append(similarButton(r.url));
    item.append(meta);

    if (r.note) {
      const note = el("p", "note", r.note);
      note.title = "Your note";
      item.append(note);
    }

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
  const tag = $("tag").value;

  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (raw) params.set("raw", "1");
  if ($("folder").value) params.set("folder", $("folder").value);
  if (tag) params.set("tag", tag);
  if (!q && tag) params.set("limit", String(TAG_LIST_LIMIT));
  const qs = params.toString();
  updateAddress();

  if (searchController) searchController.abort();
  if (!q && !tag) {
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
    const n = data.results.length;
    if (q) {
      status.textContent = n === 0 ? "No matches." : n === 1 ? "1 match" : `${n} matches`;
    } else {
      // A tag with an empty search box lists everything with that tag.
      status.textContent =
        n === 0 ? `No bookmarks tagged ${tag}.` :
        n >= TAG_LIST_LIMIT ? `First ${n} bookmarks tagged ${tag}; type to narrow them down` :
        n === 1 ? `1 bookmark tagged ${tag}` : `${n} bookmarks tagged ${tag}`;
    }
  } catch (err) {
    if (err.name === "AbortError") return;
    renderResults([]);
    status.classList.add("error");
    status.textContent = err.message;
  }
}

function scheduleSearch() {
  leaveSimilar();
  clearTimeout(searchTimer);
  searchTimer = setTimeout(runSearch, DEBOUNCE_MS);
}

// --- folders ---------------------------------------------------------------

const NBSP = String.fromCharCode(160);

// Fill the folder menu as an indented tree. `wanted` is the folder to select
// (from the page address); it's kept even if it no longer has bookmarks.
async function loadFolders(wanted) {
  const menu = $("folder");
  try {
    const data = await getJSON("/api/folders");
    for (const f of data.folders) {
      const parts = f.path.split("/");
      const label = `${NBSP.repeat(3 * (parts.length - 1))}${parts[parts.length - 1]} (${f.count})`;
      const option = el("option", null, label);
      option.value = f.path;
      option.title = f.path;
      menu.append(option);
    }
  } catch {
    // Searching still works without the menu.
  }
  if (wanted) {
    if (![...menu.options].some((o) => o.value === wanted)) {
      const option = el("option", null, wanted);
      option.value = wanted;
      menu.append(option);
    }
    menu.value = wanted;
  }
}

// --- tags ------------------------------------------------------------------

function tagOption(tag) {
  const option = el("option", null, tag);
  option.value = tag;
  return option;
}

// Fill the tag menu, shown only if some bookmark has a tag. `wanted` is the
// tag to select (from the page address).
async function loadTags(wanted) {
  const menu = $("tag");
  try {
    const data = await getJSON("/api/tags");
    for (const t of data.tags) {
      const option = tagOption(t.tag);
      option.textContent = `${t.tag} (${t.count})`;
      menu.append(option);
    }
  } catch {
    // Searching still works without the menu.
  }
  if (wanted) selectTag(wanted);
  menu.hidden = menu.options.length < 2;
}

// Tags match ignoring case, so pick the menu's spelling when there is one.
function selectTag(tag) {
  const menu = $("tag");
  let option = [...menu.options].find((o) => o.value && o.value.toLowerCase() === tag.toLowerCase());
  if (!option) menu.append((option = tagOption(tag)));
  menu.value = option.value;
  menu.hidden = false;
}

function filterByTag(tag) {
  selectTag(tag);
  leaveSimilar();
  runSearch();
  $("q").focus();
}

// --- keyboard navigation through results -------------------------------------

function resultItems() {
  return [...$("results").querySelectorAll(".result")];
}

// Highlight result `index`, or none if it's below 0.
function select(index) {
  const items = resultItems();
  selected = items.length ? Math.min(index, items.length - 1) : -1;
  items.forEach((item, i) => {
    item.classList.toggle("selected", i === selected);
    if (i === selected) item.setAttribute("aria-current", "true");
    else item.removeAttribute("aria-current");
  });
  const item = items[selected];
  if (item && item.scrollIntoView) item.scrollIntoView({ block: "nearest" });
}

// Arrow keys move through results; Enter opens one, Shift+Enter finds similar.
function navigate(event) {
  if ($("view-search").hidden) return false;
  if (event.key === "ArrowDown") select(selected + 1);
  else if (event.key === "ArrowUp") select(selected - 1);
  else if (event.key === "Enter" && selected >= 0) {
    const item = resultItems()[selected];
    const target = event.shiftKey ? item.querySelector("button.similar") : item.querySelector("a.title");
    if (target) target.click();
  } else return false;
  return true;
}

// --- similar bookmarks -------------------------------------------------------

async function showSimilar(url) {
  similarTo = url;
  updateAddress();
  clearTimeout(searchTimer);
  if (searchController) searchController.abort();
  searchController = new AbortController();

  $("similar-header").hidden = false;
  const title = $("similar-title");
  const keywords = $("similar-keywords");
  const status = $("search-status");
  title.textContent = "Finding similar bookmarks…";
  keywords.textContent = "";
  try {
    const data = await getJSON(
      `/api/similar?url=${encodeURIComponent(url)}`,
      searchController.signal,
    );
    title.replaceChildren("Similar to: ", link(data.url, data.title || data.url));
    keywords.textContent = data.keywords.length ? `Based on: ${data.keywords.join(", ")}` : "";
    renderResults(data.results);
    status.classList.remove("error");
    status.textContent =
      data.results.length === 0 ? "No similar bookmarks." :
      data.results.length === 1 ? "1 similar bookmark" : `${data.results.length} similar bookmarks`;
  } catch (err) {
    if (err.name === "AbortError") return;
    title.textContent = "Similar bookmarks";
    renderResults([]);
    status.classList.add("error");
    status.textContent = err.message;
  }
}

// Back to normal search. Returns whether a similar view was open.
function leaveSimilar() {
  if (!similarTo) return false;
  similarTo = null;
  $("similar-header").hidden = true;
  return true;
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
  $("raw").addEventListener("change", () => {
    leaveSimilar();
    runSearch();
  });
  for (const menu of [$("folder"), $("tag")]) {
    menu.addEventListener("change", () => {
      leaveSimilar();
      runSearch();
    });
  }
  $("back").addEventListener("click", () => {
    leaveSimilar();
    runSearch();
    $("q").focus();
  });
  $("show-all").addEventListener("change", loadBroken);
  $("tab-search").addEventListener("click", () => showView("search"));
  $("tab-broken").addEventListener("click", () => showView("broken"));

  document.addEventListener("keydown", (event) => {
    const typing = event.target instanceof HTMLInputElement;
    // Other form controls (the folder menu, checkboxes) keep their own keys.
    const inOtherControl =
      event.target !== $("q") &&
      (typing || event.target instanceof HTMLSelectElement || event.target instanceof HTMLButtonElement);
    if (!inOtherControl && navigate(event)) {
      event.preventDefault();
      return;
    }
    if (event.key === "/" && !typing) {
      event.preventDefault();
      showView("search");
    } else if (event.key === "Escape" && event.target === $("q")) {
      // First Esc leaves the similar view; the next one clears the search.
      if (!leaveSimilar()) $("q").value = "";
      runSearch();
    }
  });

  loadStats();
  $("q").focus();
  // The folder and tag must be in their menus before the first search reads them.
  Promise.all([loadFolders(params.get("folder")), loadTags(params.get("tag"))]).then(() => {
    if (params.get("similar")) showSimilar(params.get("similar"));
    else if ($("q").value || $("tag").value) runSearch();
  });
}

document.addEventListener("DOMContentLoaded", init);
