/* Shared console helpers: top nav, fetch wrapper, formatting, severity map. */

/* Colours are CSS variable references, not literals. They used to be hex copies
   of the :root tokens and drifted the moment those were darkened for contrast -
   the severity mix bar drew #E05555 while the badge beside it drew #D82929, two
   different reds on the same row. Referencing the token means one value wins,
   including any brand override. */
const SEV = {
  high:   { label: "High",   color: "var(--red)" },
  medium: { label: "Medium", color: "var(--amber)" },
  low:    { label: "Low",    color: "var(--green-2)" },
};

/* Hazard TYPE colors and icons — same language as the annotation boxes, and a
   separate axis from the severity badges above.

   Two channels, on purpose. COLOR carries the contrast: smoke used to be
   #F0A500 against a #FFD700 dead tree, only 1.48:1 apart (WCAG 1.4.11 asks 3:1
   for graphical objects) and near-identical under red-green colorblindness.
   Slate vs bone is 3.42:1. ICON carries the recognition: a flame looks like
   fire, a puff like smoke, a bare trunk like a dead tree — so the map is
   readable without decoding the legend, and the type survives greyscale
   printing and any color-vision deficiency.

   Keep in sync with :root in console.css and the BGR constants in annotate.py.
   `text` is the accessible on-white variant (bone is invisible as text);
   `on` is the icon color to use when drawn ON TOP of the fill. */
const KIND = {
  flame: {
    label: "Flame", color: "#E05555", text: "#C0392B", on: "#fff",
    icon: "M12 2.5c.6 3.2-1.1 4.5-2.6 6C7.7 10.2 6.5 11.9 6.5 14a5.5 5.5 0 0 0 11 0c0-2.6-1.6-4.3-2.8-6.2-.5 1-1.2 1.6-2 2 .4-2.9-.4-5.6-1.2-7.3z",
  },
  smoke: {
    label: "Smoke", color: "#546E7A", text: "#455A64", on: "#fff",
    icon: "M7 18h10a3.5 3.5 0 0 0 .4-7A4.5 4.5 0 0 0 9 9.3 3.6 3.6 0 0 0 4 12.6 3.4 3.4 0 0 0 7 18z",
  },
  deadtree: {
    label: "Dead Tree", color: "#D9CDB0", text: "#8a7c5c", on: "#5a5346",
    icon: "M12 21V7m0 4 3.5-3.5M12 13l-3.6-3.6M12 9 9 6M12 7l3-3",
  },
  /* No colour reaches 3:1 luminance contrast against all three above (slate
     is dark, bone is light, and the band between is too narrow from both
     sides), so sky blue is separated by HUE: under simulated deuteranopia and
     protanopia its nearest neighbour is dE 40+ away, wider than flame/bone
     (30.1). The icon - a log lying diagonally with its cut end showing - keeps
     colour from being the only channel. */
  fallen: {
    label: "Fallen Tree", color: "#29B6F6", text: "#0277BD", on: "#06324d",
    icon: "M3.2 17.4 15.6 5.9a2.3 2.3 0 0 1 3.3.1 2.3 2.3 0 0 1-.1 3.3L6.4 20.8a2.3 2.3 0 0 1-3.3-.1 2.3 2.3 0 0 1 .1-3.3z",
  },
};
/* Display label (as the API sends it) -> KIND key. Pages used to inline
   `k === "Flame" ? "flame" : k === "Smoke" ? "smoke" : "deadtree"`, which
   silently filed every new type under dead tree. */
const KIND_BY_LABEL = { "Flame": "flame", "Smoke": "smoke", "Dead Tree": "deadtree", "Fallen Tree": "fallen" };
const kindForLabel = label => KIND_BY_LABEL[label] || "deadtree";
/* A site can hold several hazard types; it is shown if ANY of them passes the
   pin filter. Its pin colour is still the most critical type there. */
const siteKinds = s => ((s.kinds && s.kinds.length) ? s.kinds : [s.kind]).map(k => kindKey(k));
/* The type a pin shows under the current filter: the most critical of its types
   that is switched on. Colouring by the most critical type overall meant that
   with only "Fallen Tree" selected, every site holding dead trees as well stayed
   bone-coloured - the filter showed fallen-tree sites dressed as something else. */
function shownKind(s, active) {
  const kinds = siteKinds(s);   // most critical first, as data.map_data sorts them
  return kinds.find(k => !active || active.has(k)) || kinds[0];
}
/* Re-dress existing Leaflet markers for a filter, in place: icon pins get a new
   icon, the canvas circles used past MARKER_ICON_LIMIT get a new fill. */
function recolorSiteMarkers(leaf, sites, active) {
  sites.forEach((s, i) => {
    const kind = shownKind(s, active);
    const m = leaf.markers[i];
    if (leaf.useIcons) m.setIcon(siteDivIcon({ ...s, kind }));
    else m.setStyle({ fillColor: KIND[kind].color });
  });
}
const kindOf = s => KIND[s.kind] || KIND.deadtree;
const kindKey = k => (KIND[k] ? k : "deadtree");

/* Dead tree is drawn as strokes (bare branches), the other two as filled
   silhouettes — a shape difference that survives being scaled down to 10px. */
function kindIcon(kind, size = 13, color) {
  const k = KIND[kind] || KIND.deadtree;
  const c = color || k.color;
  const paint = kind === "deadtree"
    ? `fill="none" stroke="${c}" stroke-width="2" stroke-linecap="round"`
    : `fill="${c}"`;
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" ${paint}
            style="vertical-align:-2px; flex-shrink:0;"><path d="${k.icon}"></path></svg>`;
}

function kindBadge(kind) {
  const k = KIND[kind] || KIND.deadtree;
  return `<span class="badge" style="color:${k.text}; background:${k.color}22; border:1px solid ${k.color};">`
       + `${kindIcon(kind, 11, k.text)} ${k.label.toUpperCase()}</span>`;
}

/* Shared legend for the Dashboard and Map map cards — one definition, so the
   two cannot drift the way the hardcoded hex swatches did. Every entry is also
   a real <button> that toggles its hazard type on the map (the pin filter);
   the pressed state rides on aria-pressed so the chips read as toggles, not
   decoration. `active` is the Set of kind keys currently shown. */
function kindFilterHtml(active) {
  return Object.keys(KIND).map(k => {
    const on = active.has(k);
    return `<button type="button" class="kind-toggle${on ? "" : " off"}" data-kind="${k}"
      aria-pressed="${on}" title="${on ? "Hide" : "Show"} ${KIND[k].label} pins">
      ${kindIcon(k, 13)} ${KIND[k].label}</button>`;
  }).join("");
}

/* One saved pin-filter for both hazard maps: hiding a type on the Dashboard
   hides it on the Map page too — it is one decision about what the operator is
   hunting, not a per-page view setting. An all-hidden saved set falls back to
   everything, because restoring a blank map reads as broken, not filtered. */
const KIND_FILTER_KEY = "wf.kinds";
function loadKindFilter() {
  try {
    const saved = JSON.parse(localStorage.getItem(KIND_FILTER_KEY));
    const valid = (Array.isArray(saved) ? saved : []).filter(k => KIND[k]);
    if (valid.length) return new Set(valid);
  } catch (e) { /* first visit or unreadable — show everything */ }
  return new Set(Object.keys(KIND));
}
function saveKindFilter(active) {
  localStorage.setItem(KIND_FILTER_KEY, JSON.stringify([...active]));
}

const REVIEW_URL_FALLBACK = "http://127.0.0.1:7860";

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (e) { /* keep statusText */ }
    throw new Error(msg);
  }
  return res.json();
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function sevBadge(sev) {
  if (!sev) return '<span class="badge none">&mdash;</span>';
  return `<span class="badge ${sev}">${SEV[sev].label}</span>`;
}

/* A run made by an older detector than this one. Its counts were taken under
   different rules (gen 1 called ~70 "dead trees" per frame where gen 2 finds
   ~20), so its severity is graded on thresholds that no longer fit it. Shown
   beside the badge rather than replacing it: the run is still the record of
   what was seen on that flight. */
function staleChip(s) {
  if (!s || !s.detector_stale) return "";
  return '<span class="badge stale" title="Made by an older detector: its counts are not comparable '
    + 'with newer scans and its severity may be overstated. Re-detect from the scan page.">OLDER DETECTOR</span>';
}

/* A run's badge is its WORST frame. On its own that reads as a verdict on the
   whole flight: a 58-image run with flame in 6 frames showed "HIGH" while 44 of
   its frames were medium or low. These two render the rest of the picture. */
function sevMixBar(mix, total) {
  if (!mix || !total) return "";
  const order = ["high", "medium", "low", "none"];
  const seg = order.filter(k => mix[k]).map(k => {
    const color = k === "none" ? "var(--muted-3)" : SEV[k].color;
    const label = k === "none" ? "no detections" : SEV[k].label.toLowerCase();
    return `<span title="${mix[k]} ${label}" style="width:${(mix[k] / total) * 100}%;
             background:${color};"></span>`;
  }).join("");
  return `<span class="sev-mix" title="per-image severity across ${total} images">${seg}</span>`;
}

function sevMixText(mix, flameImages) {
  if (!mix) return "";
  const parts = ["high", "medium", "low"].filter(k => mix[k])
    .map(k => `${mix[k]} ${SEV[k].label.toLowerCase()}`);
  if (mix.none) parts.push(`${mix.none} clear`);
  // Flame is called out by name: "6 images with flame" is actionable in a way
  // that a severity word is not.
  const flame = flameImages
    ? `<b style="color:${KIND.flame.text};">flame in ${flameImages} image${flameImages === 1 ? "" : "s"}</b>`
    : "";
  return [flame, parts.join(" · ")].filter(Boolean).join(" &middot; ");
}

function countsLine(counts) {
  const parts = Object.entries(counts || {}).map(([k, v]) => `${v} ${esc(k)}`);
  return parts.length ? parts.join(" · ") : "no detections";
}

/* ------------------------------------------------ leaving with unsaved work
   A page holding unsaved work sets window.WF_LEAVE_GUARD to a function that
   returns a warning, or "" while there is nothing to lose. Every in-app way out
   goes through wfNavigate: nav tabs and other links (caught below), the
   d/s/r/m/p/e keys, and code that changes location. The browser's own
   beforeunload dialog is kept for browser mode, but WebView2 and WKWebView do
   not reliably show it, so it cannot be the only line of defence. */
function wfNavigate(href) {
  const warning = window.WF_LEAVE_GUARD ? window.WF_LEAVE_GUARD() : "";
  if (!warning) { location.href = href; return; }
  wfConfirm(warning, { ok: "Leave without saving", cancel: "Stay on this page", danger: true })
    .then(leave => {
      if (!leave) return;
      window.WF_LEAVE_GUARD = null;   // or beforeunload would ask a second time
      location.href = href;
    });
}

document.addEventListener("click", e => {
  if (!window.WF_LEAVE_GUARD || e.defaultPrevented) return;
  const a = e.target.closest && e.target.closest("a[href]");
  if (!a || a.target === "_blank" || e.ctrlKey || e.metaKey || e.shiftKey) return;
  const url = new URL(a.getAttribute("href"), location.href);
  if (url.origin !== location.origin) return;
  if (url.pathname === location.pathname && url.search === location.search) return;
  if (!window.WF_LEAVE_GUARD()) return;
  e.preventDefault();
  wfNavigate(url.href);
}, true);

window.addEventListener("beforeunload", e => {
  const warning = window.WF_LEAVE_GUARD ? window.WF_LEAVE_GUARD() : "";
  if (warning) { e.preventDefault(); e.returnValue = warning; }
});

/* An in-page confirmation, resolving true or false. Not window.confirm: native
   dialogs are unreliable inside the desktop webviews. Escape and the backdrop
   cancel; the safe choice holds focus, so Enter never confirms a destructive
   action by accident. */
function wfConfirm(message, { ok = "OK", cancel = "Cancel", danger = false } = {}) {
  return new Promise(resolve => {
    const back = document.createElement("div");
    back.className = "wf-modal-back";
    back.innerHTML = `
      <div class="wf-modal" role="alertdialog" aria-modal="true" aria-labelledby="wf-modal-msg">
        <div id="wf-modal-msg" class="wf-modal-msg">${esc(message)}</div>
        <div class="wf-modal-actions">
          <button type="button" class="btn-quiet" data-a="cancel">${esc(cancel)}</button>
          <button type="button" class="btn ${danger ? "btn-danger" : "btn-primary"}" data-a="ok">${esc(ok)}</button>
        </div>
      </div>`;
    const done = value => {
      document.removeEventListener("keydown", onKey, true);
      back.remove();
      resolve(value);
    };
    const onKey = e => {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); done(false); }
    };
    back.addEventListener("click", e => {
      if (e.target === back) return done(false);
      const b = e.target.closest("[data-a]");
      if (b) done(b.dataset.a === "ok");
    });
    document.addEventListener("keydown", onKey, true);
    document.body.appendChild(back);
    back.querySelector('[data-a="cancel"]').focus();
  });
}

function toast(msg, ms = 5000, link = null) {
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = msg;
  if (link) {
    const a = document.createElement("a");
    a.className = "toast-link";
    a.href = link.href;
    a.textContent = link.text;
    a.addEventListener("click", e => { e.preventDefault(); wfNavigate(link.href); });
    t.appendChild(a);
  }
  document.body.appendChild(t);
  setTimeout(() => t.remove(), ms);
}

/* Branding — read from the branding/ folder (name, colors, logo). Applied on
   load so dropping a logo + editing brand.json rebrands the whole console. */
let BRANDING = {
  app_name: "Wildfire Hazard Detection System",
  subtitle: "Operations Console · Offline",
  logo_url: null, colors: {},
};
let _navActive = null;

/* NOTE: these overwrite the :root tokens at runtime, so a brand colour silently
   wins over the value in console.css. When --green-2 was darkened there for
   contrast, brand.json kept pushing the old lighter green back. If you tune a
   token that appears here, tune branding/brand.json to match or the CSS change
   has no effect. */
function applyBrandingColors() {
  const c = BRANDING.colors || {};
  const root = document.documentElement.style;
  if (c.primary) root.setProperty("--green", c.primary);
  if (c.primary_light) root.setProperty("--green-2", c.primary_light);
}

/* Top navigation, shared by all pages. `active`: dashboard | scans | review | map */
/* accessibility alteration to const tab, do not change */
function renderNav(active) {
  _navActive = active;
  const tab = (id, label, href, key) => {
    const isActive = active === id;
    const cls = "nav-tab" + (isActive ? " active" : "");
    /* following two lines needed for keyboard shotcuts, don't change or move please */
    const keyAttr = key ? ` accesskey="${key}"` : "";
    const isMac = navigator.platform.toUpperCase().includes("MAC");
    const combo = key ? (isMac ? `Ctrl+Option+${key.toUpperCase()}` : `Alt+${key.toUpperCase()}`) : "";
    const titleAttr = key ? ` title="${label} (press ${key.toUpperCase()})"` : "";
  return `<a class="${cls}" href="${href}"${isActive ? ' aria-current="page"' : ""}${keyAttr}${titleAttr}>${label}</a>`;
};

// Webview2 has something called accelerator keys (ctrl, alt), single key press. this event listener is to handle the shortcuts. please do not move or alter*/
const NAV_KEYS = { d: "/", s: "/scans", r: "/review", m: "/map", p: "/reports", e: "/settings" };

document.addEventListener("keydown", e => {
  if (e.altKey || e.ctrlKey || e.metaKey) return;
  // leave the accelerators alone
  const tag = document.activeElement?.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return; 
  // fix to the issue of typing causing it to trigger and steal you to another tab mid type
  if (document.activeElement?.isContentEditable) return;
  // a page in the middle of something keyboard-heavy (box review) opts out, so
  // a stray letter cannot throw the operator onto another page mid-review
  if (window.WF_SUPPRESS_NAV_KEYS) return;
  const href = NAV_KEYS[e.key.toLowerCase()];
  if (href) wfNavigate(href);
});
  const logo = BRANDING.logo_url
    ? `<img src="${esc(BRANDING.logo_url)}" alt="logo" style="height:32px; width:auto; border-radius:6px;">`
    : "";
  document.getElementById("topnav").innerHTML = `
    <div class="brand">
      ${logo}
      <div style="display:flex; flex-direction:column; line-height:1.15;">
        <span class="brand-name">${esc(BRANDING.app_name)}</span>
        <span class="brand-sub">${esc(BRANDING.subtitle)}</span>
      </div>
    </div>
    <div class="nav-tabs">
      ${tab("dashboard", "Dashboard", "/", "d")}
      ${tab("scans", "Scans", "/scans", "s")}
      ${tab("review", "Review", "/review", "r")}
      ${tab("map", "Map", "/map", "m")}
      ${tab("reports", "Reports", "/reports", "p")}
      ${tab("settings", "Settings", "/settings", "e")}
    </div>
    <div class="nav-right">
      <a class="job-pill" id="job-pill" href="/scans" hidden></a>
      <button type="button" class="nav-help" title="Keyboard shortcuts (press ?)"
              aria-label="Keyboard shortcuts" onclick="wfShowShortcuts()">?</button>
      <div class="live" id="live-status" title="Detection and reports always run on this machine.">
        <span class="live-dot"></span><span class="mono">Local</span>
      </div>
    </div>`;
  refreshStatus();
  pollJobs();
}

/* ------------------------------------------------------------- job progress
   Detection used to be visible only on the Scans page, which the operator
   leaves for the 20 minutes a flight takes. Every page's header now carries a
   pill with progress and time left, and the page says when a job ends - even
   one that ended while another page was open (the ids seen running are kept for
   the tab's session). The desktop window adds a system notification. */
const JOBS_SEEN = "wf.jobsSeen", JOBS_TOLD = "wf.jobsTold";
let _jobPoll = null;

function _sessionSet(key) {
  try { return new Set(JSON.parse(sessionStorage.getItem(key) || "[]")); } catch (e) { return new Set(); }
}
function _sessionSave(key, set) {
  try { sessionStorage.setItem(key, JSON.stringify([...set].slice(-50))); } catch (e) { /* private mode */ }
}

function fmtDuration(s) {
  if (s == null) return "";
  if (s < 60) return "under a minute";
  const m = Math.round(s / 60);
  if (m < 60) return `about ${m} min`;
  return `about ${Math.floor(m / 60)} h ${m % 60} min`;
}

function jobProgressText(j) {
  if (!j.total || !j.done) return "Detection starting\u2026";
  const left = j.eta_s != null ? ` \u00b7 ${fmtDuration(j.eta_s)} left` : "";
  return `Detecting ${j.done}/${j.total}${left}`;
}

async function pollJobs() {
  clearTimeout(_jobPoll);
  let jobs = [];
  try { jobs = (await api("/api/jobs")).jobs || []; } catch (e) { /* server restarting */ }
  const active = jobs.filter(j => j.state === "queued" || j.state === "running");
  const seen = _sessionSet(JOBS_SEEN), told = _sessionSet(JOBS_TOLD);
  active.forEach(j => seen.add(j.id));

  const pill = document.getElementById("job-pill");
  if (pill) {
    pill.hidden = !active.length;
    if (active.length) {
      const j = active[0];
      const pct = j.total ? Math.round(100 * j.done / j.total) : 0;
      pill.innerHTML = `<span class="job-pill-bar"><span style="width:${pct}%;"></span></span>`
        + `<span>${esc(jobProgressText(j))}${active.length > 1 ? ` \u00b7 +${active.length - 1} queued` : ""}</span>`;
      pill.title = "Open the Scans page";
    }
  }
  for (const j of jobs) {
    if (!j.finished || !seen.has(j.id) || told.has(j.id)) continue;
    told.add(j.id);
    if (j.state === "done") {
      toast(`Detection finished: ${j.title || j.id}. `, 12000, { href: `/scans/${encodeURIComponent(j.id)}`, text: "Open" });
    } else {
      toast(`Detection failed: ${j.error || j.id}`, 12000);
    }
  }
  _sessionSave(JOBS_SEEN, seen); _sessionSave(JOBS_TOLD, told);
  // setTimeout, not requestAnimationFrame: it must keep running in a background tab
  _jobPoll = setTimeout(pollJobs, active.length ? 3000 : 15000);
}

/* ------------------------------------------------------------- shortcuts
   One sheet for the whole console, opened with ? on any page. A page adds its
   own keys by setting window.WF_PAGE_SHORTCUTS to a list of
   { title, keys: [[key, what], ...] } sections; the page letters come last. */
function wfShowShortcuts() {
  if (document.querySelector(".wf-modal-back")) return;
  const off = window.WF_SUPPRESS_NAV_KEYS ? " (off while reviewing boxes)" : "";
  const sections = [...(window.WF_PAGE_SHORTCUTS || []), {
    title: "Anywhere" + off,
    keys: [["D", "Dashboard"], ["S", "Scans"], ["R", "Review"], ["M", "Map"],
           ["P", "Reports"], ["E", "Settings"], ["?", "Show this list"]],
  }];
  const keyHtml = k => k.split(" / ").map(x => `<span class="kbd">${x}</span>`).join(" / ");
  const back = document.createElement("div");
  back.className = "wf-modal-back";
  back.innerHTML = `<div class="wf-modal" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts"
      style="width:min(520px, 100%); max-height:86vh; overflow-y:auto;">
      <div style="font-size:15px; font-weight:600;">Keyboard shortcuts</div>
      ${sections.map(sec => `<div class="shortcut-title">${esc(sec.title)}</div>
        <div class="shortcut-sheet">${sec.keys.map(([k, what]) =>
          `<span>${keyHtml(k)}</span><span>${what}</span>`).join("")}</div>`).join("")}
      <div class="wf-modal-actions"><button type="button" class="btn btn-primary" data-close>Close</button></div>
    </div>`;
  const close = () => { back.remove(); document.removeEventListener("keydown", onKey, true); };
  const onKey = e => { if (e.key === "Escape" || e.key === "?") { e.preventDefault(); e.stopPropagation(); close(); } };
  back.addEventListener("click", e => { if (e.target === back || e.target.closest("[data-close]")) close(); });
  document.addEventListener("keydown", onKey, true);
  document.body.appendChild(back);
  back.querySelector("[data-close]").focus();
}

document.addEventListener("keydown", e => {
  if (e.key !== "?" || e.ctrlKey || e.metaKey || e.altKey) return;
  const tag = document.activeElement?.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || document.activeElement?.isContentEditable) return;
  if (document.querySelector(".wf-modal-back")) return;
  e.preventDefault();
  wfShowShortcuts();
});

/* The chip used to read "Local - Offline" no matter what, which stopped being
   true once cloud sync landed: a run could be uploading and the header still
   claimed offline. It now reports what is actually happening. Processing is
   always local, so that half never changes; only the cloud half does. */
const STATUS = {
  local:    { dot: "var(--green-2)", text: "Local", title: "Everything runs on this machine. Cloud sync is off." },
  cloud:    { dot: "var(--green-2)", text: "Local · Cloud on", title: "Processing is local. Cloud sync is enabled." },
  syncing:  { dot: "var(--amber)",   text: "Local · Syncing…", title: "Uploading results to the cloud container." },
  error:    { dot: "var(--red)",     text: "Local · Sync failed", title: "The last cloud upload failed. See the run's Scan Detail page." },
};

function setStatus(kind) {
  const el = document.getElementById("live-status");
  if (!el) return;
  const s = STATUS[kind] || STATUS.local;
  el.title = s.title;
  el.querySelector(".live-dot").style.background = s.dot;
  el.querySelector(".mono").textContent = s.text;
}

async function refreshStatus() {
  try {
    const { values } = await api("/api/settings");
    if (!values.cloud_enabled) return setStatus("local");
    // Any upload still running anywhere outranks the idle "cloud on" state.
    const jobs = await api("/api/cloud/jobs").catch(() => ({ jobs: [] }));
    const states = (jobs.jobs || []).map(j => j.state);
    setStatus(states.includes("running") ? "syncing"
              : states.includes("error") ? "error" : "cloud");
  } catch (e) {
    setStatus("local");  // settings unreachable: say the safe, true thing
  }
}
// Cheap poll; the endpoint reads in-memory job state, no network of its own.
setInterval(refreshStatus, 4000);

// Load branding once; re-render the nav and recolor when it arrives.
api("/api/branding").then(b => {
  BRANDING = Object.assign(BRANDING, b);
  applyBrandingColors();
  if (_navActive) renderNav(_navActive);
}).catch(() => {});

/* Stylized terrain backdrop for the hazard map (from the design mockup).
   It is a decorative canvas — pins are placed by real GPS, normalized to the
   bounding box of all scan coordinates. */
function terrainSvg(idPrefix) {
  const p = idPrefix;
  return `
  <svg style="position:absolute; inset:0; width:100%; height:100%;" viewBox="0 0 800 500" preserveAspectRatio="xMidYMid slice">
    <defs>
      <filter id="${p}-terr" x="0" y="0" width="100%" height="100%">
        <feTurbulence type="fractalNoise" baseFrequency="0.009 0.014" numOctaves="5" seed="11" stitchTiles="stitch" result="n"></feTurbulence>
        <feComponentTransfer in="n">
          <feFuncR type="table" tableValues="0.05 0.10 0.17 0.27 0.40 0.48"></feFuncR>
          <feFuncG type="table" tableValues="0.11 0.19 0.26 0.32 0.37 0.41"></feFuncG>
          <feFuncB type="table" tableValues="0.05 0.08 0.11 0.13 0.17 0.19"></feFuncB>
          <feFuncA type="table" tableValues="1 1"></feFuncA>
        </feComponentTransfer>
      </filter>
      <filter id="${p}-veg" x="0" y="0" width="100%" height="100%">
        <feTurbulence type="fractalNoise" baseFrequency="0.045 0.055" numOctaves="3" seed="4" result="vn"></feTurbulence>
        <feColorMatrix in="vn" type="matrix" values="0 0 0 0 0.07 0 0 0 0 0.19 0 0 0 0 0.06 0 0 0 1.1 -0.45"></feColorMatrix>
      </filter>
      <radialGradient id="${p}-vig" cx="50%" cy="44%" r="72%">
        <stop offset="52%" stop-color="#000" stop-opacity="0"></stop>
        <stop offset="100%" stop-color="#0a0d09" stop-opacity="0.6"></stop>
      </radialGradient>
    </defs>
    <rect width="800" height="500" fill="#18220f"></rect>
    <rect width="800" height="500" filter="url(#${p}-terr)"></rect>
    <rect width="800" height="500" filter="url(#${p}-veg)" opacity="0.55"></rect>
    <path d="M90 -20 C 150 70, 90 150, 200 230 C 290 296, 360 340, 470 520" stroke="#1f3d4a" stroke-width="9" fill="none" stroke-linecap="round" opacity="0.85"></path>
    <path d="M90 -20 C 150 70, 90 150, 200 230 C 290 296, 360 340, 470 520" stroke="#356a7e" stroke-width="3" fill="none" stroke-linecap="round" opacity="0.7"></path>
    <path d="M-20 372 C 150 340, 330 360, 486 304 C 620 256, 740 244, 820 262" stroke="#0d0f0a" stroke-width="5.5" fill="none" opacity="0.5"></path>
    <path d="M-20 372 C 150 340, 330 360, 486 304 C 620 256, 740 244, 820 262" stroke="#d9cda6" stroke-width="2.4" fill="none" opacity="0.65"></path>
    <rect width="800" height="500" fill="url(#${p}-vig)"></rect>
  </svg>`;
}

/* GPS list -> percentage positions inside the map box (padded bounding box). */
function gpsToPercent(pins) {
  const pts = pins.filter(p => p.lat != null && p.lon != null);
  if (!pts.length) return [];
  let minLat = Math.min(...pts.map(p => p.lat)), maxLat = Math.max(...pts.map(p => p.lat));
  let minLon = Math.min(...pts.map(p => p.lon)), maxLon = Math.max(...pts.map(p => p.lon));
  const latSpan = Math.max(maxLat - minLat, 1e-4), lonSpan = Math.max(maxLon - minLon, 1e-4);
  const PAD = 14; // % padding so edge pins stay inside
  return pts.map(p => ({
    ...p,
    x: PAD + ((p.lon - minLon) / lonSpan) * (100 - 2 * PAD),
    y: PAD + ((maxLat - p.lat) / latSpan) * (100 - 2 * PAD), // north = up
  }));
}

/* 5 decimals, not 3. Sites are deduped at 40 m, but 0.001 degrees is ~111 m of
   latitude and ~70 m of longitude at this latitude, so neighbouring sites kept
   printing the same coordinates and looked like duplicates. 5 decimals is ~1 m
   and matches what the RTK GPS actually delivers. */
function fmtCoord(lat, lon) {
  if (lat == null) return "no GPS";
  const ns = lat >= 0 ? "N" : "S", ew = lon >= 0 ? "E" : "W";
  return `${Math.abs(lat).toFixed(5)}°${ns}, ${Math.abs(lon).toFixed(5)}°${ew}`;
}

/* ---------------------------------------------------------------- shared map
   Site-based Leaflet map used by Dashboard and the Map page (same data, same
   look). preferCanvas is essential: thousands of SVG overlay paths freeze the
   desktop WebView; the canvas renderer handles them easily. */
/* Link to just this site's images inside a run, not the whole run. A site is a
   physical location; opening it should show the frames taken there, not all 58
   images of the flight that happened to include it. Sites can span runs, so
   members are grouped by run and each run gets its own link. */
/* One date format across the console: "Nov 5, 2026 · 21:58". The pages used
   three - "2026-11-05 · 21:58:18", "Nov 05, 2026" and raw EXIF
   "2025:05:02 14:52:30" - sometimes two on one screen. */
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function fmtWhen(iso, { seconds = false } = {}) {
  const m = /^(\d{4})[-:](\d{2})[-:](\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?/.exec(String(iso || ""));
  if (!m) return String(iso || "");
  const [, y, mo, d, hh, mm, ss] = m;
  return `${MONTHS[+mo - 1]} ${+d}, ${y} \u00b7 ${hh}:${mm}${seconds && ss ? ":" + ss : ""}`;
}
const fmtExif = ts => fmtWhen(ts, { seconds: true });   // EXIF "YYYY:MM:DD HH:MM:SS"

/* "1 photo" / "12 photos". Copy like "1 images" is the kind of thing that makes
   a tool read as unfinished, and it was in four places. */
const plural = (n, one, many = one + "s") => `${n} ${n === 1 ? one : many}`;

/* How a run is named on every page: the flight's title in front, the folder id
   small behind it for anyone who needs the exact run. Titles come from the
   photos' capture time unless the operator renamed the run. */
function runLabel(s, { size = 13, link = false } = {}) {
  const title = esc(s.title || s.id || s.run_id || "");
  const id = esc(s.id || s.run_id || "");
  // A name the operator chose no longer says when the flight was, so the date
  // moves into the small line instead of being lost.
  const sub = s.title_custom && s.flown_date ? `${esc(s.flown_date)} \u00b7 ${id}` : id;
  const href = `/scans/${encodeURIComponent(s.id || s.run_id)}`;
  const head = link
    ? `<a href="${href}" class="run-title" style="font-size:${size}px;">${title}</a>`
    : `<span class="run-title" style="font-size:${size}px;">${title}</span>`;
  return `<span class="run-label" title="${title}">${head}<span class="run-id" title="${id}">${sub}</span></span>`;
}

/* Popups name the flight; the page sets this from /api/map-data's run_titles. */
window.WF_RUN_TITLES = window.WF_RUN_TITLES || {};

function siteImageLinks(s, label) {
  const byRun = new Map();
  for (const m of s.members || []) {
    if (!byRun.has(m.run_id)) byRun.set(m.run_id, []);
    byRun.get(m.run_id).push(m.name);
  }
  return [...byRun.entries()].map(([runId, names]) => ({
    runId, names,
    url: `/scans/${encodeURIComponent(runId)}`
       + `?images=${encodeURIComponent(names.join("|"))}`
       + `&from=${encodeURIComponent(label)}`,
  }));
}

function sitePopupHtml(s, i) {
  const members = siteImageLinks(s, `Site ${i + 1}`).map(l => `
    <div style="display:flex; align-items:baseline; justify-content:space-between; gap:8px; margin-top:3px;">
      <span class="mono" style="font-size:10px; color:#6b6b63; overflow:hidden;
            text-overflow:ellipsis; white-space:nowrap;" title="${esc(l.runId)}">${esc(window.WF_RUN_TITLES[l.runId] || l.runId)}</span>
      <a href="${l.url}" style="color:#2D5A2D; font-weight:600; font-size:10px; white-space:nowrap;"
         >${plural(l.names.length, "image")} &rarr;</a>
    </div>`).join("");
  const thumb = ((s.members || []).find(m => m.thumb) || {}).thumb;
  return `
    <div style="display:flex; gap:10px; max-width:250px;">
      ${thumb ? `<img src="${esc(thumb)}" alt="" style="width:70px; height:70px; object-fit:cover; border-radius:6px; border:1px solid #ddd; flex-shrink:0;">` : ""}
      <div style="min-width:0;">
        ${siteKinds(s).map(k => kindBadge(k)).join(" ")} <span class="badge ${s.severity}" style="margin-left:2px;">${SEV[s.severity].label.toUpperCase()}</span>
        <div style="font-size:12px; font-weight:600; margin-top:5px;">Site ${i + 1} · ${s.count} image${s.count === 1 ? "" : "s"}</div>
        <div class="mono" style="font-size:10px; color:#8a8a82;">${fmtCoord(s.lat, s.lon)}</div>
      </div>
    </div>
    <div style="margin-top:8px; border-top:1px solid #eee; padding-top:6px;">${members}</div>`;
}

/* Icon pins are DOM elements (L.divIcon). Thousands of DOM markers would stall
   WebView2 the same way SVG vector layers did, so past this many sites we drop
   back to canvas circles — color only, no icon. The legend still shows icons.
   500 is measured, not guessed: a month can hold 10k+ photos, and at 500 icon
   pins first paint is still well under a second. Raise it only with numbers. */
const MARKER_ICON_LIMIT = 500;

function siteDivIcon(s) {
  const k = kindOf(s);
  const d = Math.round(22 + Math.min(s.count, 10) * 1.4);   // 22..36 px
  const kind = kindKey(s.kind);
  return L.divIcon({
    className: "",   // Leaflet's default adds a white box
    html: `<span class="site-pin" style="width:${d}px; height:${d}px; background:${k.color};">
             ${kindIcon(kind, Math.round(d * 0.6), k.on)}
           </span>`,
    iconSize: [d, d], iconAnchor: [d / 2, d / 2], popupAnchor: [0, -d / 2 - 2],
  });
}

/* Pins for the no-tiles stylized fallback map, shared by Dashboard and Map so
   the two cannot drift apart. `p` comes from gpsToPercent(). */
function fallbackPinHtml(p, selected) {
  const k = kindOf(p);
  const kind = kindKey(p.kind);
  return `
    <div class="pin ${selected ? "selected" : ""}" data-id="${esc(String(p.id))}"
         style="left:${p.x}%; top:${p.y}%;" title="${esc(k.label)} — double-click to open">
      <span class="pin-ring" style="background:${k.color};"></span>
      <span class="site-pin pin-dot" style="width:22px; height:22px; background:${k.color};">
        ${kindIcon(kind, 13, k.on)}
      </span>
      ${p.count > 1 ? `<span class="site-badge" style="position:absolute; left:14px; top:-10px;">${p.count}</span>` : ""}
    </div>`;
}

function initLeafletSites(el, sites, info, opts = {}) {
  el.innerHTML = '<div style="position:absolute; inset:0; background:#161a1b;" class="leaflet-host"></div>';
  const map = L.map(el.querySelector(".leaflet-host"), {
    preferCanvas: true, zoomControl: opts.zoomControl !== false,
  });
  L.tileLayer("/tiles/{z}/{x}/{y}", {
    minZoom: info.min_zoom, maxZoom: info.max_zoom,
    attribution: esc(info.attribution) + " · offline cache",
  }).addTo(map);
  if (opts.overlays && info.overlays) {
    fetch("/map-overlays.geojson").then(r => r.json()).then(gj => {
      L.geoJSON(gj, {
        smoothFactor: 1.5,
        style: f => {
          const p = f.properties || {};
          if (p.natural === "water") return { color: "#2c5566", weight: 1, fillColor: "#1f3d4a", fillOpacity: .55 };
          if (p.waterway) return { color: "#356a7e", weight: 2, opacity: .8 };
          if (["motorway", "trunk", "primary", "secondary"].includes(p.highway))
            return { color: "#d9cda6", weight: 2.5, opacity: .8 };
          return { color: "#cfc09a", weight: 1.2, opacity: .5, dashArray: "4 3" };
        },
      }).addTo(map);
    }).catch(() => {});
  }
  /* Double-click is ours now (open / fullscreen), so Leaflet's own
     double-click-to-zoom has to go or the two gestures fight over every pin.
     Wheel and the zoom control still zoom. */
  if (opts.onOpen) map.doubleClickZoom.disable();

  const useIcons = sites.length <= MARKER_ICON_LIMIT;
  const markers = sites.map((s, i) => {
    const m = useIcons
      ? L.marker([s.lat, s.lon], { icon: siteDivIcon(s), keyboard: false })
      : L.circleMarker([s.lat, s.lon], {
          radius: 8 + Math.min(s.count, 10), color: "#fff", weight: 2,
          fillColor: kindOf(s).color, fillOpacity: .9,
        });
    m.addTo(map).bindPopup(sitePopupHtml(s, i));
    if (opts.onSelect) m.on("click", () => opts.onSelect(i));
    if (opts.onOpen) m.on("dblclick", e => { L.DomEvent.stopPropagation(e); opts.onOpen(i); });
    return m;
  });
  if (sites.length) map.fitBounds(L.latLngBounds(sites.map(s => [s.lat, s.lon])).pad(0.25));
  else map.setView([51.1, -115.4], info.min_zoom || 10);
  return { map, markers, useIcons };
}
