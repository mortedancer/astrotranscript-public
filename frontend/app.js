(() => {
  const $ = (sel, el = document) => el.querySelector(sel);
  const app = $("#app");
  const orig = $("#player-orig");
  const clean = $("#player-clean");

  const state = {
    view: "dashboard",
    projects: [],
    groups: [],
    group: null,
    modal: null,
    showArchived: false,
    root: "",
    project: null,
    filter: "all",
    q: "",
    txHits: null,
    txGen: 0,
    jumpN: null,
    selected: new Set(),
    inspect: null,
    viewT0: 0,
    viewT1: 1,
    playhead: 0,
    settings: null,
    parallel: false,
    showSet: true,
    showIns: true,
    wsDir: "tb",
    wsFrac: 0.46,
    track: "full",
    volOrig: 0.7,
    volClean: 0.85,
    loop: null,
    playing: false,
    peaksOrig: null,
    peaksClean: null,
    followLock: 0,
    txFollow: true,
    playGen: 0,
    seekLock: 0,
    scrubbing: false,
    showFx: false,
    cuts: [],
    uploading: "",
    speakers: [],
    speaker: null,
    followFx: true,
    fxSpeakerId: null,
    keepPlace: false,
    resumeAt: null,
    playRate: 1,
    volMaster: 1,
    session: null,
    openNotes: {},
    editingTitle: null,
    markers: [],
    markersFor: "",
    markersDirty: false,
    markerNote: "",
  };
  let markerRev = 0;
  let markerAck = 0;

  const fmtDur = (s) => {
    if (!s && s !== 0) return "—";
    if (s < 90) return `${s.toFixed(1)}s`;
    const m = s / 60;
    if (m < 90) return `${m.toFixed(1)} min`;
    return `${(s / 3600).toFixed(2)} h`;
  };
  const fmtBytes = (n) => {
    if (!n) return "0 B";
    const u = ["B", "KB", "MB", "GB"];
    let i = 0;
    let x = n;
    while (x >= 1024 && i < u.length - 1) {
      x /= 1024;
      i += 1;
    }
    return `${x.toFixed(i ? 1 : 0)} ${u[i]}`;
  };
  const esc = (s) =>
    String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  async function api(path, opts = {}) {
    const init = { method: opts.method || "GET", headers: {} };
    if (opts.json !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.json);
    }
    const r = await fetch(path, init);
    const ct = r.headers.get("content-type") || "";
    const body = ct.includes("application/json") ? await r.json() : await r.text();
    if (!r.ok) {
      const detail = body && body.detail ? body.detail : `${r.status} ${path}`;
      throw new Error(detail);
    }
    return body;
  }

  function recBase() {
    if (!state.group || !state.project) return "";
    return `/api/projects/${encodeURIComponent(state.group.id)}/recordings/${encodeURIComponent(state.project.id)}`;
  }

  function mediaBase(groupId, stem) {
    return `/api/projects/${encodeURIComponent(groupId)}/recordings/${encodeURIComponent(stem)}`;
  }

  const COMMENT_MAX = 2000;

  function recTitle(p) {
    const t = String((p && p.title) || "").trim();
    return t || (p && p.id) || "";
  }

  function fileNameOf(p) {
    if (p && p.file_name) return p.file_name;
    const src = (p && p.source) || (p && p.job && p.job.source) || "";
    const base = String(src).split(/[/\\]/).pop();
    return base || (p && p.id) || "";
  }

  function oneLine(text, n = 90) {
    const s = String(text || "").replace(/\s+/g, " ").trim();
    if (s.length <= n) return s;
    return `${s.slice(0, n - 1)}…`;
  }

  function pencilSvg() {
    return `<svg viewBox="0 0 16 16" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" d="M9.1 2.9l4 4L6 14H2v-4z"/><path fill="none" stroke="currentColor" stroke-width="1.4" d="M8 4l4 4"/></svg>`;
  }

  function commentTipHtml(text) {
    const tip = String(text || "").trim();
    if (!tip) return "";
    return `<div class="rec-tip" role="tooltip">${esc(tip)}</div>`;
  }

  function routeFromHash() {
    const raw = location.hash.replace(/^#/, "") || "/";
    const qPos = raw.indexOf("?");
    const h = qPos >= 0 ? raw.slice(0, qPos) : raw;
    const params = new URLSearchParams(qPos >= 0 ? raw.slice(qPos + 1) : "");
    const nRaw = params.get("n");
    const extra = {
      n: nRaw && Number(nRaw) > 0 ? Number(nRaw) : null,
      q: params.has("q") ? params.get("q") : null,
    };
    const rec = h.match(/^\/g\/([^/]+)\/r\/([^/?]+)/);
    if (rec) {
      state.view = "workspace";
      return { groupId: decodeURIComponent(rec[1]), stem: decodeURIComponent(rec[2]), ...extra };
    }
    const cuts = h.match(/^\/g\/([^/]+)\/cuts\/?$/);
    if (cuts) {
      state.view = "cuts";
      return { groupId: decodeURIComponent(cuts[1]), stem: null, ...extra };
    }
    const g = h.match(/^\/g\/([^/?]+)/);
    if (g) {
      state.view = "group";
      return { groupId: decodeURIComponent(g[1]), stem: null, speakerId: null, ...extra };
    }
    const spOne = h.match(/^\/speakers\/([^/?]+)/);
    if (spOne) {
      state.view = "speaker";
      return { groupId: null, stem: null, speakerId: decodeURIComponent(spOne[1]), ...extra };
    }
    if (h === "/speakers" || h === "/speakers/") {
      state.view = "speakers";
      return { groupId: null, stem: null, speakerId: null, ...extra };
    }
    state.view = "dashboard";
    return { groupId: null, stem: null, speakerId: null, ...extra };
  }

  async function boot() {
    window.addEventListener("hashchange", () => load());
    window.addEventListener("keydown", onKey);
    orig.addEventListener("timeupdate", onOrigTime);
    orig.addEventListener("ended", onOrigEnded);
    clean.addEventListener("timeupdate", () => {
      if (state.playing && audioTrack() === "vad") onOrigTime();
    });
    clean.addEventListener("ended", onCleanEnded);
    const resumeIfNeeded = (el) => {
      if (state.playing && !state.scrubbing && !state.seekLock && el.paused) el.play().catch(() => {});
    };
    orig.addEventListener("canplay", () => resumeIfNeeded(orig));
    clean.addEventListener("canplay", () => resumeIfNeeded(clean));
    orig.addEventListener("stalled", () => resumeIfNeeded(orig));
    clean.addEventListener("stalled", () => resumeIfNeeded(clean));
    bindFileInput();
    bindSpeakerFile();
    app.addEventListener("click", (e) => {
      const playBtn = e.target.closest("[data-hit-play]");
      if (playBtn && app.contains(playBtn)) {
        e.preventDefault();
        e.stopPropagation();
        toggleHitPreview(playBtn.dataset.stem, +playBtn.dataset.n);
        return;
      }
      const btn = e.target.closest("[data-hit-dl]");
      if (!btn || !app.contains(btn)) return;
      e.preventDefault();
      e.stopPropagation();
      downloadHitAudio(btn.dataset.stem, +btn.dataset.n, btn.dataset.hitDl, btn);
    });
    loadWsLayout();
    bindMini();
    window.addEventListener("resize", () => {
      if (state.view === "workspace") drawTimeline();
    });
    await load();
    scheduleRefresh();
  }

  const WS_STORE = "astro-ws-layout-v1";
  function loadWsLayout() {
    try {
      const raw = JSON.parse(localStorage.getItem(WS_STORE) || "null");
      if (!raw || typeof raw !== "object") return;
      if (raw.split === "lr" || raw.split === "tb") state.wsDir = raw.split;
      if (typeof raw.frac === "number" && Number.isFinite(raw.frac)) {
        state.wsFrac = Math.min(0.78, Math.max(0.22, raw.frac));
      }
      if (typeof raw.set === "boolean") state.showSet = raw.set;
      if (typeof raw.ins === "boolean") state.showIns = raw.ins;
      if (typeof raw.follow === "boolean") state.txFollow = raw.follow;
    } catch {
      /* keep defaults */
    }
  }
  function saveWsLayout() {
    try {
      localStorage.setItem(
        WS_STORE,
        JSON.stringify({
          split: state.wsDir,
          frac: state.wsFrac,
          set: state.showSet,
          ins: state.showIns,
          follow: state.txFollow,
        })
      );
    } catch {
      /* ignore */
    }
  }
  function applyWsSplit() {
    const center = app.querySelector(".center");
    if (center) center.style.setProperty("--split", `${(state.wsFrac * 100).toFixed(1)}%`);
  }
  function bindSplitHandle() {
    const handle = $("#ws-split");
    const center = app.querySelector(".center");
    if (!handle || !center) return;
    handle.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      handle.setPointerCapture(e.pointerId);
      handle.classList.add("drag");
      const tb = state.wsDir !== "lr";
      const move = (ev) => {
        const r = center.getBoundingClientRect();
        const t = tb ? (ev.clientY - r.top) / Math.max(1, r.height) : (ev.clientX - r.left) / Math.max(1, r.width);
        state.wsFrac = Math.min(0.78, Math.max(0.22, t));
        applyWsSplit();
        drawTimeline();
      };
      const up = (ev) => {
        try {
          handle.releasePointerCapture(ev.pointerId);
        } catch {
          /* ignore */
        }
        handle.classList.remove("drag");
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", up);
        saveWsLayout();
        drawTimeline();
      };
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", up);
    });
  }

  function bindSpeakerFile() {
    const fileIn = $("#speaker-file");
    if (!fileIn || fileIn.dataset.bound) return;
    fileIn.dataset.bound = "1";
    fileIn.addEventListener("change", async () => {
      const files = fileIn.files;
      fileIn.value = "";
      await ingestSpeakerSamples(files);
    });
  }

  function bindFileInput() {
    const fileIn = $("#rec-file");
    if (!fileIn || fileIn.dataset.bound) return;
    fileIn.dataset.bound = "1";
    fileIn.addEventListener("change", async () => {
      const files = fileIn.files;
      fileIn.value = "";
      await ingestFiles(files);
    });
  }

  function pollRenderBlocked() {
    if (state.modal || state.uploading) return true;
    const ae = document.activeElement;
    if (!ae) return false;
    if (ae.id === "dash-q") return true;
    const tag = (ae.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select";
  }

  let refreshTimer = 0;
  function scheduleRefresh() {
    clearTimeout(refreshTimer);
    const items = (state.group && state.group.items) || [];
    const wsBusy =
      state.view === "workspace" &&
      state.project &&
      ["queued", "vad", "processing"].includes(state.project.status);
    const busy =
      wsBusy || items.some((p) => ["queued", "vad", "processing"].includes(p.status));
    refreshTimer = setTimeout(async () => {
      await refreshList();
      scheduleRefresh();
    }, busy ? 1200 : 3000);
  }

  async function refreshList() {
    try {
      const q = state.showArchived ? "/api/projects?archived=1" : "/api/projects";
      const data = await api(q);
      state.groups = data.projects || [];
      state.root = data.root || "";
      if (state.view === "group" && state.group) {
        state.group = await api(`/api/projects/${encodeURIComponent(state.group.id)}`);
      }
      if (state.view === "workspace" && state.project && state.group) {
        const st = state.project.status;
        if (["queued", "vad", "processing"].includes(st)) {
          try {
            const job = await api(
              `/api/projects/${encodeURIComponent(state.group.id)}/recordings/${encodeURIComponent(state.project.id)}/job`
            );
            state.project.status = job.status || state.project.status;
            state.project.job = { ...(state.project.job || {}), ...job };
            if (job.status === "completed" || job.status === "error") {
              await load();
              return;
            }
            const live = app.querySelector(".job-live");
            if (live) live.innerHTML = jobLiveInner(jobProgressOf({ ...state.project, job }));
            await syncWorkspaceLive(job);
          } catch {
            /* keep last */
          }
        }
      }
      if (state.view === "dashboard" || state.view === "group" || state.view === "cuts") {
        if (!pollRenderBlocked()) render();
      }
    } catch {
      /* keep last */
    }
  }

  async function load() {
    freezeSession();
    const route = routeFromHash();
    if (state.view !== "group") stopHitPreview();
    try {
      const q = state.showArchived ? "/api/projects?archived=1" : "/api/projects";
      const data = await api(q);
      state.groups = data.projects || [];
      state.root = data.root || "";
    } catch (e) {
      app.innerHTML = `<div class="empty err">API недоступен: ${esc(e.message)}</div>`;
      return;
    }
    if (route.groupId) {
      try {
        const prevId = state.group && state.group.id;
        state.group = await api(`/api/projects/${encodeURIComponent(route.groupId)}`);
        if (prevId !== state.group.id) {
          state.txHits = null;
          stopHitPreview();
        }
      } catch (e) {
        app.innerHTML = `<div class="empty err">Проект не найден: ${esc(e.message)}</div>`;
        return;
      }
    } else {
      state.group = null;
      state.txHits = null;
    }
    if (route.stem) {
      try {
        const sameRec = state.project && state.group && state.project.id === route.stem && state.group.id === route.groupId;
        const sameSession = sessionMatches(route.groupId, route.stem);
        const keep = state.keepPlace && sameRec
          ? {
              playhead: state.playhead,
              viewT0: state.viewT0,
              viewT1: state.viewT1,
              playing: state.playing,
              loop: state.loop,
              selected: new Set(state.selected),
              inspectN: state.inspect && state.inspect.n,
              track: state.track,
              q: state.q,
              peaksOrig: state.peaksOrig,
              peaksClean: state.peaksClean,
            }
          : null;
        state.keepPlace = false;
        const markerSnap = { rev: markerRev, ack: markerAck };
        state.project = await api(
          `/api/projects/${encodeURIComponent(route.groupId)}/recordings/${encodeURIComponent(route.stem)}`
        );
        adoptMarkers(route.groupId, route.stem, markerSnap);
        const d = state.project.duration_sec || 1;
        state.settings = state.project.pipeline;
        const segs = state.project.segments_detail || [];
        state.followFx = state.project.follow_fx !== false;
        state.fxSpeakerId = null;
        state.spkThresh = threshOf(state.project);
        if (sameSession && state.session && state.session.live) {
          state.session.project = state.project;
          state.session.title = recTitle(state.project);
          state.session.fileName = fileNameOf(state.project);
          state.session.groupId = route.groupId;
          const head = Math.min(Math.max(0, state.session.playhead || 0), d);
          state.playhead = head;
          state.session.playhead = head;
          state.playing = !!state.session.playing;
          state.loop = state.session.loop || null;
          state.track = state.session.track === "vad" ? "vad" : "full";
          if (keep) {
            state.viewT0 = Math.min(Math.max(0, keep.viewT0), d);
            state.viewT1 = Math.min(Math.max(state.viewT0 + 0.05, keep.viewT1), d);
            if (state.viewT1 <= state.viewT0) state.viewT1 = d;
            state.q = route.q != null ? route.q : keep.q || "";
            state.peaksOrig = keep.peaksOrig;
            state.peaksClean = keep.peaksClean;
            state.selected = keep.selected;
            state.inspect =
              (route.n && segs.find((s) => s.n === route.n)) ||
              segs.find((s) => s.n === keep.inspectN) ||
              segs[0] ||
              null;
          } else {
            const span = Math.min(d, Math.max(40, Math.min(d, 180)));
            state.viewT0 = Math.max(0, head - span * 0.25);
            state.viewT1 = Math.min(d, state.viewT0 + span);
            state.viewT0 = Math.max(0, state.viewT1 - span);
            state.q = route.q || "";
            state.inspect = (route.n && segs.find((s) => s.n === route.n)) || segs[0] || null;
            state.selected = new Set();
            state.peaksOrig = null;
            state.peaksClean = null;
          }
          state.jumpN = route.n;
          state.parallel = false;
          if (!state.peaksOrig && !state.peaksClean) loadPeaks(route.stem);
        } else if (keep) {
          state.viewT0 = Math.min(Math.max(0, keep.viewT0), d);
          state.viewT1 = Math.min(Math.max(state.viewT0 + 0.05, keep.viewT1), d);
          if (state.viewT1 <= state.viewT0) state.viewT1 = d;
          state.q = route.q != null ? route.q : keep.q || "";
          state.jumpN = route.n;
          state.inspect =
            (state.jumpN && segs.find((s) => s.n === state.jumpN)) ||
            segs.find((s) => s.n === keep.inspectN) ||
            segs[0] ||
            null;
          state.selected = keep.selected;
          state.parallel = false;
          state.playhead = Math.min(Math.max(0, keep.playhead || 0), d);
          state.loop = keep.loop;
          state.track = keep.track === "vad" ? "vad" : "full";
          state.peaksOrig = keep.peaksOrig;
          state.peaksClean = keep.peaksClean;
          const foreign = state.session && state.session.live && !sameSession;
          if (!foreign) {
            state.playing = false;
            orig.pause();
            clean.pause();
            if (keep.playing) state.resumeAt = state.playhead;
          } else {
            state.playing = !!state.session.playing;
          }
          if (!state.peaksOrig && !state.peaksClean) loadPeaks(route.stem);
        } else {
          state.viewT0 = 0;
          state.viewT1 = d;
          state.q = route.q || "";
          state.jumpN = route.n;
          state.inspect = (state.jumpN && segs.find((s) => s.n === state.jumpN)) || segs[0] || null;
          state.selected = new Set();
          state.parallel = false;
          state.playhead = 0;
          state.loop = null;
          state.peaksOrig = null;
          state.peaksClean = null;
          if (state.session && state.session.live) {
            state.playing = !!state.session.playing;
          } else {
            state.playing = false;
            stopPlay(false);
          }
          loadPeaks(route.stem);
        }
      } catch (e) {
        state.project = null;
        state.markers = [];
        state.markersFor = "";
        state.markersDirty = false;
        app.innerHTML = `<div class="empty err">Запись не найдена: ${esc(e.message)}</div>`;
        syncMini();
        return;
      }
    } else {
      state.project = null;
      state.markers = [];
      state.markersFor = "";
      state.markersDirty = false;
    }
    if (state.view === "cuts" && state.group) {
      try {
        const data = await api(`/api/projects/${encodeURIComponent(state.group.id)}/cuts`);
        state.cuts = data.cuts || [];
      } catch {
        state.cuts = [];
      }
    }
    if (state.view === "speakers" || state.view === "speaker" || state.view === "workspace") {
      try {
        const data = await api("/api/speakers");
        state.speakers = data.speakers || [];
      } catch {
        state.speakers = [];
      }
    }
    if (state.view === "speaker" && route.speakerId) {
      try {
        state.speaker = await api(`/api/speakers/${encodeURIComponent(route.speakerId)}`);
      } catch (e) {
        state.speaker = null;
        app.innerHTML = `<div class="empty err">Диктор не найден: ${esc(e.message)}</div>`;
        return;
      }
    } else if (state.view !== "speaker") {
      state.speaker = null;
    }
    render();
    if (state.resumeAt != null) {
      const t = state.resumeAt;
      state.resumeAt = null;
      playFrom(t);
    }
  }

  function render() {
    if (state.view === "workspace" && state.project) renderWorkspace();
    else if (state.view === "cuts" && state.group) renderCuts();
    else if (state.view === "group" && state.group) renderGroup();
    else if (state.view === "speaker" && state.speaker) renderSpeaker();
    else if (state.view === "speakers") renderSpeakers();
    else renderDashboard();
    if (state.modal) {
      app.insertAdjacentHTML("beforeend", modalHtml());
      bindModal();
    }
    syncFxPanel();
    syncMini();
    focusTitleEditor();
  }

  function topbar(extra = "") {
    return `
      <header class="topbar">
        <div class="brand">
          <div class="brand-mark" aria-hidden="true"></div>
          <div>AstroTranscript
            <small>wall-clock transcripts</small>
          </div>
        </div>
        <nav class="tabs" aria-label="Разделы">
          <button class="tab ${state.view === "dashboard" ? "active" : ""}" data-go="#/">Dashboard</button>
          <button class="tab ${state.view === "workspace" ? "active" : ""}" data-go="${state.project && state.group ? `#/g/${state.group.id}/r/${state.project.id}` : "#/"}">Workspace</button>
        </nav>
        ${extra}
        <span class="pill mono">${esc(state.root || "")}</span>
      </header>`;
  }

  function groupTabs(g) {
    if (!g) return "";
    const recOn = state.view === "group";
    const cutsOn = state.view === "cuts";
    const n = cutsOn ? (state.cuts || []).length : g.cuts_count || 0;
    const cutsLab = n ? `Cuts (${n})` : "Cuts";
    return `
      <button class="chip ${recOn ? "on" : ""}" data-go="#/g/${encodeURIComponent(g.id)}">Recordings</button>
      <button class="chip ${cutsOn ? "on" : ""}" data-go="#/g/${encodeURIComponent(g.id)}/cuts">${cutsLab}</button>`;
  }

  function sideNav() {
    const on = state.view === "dashboard" || state.view === "group" || state.view === "cuts";
    const spOn = state.view === "speakers" || state.view === "speaker";
    const bytes = (state.groups || []).reduce((a, p) => a + (p.bytes || 0), 0);
    const usedPct = Math.min(100, (bytes / (500 * 1024 * 1024)) * 100);
    return `
        <aside class="side">
          <button class="nav-item ${on ? "active" : ""}" data-go="#/">Dashboard</button>
          <button class="nav-item ${on ? "active" : ""}" data-go="#/">Projects</button>
          <button class="nav-item ${spOn ? "active" : ""}" data-go="#/speakers">Speakers</button>
          <button class="nav-item" disabled>Audio Files</button>
          <button class="nav-item" disabled>Transcripts</button>
          <div class="storage">
            <div class="muted">Storage used</div>
            <div class="bar"><i style="width:${usedPct.toFixed(0)}%"></i></div>
            <div class="faint">${fmtBytes(bytes)} processed</div>
          </div>
          ${spOn
            ? `<button class="btn-new" data-act="new-speaker">New speaker</button>`
            : `<button class="btn-new" data-act="new-project">New project</button>`}
        </aside>`;
  }

  function renderDashboard() {
    const items = state.groups || [];
    app.innerHTML = `
      ${topbar(`<input class="search" id="dash-q" placeholder="Search projects…" value="${esc(state.q)}" />`)}
      <div class="dash">
        ${sideNav()}
        <section class="main">
          <h1>Projects</h1>
          <p class="sub">Именованные папки записей. Старые папки диктофона переезжают в Inbox.</p>
          <div class="filters">
            <button class="chip ${!state.showArchived ? "on" : ""}" data-archived="0">All</button>
            <button class="chip ${state.showArchived ? "on" : ""}" data-archived="1">Archived</button>
            <span class="muted">${items.length} of ${state.groups.length}</span>
          </div>
          <div class="grid">
            ${items.map(groupCardHtml).join("") || `<div class="empty">Нет проектов в ${esc(state.root)}</div>`}
          </div>
        </section>
      </div>`;
    bindDash();
  }

  function groupCardHtml(p) {
    return `
      <article class="card group-card ${p.archived ? "archived" : ""}" tabindex="0" data-open-group="${esc(p.id)}" role="button">
        <div class="card-top">
          <h3>${esc(p.name)}</h3>
          <span class="badge ${p.archived ? "pending" : "completed"}">${p.archived ? "archived" : p.id === "inbox" ? "inbox" : "active"}</span>
        </div>
        <div class="meta">
          <span>${p.recordings} recordings · ${fmtDur(p.duration_sec)}</span>
          <span class="mono faint">${esc(p.id)}</span>
        </div>
        <div class="updated">${fmtBytes(p.bytes)}</div>
        <div class="card-actions" data-stop="1">
          <button type="button" data-rename="${esc(p.id)}">Rename</button>
          ${p.protected ? "" : `<button type="button" data-archive="${esc(p.id)}">Archive</button>
          <button type="button" data-delete="${esc(p.id)}">Delete</button>`}
        </div>
      </article>`;
  }

  function renderSpeakers() {
    const items = state.speakers || [];
    app.innerHTML = `
      ${topbar(`<input class="search" id="dash-q" placeholder="Search speakers…" value="${esc(state.q)}" />`)}
      <div class="dash">
        ${sideNav()}
        <section class="main">
          <h1>Speakers</h1>
          <p class="sub">Общий каталог голосов вне проектов. Добавь эталон → потом запись матчится к дикторам.</p>
          <div class="grid">
            ${items.map(speakerCardHtml).join("") || `<div class="empty">Пока нет дикторов. New speaker — имя, потом короткий WAV.</div>`}
          </div>
        </section>
      </div>`;
    bindDash();
    bindSpeakerCards();
  }

  function speakerCardHtml(s) {
    const n = s.samples_count != null ? s.samples_count : (s.samples || []).length;
    return `
      <article class="card group-card" tabindex="0" data-open-speaker="${esc(s.id)}" role="button">
        <div class="card-top">
          <h3>${esc(s.name)}</h3>
          <span class="badge completed">${n} sample${n === 1 ? "" : "s"}</span>
        </div>
        <div class="meta">
          <span class="mono faint">${esc(s.id)}</span>
        </div>
        <div class="card-actions" data-stop="1">
          <button type="button" data-rename-speaker="${esc(s.id)}">Rename</button>
          <button type="button" data-delete-speaker="${esc(s.id)}">Delete</button>
        </div>
      </article>`;
  }

  function renderSpeaker() {
    const s = state.speaker;
    const samples = s.samples || [];
    app.innerHTML = `
      ${topbar(`<span class="pill">Speakers</span>`)}
      <div class="dash">
        ${sideNav()}
        <section class="main">
          <div class="filters">
            <button class="icon-btn" data-go="#/speakers">←</button>
            <h1 style="margin:0">${esc(s.name)}</h1>
            <span class="mono faint">${esc(s.id)}</span>
            <span style="margin-left:auto;display:flex;gap:8px">
              <button class="icon-btn" data-act="add-sample-path">Add path</button>
              <button class="btn-new" style="margin:0;width:auto;padding:8px 14px" data-act="add-sample-file">Add sample</button>
            </span>
          </div>
          <p class="sub">Короткий клип голоса (до 12 МБ). Drop wav/mp3 сюда. Не тащи часовой WAV диктофона.</p>
          <div class="grid drop-zone" id="spk-drop">
            ${samples.map(sampleCardHtml).join("") || `<div class="empty drop-hint">Нет эталонов. Drop a short wav/mp3 here, or Add sample.</div>`}
          </div>
        </section>
      </div>`;
    bindDash();
    bindSpeakerDetail();
  }

  function sampleCardHtml(sm) {
    const href = sm.url || `/api/speakers/${encodeURIComponent(state.speaker.id)}/samples/${encodeURIComponent(sm.id)}`;
    return `
      <article class="card cut-card">
        <div class="card-top">
          <h3>${esc(sm.filename || sm.id)}</h3>
          <span class="badge completed">${fmtDur(sm.duration_sec)}</span>
        </div>
        <div class="meta">
          <span class="mono faint">${esc(sm.id)}</span>
          <span>${fmtBytes(sm.bytes)}</span>
        </div>
        <audio controls preload="none" src="${esc(href)}"></audio>
        <div class="card-actions">
          <button type="button" data-del-sample="${esc(sm.id)}">Remove</button>
        </div>
      </article>`;
  }

  function bindSpeakerCards() {
    app.querySelectorAll("[data-open-speaker]").forEach((el) => {
      el.addEventListener("click", (e) => {
        if (e.target.closest("[data-stop]")) return;
        location.hash = `#/speakers/${encodeURIComponent(el.dataset.openSpeaker)}`;
      });
    });
    app.querySelectorAll("[data-rename-speaker]").forEach((b) =>
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        openRenameSpeaker(b.dataset.renameSpeaker);
      })
    );
    app.querySelectorAll("[data-delete-speaker]").forEach((b) =>
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        openDeleteSpeaker(b.dataset.deleteSpeaker);
      })
    );
  }

  function bindSpeakerDetail() {
    const addFile = app.querySelector("[data-act=add-sample-file]");
    const addPath = app.querySelector("[data-act=add-sample-path]");
    const fileIn = $("#speaker-file");
    if (addFile && fileIn) addFile.addEventListener("click", () => fileIn.click());
    if (addPath) addPath.addEventListener("click", () => openAddSpeakerPath());
    bindDropZone($("#spk-drop"), ingestSpeakerSamples);
    app.querySelectorAll("[data-del-sample]").forEach((b) =>
      b.addEventListener("click", async () => {
        try {
          await api(
            `/api/speakers/${encodeURIComponent(state.speaker.id)}/samples/${encodeURIComponent(b.dataset.delSample)}`,
            { method: "DELETE" }
          );
          await load();
        } catch (err) {
          openModal({ title: "Sample", text: err.message, fields: [], submitLabel: "OK", onSubmit: async () => {} });
        }
      })
    );
  }

  function openNewSpeaker() {
    openModal({
      title: "New speaker",
      submitLabel: "Create",
      text: "Имя диктора. Эталон голоса добавишь на следующей странице.",
      fields: [{ name: "name", label: "Name", type: "text", value: "" }],
      error: "",
      onSubmit: async (v) => {
        const sp = await api("/api/speakers", { method: "POST", json: { name: v.name } });
        location.hash = `#/speakers/${encodeURIComponent(sp.id)}`;
      },
    });
  }

  function openRenameSpeaker(id) {
    const s = (state.speakers || []).find((x) => x.id === id) || state.speaker;
    openModal({
      title: "Rename speaker",
      submitLabel: "Save",
      fields: [{ name: "name", label: "Name", type: "text", value: (s && s.name) || "" }],
      error: "",
      onSubmit: async (v) => {
        await api(`/api/speakers/${encodeURIComponent(id)}`, { method: "PATCH", json: { name: v.name } });
      },
    });
  }

  function openDeleteSpeaker(id) {
    openModal({
      title: "Delete speaker",
      submitLabel: "Delete",
      text: "Эталоны голоса удалятся. Записи проектов не трогаем.",
      fields: [],
      error: "",
      onSubmit: async () => {
        await api(`/api/speakers/${encodeURIComponent(id)}`, { method: "DELETE" });
        if (state.view === "speaker") location.hash = "#/speakers";
      },
    });
  }

  function openAddSpeakerPath() {
    openModal({
      title: "Add voice sample",
      submitLabel: "Add",
      text: "Полный путь к короткому WAV/MP3 на этой машине.",
      fields: [{ name: "path", label: "Server path", type: "text", value: "" }],
      error: "",
      onSubmit: async (v) => {
        const path = (v.path || "").trim();
        if (!path) throw new Error("Нужен путь к короткому клипу");
        await api(`/api/speakers/${encodeURIComponent(state.speaker.id)}/samples`, {
          method: "POST",
          json: { path },
        });
      },
    });
  }

  async function ingestSpeakerSamples(fileList) {
    if (!fileList || !fileList.length || !state.speaker) return;
    const files = [...fileList].filter((f) => /\.(wav|mp3|m4a|aac|ogg|flac)$/i.test(f.name));
    if (!files.length) {
      openModal({
        title: "Sample",
        text: "Нужен короткий wav/mp3 (до 12 МБ).",
        fields: [],
        submitLabel: "OK",
        onSubmit: async () => {},
      });
      return;
    }
    const errors = [];
    for (const file of files) {
      try {
        const body = new FormData();
        body.append("file", file, file.name);
        const r = await fetch(`/api/speakers/${encodeURIComponent(state.speaker.id)}/samples/upload`, {
          method: "POST",
          body,
        });
        const data = (r.headers.get("content-type") || "").includes("application/json") ? await r.json() : await r.text();
        if (!r.ok) throw new Error((data && data.detail) || `${r.status} upload`);
      } catch (err) {
        errors.push(`${file.name}: ${err.message || err}`);
      }
    }
    await load();
    if (errors.length) {
      openModal({
        title: "Some samples failed",
        text: errors.join(" · "),
        fields: [],
        submitLabel: "OK",
        onSubmit: async () => {},
      });
    }
  }

  function renderGroup() {
    const g = state.group;
    const items = g.items || [];
    const archived = !!g.archived;
    const busy = items.filter((p) => ["queued", "vad", "processing"].includes(p.status));
    const busyNote = busy
      .map((p) => {
        const lab = jobProgressOf(p) && jobProgressOf(p).label;
        return lab ? `${p.id} (${lab})` : p.id;
      })
      .join(", ");
    const others = (state.groups || []).filter((x) => !x.archived && x.id !== g.id);
    const hitsById = txHitsById();
    app.innerHTML = `
      ${topbar(`<input class="search" id="dash-q" type="search" placeholder="Search recordings and transcripts…" value="${esc(state.q)}" aria-label="Search recordings and transcripts" />`)}
      <div class="dash">
        ${sideNav()}
        <section class="main">
          <div class="filters">
            <button class="icon-btn" data-go="#/">←</button>
            <h1 style="margin:0">${esc(g.name)}</h1>
            ${archived ? `<span class="badge pending">archived</span>` : ""}
            ${groupTabs(g)}
            <span class="muted">${items.length} recordings · ${fmtDur(g.duration_sec)}</span>
            <span class="search-status muted" id="search-status"></span>
            <span style="margin-left:auto;display:flex;gap:8px">
              ${archived ? `<button class="icon-btn" data-act="restore-group">Restore</button>` : `
              <button class="icon-btn" data-act="add-path">Add path</button>
              <button class="btn-new" style="margin:0;width:auto;padding:8px 14px" data-act="add-rec">Add recording</button>`}
            </span>
          </div>
          <p class="sub">Drop WAV files here, or use Add recording. Для файлов диктофона на сотни МБ лучше Add path — без копирования через браузер.</p>
          ${busy.length ? `<p class="sub">Processing: ${esc(busyNote)}</p>` : ""}
          ${state.uploading ? `<p class="sub" id="upload-status">${esc(state.uploading)}</p>` : `<p class="sub" id="upload-status" hidden></p>`}
          <div class="grid ${archived ? "" : "drop-zone"}" id="rec-drop">
            ${items.map((p) => recCardHtml(p, others, archived, hitsById[p.id])).join("") || `<div class="empty drop-hint">Нет записей. Drop a WAV here or click Add recording.</div>`}
            <div class="empty search-empty" id="search-empty" hidden>Ничего не найдено по «${esc((state.q || "").trim())}». Попробуйте другое слово из расшифровки.</div>
          </div>
        </section>
      </div>`;
    bindDash();
    bindGroup();
    applyGroupFilter();
    focusTitleEditor();
  }

  function renderCuts() {
    const g = state.group;
    const items = state.cuts || [];
    app.innerHTML = `
      ${topbar(`<span class="pill">Cuts</span>`)}
      <div class="dash">
        ${sideNav()}
        <section class="main">
          <div class="filters">
            <button class="icon-btn" data-go="#/">←</button>
            <h1 style="margin:0">${esc(g.name)}</h1>
            ${groupTabs(g)}
            <span class="muted">${items.length} cuts</span>
          </div>
          <p class="sub">Петли с FX из плеера. Скачайте MP3 или удалите слот.</p>
          <div class="grid">
            ${items.map(cutCardHtml).join("") || `<div class="empty">Пока пусто. В workspace выделите петлю и в панели FX нажмите «Петля → cuts».</div>`}
          </div>
        </section>
      </div>`;
    bindDash();
    app.querySelectorAll("[data-cut-del]").forEach((b) =>
      b.addEventListener("click", async (e) => {
        e.stopPropagation();
        try {
          await api(
            `/api/projects/${encodeURIComponent(g.id)}/cuts/${encodeURIComponent(b.dataset.cutDel)}/remove`,
            { method: "POST" }
          );
          await load();
        } catch (err) {
          openModal({ title: "Cut", text: err.message, fields: [], submitLabel: "OK", onSubmit: async () => {} });
        }
      })
    );
  }

  function cutCardHtml(c) {
    const href = c.url || `/api/projects/${encodeURIComponent(state.group.id)}/cuts/${encodeURIComponent(c.id)}/download`;
    const dlName = `${(c.name || c.id).replace(/[^\w.\-]+/g, "_").slice(0, 80)}.mp3`;
    return `
      <article class="card cut-card">
        <div class="card-top">
          <h3>${esc(c.name || c.id)}</h3>
          <span class="badge completed">mp3</span>
        </div>
        <div class="meta">
          <span class="mono faint">${esc(c.recording_id || "")}</span>
          <span>${fmtDur(c.duration_sec)} · ${fmtBytes(c.bytes)}</span>
          <span>${c.fx ? "FX on" : "dry"}</span>
        </div>
        <audio controls preload="none" src="${esc(href)}"></audio>
        <div class="card-actions">
          <a class="icon-btn" href="${esc(href)}" download="${esc(dlName)}">Скачать</a>
          <button type="button" data-cut-del="${esc(c.id)}">Remove</button>
        </div>
      </article>`;
  }

  function bindGroup() {
    const add = app.querySelector("[data-act=add-rec]");
    const addPath = app.querySelector("[data-act=add-path]");
    const fileIn = $("#rec-file");
    if (add)
      add.addEventListener("click", () => {
        if (fileIn) fileIn.click();
        else openAddRecording();
      });
    if (addPath) addPath.addEventListener("click", () => openAddRecording());
    bindDropZone($("#rec-drop"));
    const restore = app.querySelector("[data-act=restore-group]");
    if (restore)
      restore.addEventListener("click", async () => {
        await api(`/api/projects/${encodeURIComponent(state.group.id)}/restore`, { method: "POST" });
        await load();
      });
    app.querySelectorAll("[data-process]").forEach((b) =>
      b.addEventListener("click", async (e) => {
        e.stopPropagation();
        try {
          await api(
            `/api/projects/${encodeURIComponent(state.group.id)}/recordings/${encodeURIComponent(b.dataset.process)}/process`,
            { method: "POST" }
          );
          await load();
        } catch (err) {
          openModal({ title: "Process", text: err.message, fields: [], submitLabel: "OK", onSubmit: async () => {} });
        }
      })
    );
    app.querySelectorAll("[data-to-inbox]").forEach((b) =>
      b.addEventListener("click", async (e) => {
        e.stopPropagation();
        await api(
          `/api/projects/${encodeURIComponent(state.group.id)}/recordings/${encodeURIComponent(b.dataset.toInbox)}/move`,
          { method: "POST", json: { to: "inbox" } }
        );
        await load();
      })
    );
    app.querySelectorAll("[data-move]").forEach((sel) =>
      sel.addEventListener("change", async (e) => {
        e.stopPropagation();
        const to = sel.value;
        if (!to) return;
        await api(
          `/api/projects/${encodeURIComponent(state.group.id)}/recordings/${encodeURIComponent(sel.dataset.move)}/move`,
          { method: "POST", json: { to } }
        );
        await load();
      })
    );
    app.querySelectorAll("[data-move]").forEach((sel) =>
      sel.addEventListener("click", (e) => e.stopPropagation())
    );
    app.querySelectorAll("[data-remove]").forEach((b) =>
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        openRemoveRecording(b.dataset.remove);
      })
    );
  }

  function openRemoveRecording(stem) {
    const items = (state.group && state.group.items) || [];
    const rec = items.find((x) => x.id === stem) || {};
    const processed = ["completed", "vad", "processing"].includes(rec.status) || rec.has_transcript || rec.spans > 0;
    const proj = (state.group && state.group.name) || "this project";
    const text = processed
      ? `“${stem}” is already processed in ${proj}. Transcript, clips, and VAD maps will be permanently deleted. Original dictaphone files outside the project folder are kept.`
      : `Remove “${stem}” from ${proj}? Project-store files will be deleted. Original WAVs outside the folder are kept.`;
    openModal({
      title: processed ? "Remove processed recording" : "Remove recording",
      submitLabel: processed ? "Delete processed files" : "Remove",
      text,
      fields: [],
      error: "",
      onSubmit: async () => {
        await api(
          `/api/projects/${encodeURIComponent(state.group.id)}/recordings/${encodeURIComponent(stem)}/remove?confirm=1`,
          { method: "POST" }
        );
      },
    });
  }

  function bindDropZone(el, onFiles) {
    if (!el) return;
    if (!onFiles) {
      if (!state.group || state.group.archived) return;
      onFiles = ingestFiles;
    }
    const on = (e) => {
      e.preventDefault();
      e.stopPropagation();
      el.classList.add("drop-on");
    };
    const off = (e) => {
      e.preventDefault();
      if (!el.contains(e.relatedTarget)) el.classList.remove("drop-on");
    };
    el.addEventListener("dragenter", on);
    el.addEventListener("dragover", on);
    el.addEventListener("dragleave", off);
    el.addEventListener("drop", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      el.classList.remove("drop-on");
      await onFiles(e.dataTransfer && e.dataTransfer.files);
    });
  }

  function openAddRecording() {
    openModal({
      title: "Add recording",
      submitLabel: "Add",
      text: "Полный путь на этой машине, например I:\\R20250827-115520.WAV. Для файлов диктофона (~500 МБ) путь надёжнее, чем drag-drop.",
      fields: [{ name: "path", label: "Server path", type: "text", value: "" }],
      error: "",
      onSubmit: async (v) => {
        const path = (v.path || "").trim();
        if (!path) throw new Error("Нужен полный путь к WAV/MP3 или к папке с speech_map.json");
        const rec = await api(`/api/projects/${encodeURIComponent(state.group.id)}/recordings`, {
          method: "POST",
          json: { path },
        });
        await maybeProcess(rec);
      },
    });
  }

  async function maybeProcess(rec) {
    if (!rec || rec.status !== "pending" || !state.group) return;
    try {
      await api(
        `/api/projects/${encodeURIComponent(state.group.id)}/recordings/${encodeURIComponent(rec.id)}/process`,
        { method: "POST" }
      );
    } catch {
      /* listed as pending */
    }
  }

  function setUploadStatus(text) {
    state.uploading = text || "";
    const el = app.querySelector("#upload-status");
    if (!el) {
      if (text) render();
      return;
    }
    if (!text) {
      el.hidden = true;
      el.textContent = "";
      return;
    }
    el.hidden = false;
    el.textContent = text;
  }

  function uploadRecording(pid, file, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `/api/projects/${encodeURIComponent(pid)}/recordings/upload`);
      xhr.onload = () => {
        let data = xhr.responseText;
        const ct = xhr.getResponseHeader("content-type") || "";
        if (ct.includes("application/json")) {
          try {
            data = JSON.parse(xhr.responseText);
          } catch {
            /* keep text */
          }
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(data);
          return;
        }
        const detail = data && data.detail ? data.detail : `${xhr.status} upload`;
        reject(new Error(typeof detail === "string" ? detail : JSON.stringify(detail)));
      };
      xhr.onerror = () => reject(new Error("upload failed"));
      xhr.upload.onprogress = (e) => {
        if (onProgress && e.lengthComputable) onProgress(e.loaded, e.total);
      };
      const body = new FormData();
      body.append("file", file, file.name);
      xhr.send(body);
    });
  }

  async function ingestFiles(fileList) {
    if (!fileList || !fileList.length || !state.group) return;
    const wavs = [...fileList].filter((f) => /\.(wav|mp3|m4a|aac|ogg|flac)$/i.test(f.name));
    if (!wavs.length) {
      openModal({
        title: "Add recording",
        text: "Drop WAV or MP3 (also m4a/ogg/flac). For a processed folder, use a server path.",
        fields: [],
        submitLabel: "OK",
        onSubmit: async () => {},
      });
      return;
    }
    const errors = [];
    const pid = state.group.id;
    for (const file of wavs) {
      setUploadStatus(`${file.name} · 0% · ${fmtBytes(file.size)}`);
      try {
        const data = await uploadRecording(pid, file, (loaded, total) => {
          const pct = total ? Math.round((loaded / total) * 100) : 0;
          setUploadStatus(`${file.name} · ${pct}% · ${fmtBytes(loaded)} / ${fmtBytes(total)}`);
        });
        await maybeProcess(data);
      } catch (err) {
        errors.push(`${file.name}: ${err.message || err}`);
      }
    }
    setUploadStatus("");
    await load();
    if (errors.length) {
      openModal({
        title: "Some files failed",
        text: errors.join(" · "),
        fields: [],
        submitLabel: "OK",
        onSubmit: async () => {},
      });
    }
  }

  function groupById(id) {
    return (state.groups || []).find((g) => g.id === id) || (state.group && state.group.id === id ? state.group : null);
  }

  function openModal(cfg) {
    state.modal = { error: "", fields: [], ...cfg };
    render();
  }

  function modalHtml() {
    const m = state.modal;
    if (!m) return "";
    const fields = (m.fields || [])
      .map((f) => {
        if (f.type === "select") {
          const opts = (f.options || [])
            .map(
              (o) =>
                `<option value="${esc(o.value)}" ${String(o.value) === String(f.value) ? "selected" : ""}>${esc(o.label ?? o.value)}</option>`
            )
            .join("");
          return `<label>${esc(f.label)}</label><select name="${esc(f.name)}">${opts}</select>`;
        }
        const step = f.step ? ` step="${esc(f.step)}"` : "";
        return `<label>${esc(f.label)}</label><input type="${esc(f.type || "text")}" name="${esc(f.name)}" value="${esc(f.value || "")}"${step} />`;
      })
      .join("");
    return `
      <div class="modal-back" id="modal-back">
        <form class="modal" id="app-modal">
          <h3>${esc(m.title)}</h3>
          ${m.text ? `<p class="sub">${esc(m.text)}</p>` : ""}
          ${fields}
          ${m.error ? `<p class="err">${esc(m.error)}</p>` : ""}
          ${m.note ? `<p class="note" id="modal-note">${esc(m.note)}</p>` : `<p class="note" id="modal-note" hidden></p>`}
          <div class="row">
            ${(m.extras || []).length ? `<div class="modal-extras">${(m.extras || []).map((b) => `<button type="button" data-modal-extra="${esc(b.act)}">${esc(b.label)}</button>`).join("")}</div>` : ""}
            <button type="button" data-modal-cancel>Cancel</button>
            <button type="submit" class="primary">${esc(m.submitLabel || "OK")}</button>
          </div>
        </form>
      </div>`;
  }

  function bindModal() {
    const back = $("#modal-back");
    const form = $("#app-modal");
    if (!back || !form) return;
    back.addEventListener("click", (e) => {
      if (e.target !== back) return;
      state.modal = null;
      render();
    });
    const cancel = form.querySelector("[data-modal-cancel]");
    if (cancel)
      cancel.addEventListener("click", () => {
        state.modal = null;
        render();
      });
    form.addEventListener("input", () => {
      if (!state.modal) return;
      (state.modal.fields || []).forEach((f) => {
        const el = form.elements[f.name];
        if (el) f.value = el.value;
      });
    });
    const first = form.querySelector("input, select, textarea");
    if (first && typeof first.focus === "function") first.focus();
    form.querySelectorAll("[data-modal-extra]").forEach((b) => {
      b.addEventListener("click", async () => {
        const values = modalValues(form);
        rememberModalValues(values);
        try {
          if (b.dataset.modalExtra === "copy-shift") await copyFormShift(values);
          else if (b.dataset.modalExtra === "paste-shift") await pasteShiftNow();
        } catch (err) {
          if (!state.modal) return;
          state.modal.error = err.message || String(err);
          state.modal.note = "";
          render();
        }
      });
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const values = {};
      (state.modal.fields || []).forEach((f) => {
        const el = form.elements[f.name];
        values[f.name] = el ? el.value : "";
      });
      try {
        await state.modal.onSubmit(values);
        state.modal = null;
        await load();
      } catch (err) {
        (state.modal.fields || []).forEach((f) => {
          if (values[f.name] !== undefined) f.value = values[f.name];
        });
        state.modal.error = err.message || String(err);
        render();
      }
    });
  }

  function openNewProject() {
    openModal({
      title: "New project",
      submitLabel: "Create",
      fields: [{ name: "name", label: "Name", type: "text", value: "" }],
      error: "",
      onSubmit: async (v) => {
        const p = await api("/api/projects", { method: "POST", json: { name: v.name } });
        location.hash = `#/g/${p.id}`;
      },
    });
  }

  function openRename(id) {
    const g = groupById(id);
    openModal({
      title: "Rename project",
      submitLabel: "Save",
      fields: [{ name: "name", label: "Name", type: "text", value: (g && g.name) || "" }],
      error: "",
      onSubmit: async (v) => {
        await api(`/api/projects/${encodeURIComponent(id)}`, { method: "PATCH", json: { name: v.name } });
      },
    });
  }

  function openArchive(id) {
    const g = groupById(id);
    openModal({
      title: "Archive project",
      submitLabel: "Archive",
      text: g ? `Archive “${g.name}”?` : "Archive this project?",
      fields: [],
      error: "",
      onSubmit: async () => {
        await api(`/api/projects/${encodeURIComponent(id)}/archive`, { method: "POST" });
      },
    });
  }

  function openDelete(id) {
    openModal({
      title: "Delete project",
      submitLabel: "Delete",
      text: "Recordings move to Inbox. The project folder is removed.",
      fields: [],
      error: "",
      onSubmit: async () => {
        await api(`/api/projects/${encodeURIComponent(id)}`, { method: "DELETE" });
      },
    });
  }

  function recCardHtml(p, others, archived, txRec) {
    const canProcess = !archived && ["pending", "error", "vad", "completed", "processing", "queued"].includes(p.status);
    const processLabel = p.status === "completed" ? "Reprocess" : p.resumable || p.status === "error" ? "Resume" : "Process";
    const jobErr = p.job && p.job.error;
    const moveOpts = (others || [])
      .map((o) => `<option value="${esc(o.id)}">${esc(o.name)}</option>`)
      .join("");
    const hitCount = txRec && txRec.hits ? txRec.hits : 0;
    const busy = ["queued", "vad", "processing"].includes(p.status);
    const prog = jobProgressOf(p);
    const title = recTitle(p);
    const comment = String(p.comment || "");
    const noteOpen = !!state.openNotes[p.id];
    const editing = state.editingTitle === p.id;
    const tip = !noteOpen && !editing ? commentTipHtml(comment) : "";
    return `
      <article class="card ${p.status}" tabindex="0" data-open="${esc(p.id)}" data-comment="${esc(comment)}" role="button">
        <div class="card-top">
          <div class="rec-name" data-stop="1">
            ${editing
              ? `<input class="rec-title-input mono" data-title-input="${esc(p.id)}" value="${esc(p.title || p.id)}" maxlength="200" aria-label="Название записи" />`
              : `<h3 class="mono rec-title">${esc(title)}</h3>
                 <button type="button" class="icon-btn pencil" data-edit-title="${esc(p.id)}" title="Переименовать" aria-label="Переименовать">${pencilSvg()}</button>`}
            ${tip}
          </div>
          <span class="badge ${p.status}">${esc((busy && prog && prog.op) || p.status)}</span>
        </div>
        <div class="rec-note" data-stop="1">
          <button type="button" class="rec-note-toggle" data-note-toggle="${esc(p.id)}" aria-expanded="${noteOpen ? "true" : "false"}">
            <span aria-hidden="true">${noteOpen ? "▾" : "▸"}</span>
            <span class="rec-note-preview">${comment ? esc(oneLine(comment)) : "Комментарий"}</span>
          </button>
          ${noteOpen ? `<label class="rec-note-edit">
            <textarea data-note="${esc(p.id)}" maxlength="${COMMENT_MAX}" rows="4" placeholder="Комментарий к записи, до ${COMMENT_MAX} символов">${esc(comment)}</textarea>
            <span class="faint"><span data-note-count>${comment.length}</span> / ${COMMENT_MAX}</span>
          </label>` : tip}
        </div>
        <div class="meta">
          <span>${fmtDur(p.duration_sec)} · ${p.segments || 0} lines · ${p.clips || 0} clips</span>
          <span>Start: <span class="mono">${esc(p.recording_start_label || "—")}</span>${p.clock_source === "anchor" ? ` <span class="faint">калибр.${p.clock_shift_label ? " " + esc(p.clock_shift_label) : ""}</span>` : ""}</span>
          <span>Speech: ${fmtDur(p.speech_sec)} (${(100 * (p.speech_ratio || 0)).toFixed(1)}%)</span>
          ${jobErr ? `<span class="err">${esc(jobErr)}</span>` : ""}
          <span class="hit-count" ${hitCount ? "" : "hidden"}>${hitCount} matching line${hitCount === 1 ? "" : "s"}</span>
        </div>
        <div class="tx-hits" data-stop="1">${txHitsHtml(p.id, txRec)}</div>
        ${jobProgressHtml(p)}
        ${jobSnipsHtml(p)}
        <div class="updated">${fmtBytes(p.bytes)}</div>
        ${archived ? "" : `<div class="card-actions" data-stop="1">
          ${canProcess ? `<button type="button" data-process="${esc(p.id)}">${processLabel}</button>` : ""}
          ${p.has_cleaned && state.group ? `<a class="icon-btn" style="display:inline-flex;align-items:center;padding:6px 10px" href="/api/projects/${encodeURIComponent(state.group.id)}/recordings/${encodeURIComponent(p.id)}/export.mp3" download="${esc(p.id)}.mp3">MP3</a>` : ""}
          ${state.group && state.group.id !== "inbox" ? `<button type="button" data-to-inbox="${esc(p.id)}">To Inbox</button>` : ""}
          ${moveOpts ? `<select data-move="${esc(p.id)}"><option value="">Move to…</option>${moveOpts}</select>` : ""}
          <button type="button" data-remove="${esc(p.id)}">Remove</button>
        </div>`}
      </article>`;
  }

  function jobProgressOf(p) {
    const pr = p && p.job && p.job.progress;
    if (pr && pr.op) return pr;
    return inferProgressFromLog(p);
  }

  function inferProgressFromLog(p) {
    const lines = (p && p.job && p.job.log) || [];
    const dur = (p && p.duration_sec) || 0;
    for (let i = lines.length - 1; i >= 0; i -= 1) {
      const line = String(lines[i] || "");
      let m = line.match(/clean\+asr\s+(\d+)\/(\d+)/);
      if (m) {
        const done = +m[1];
        const total = +m[2] || 1;
        const frac = done / total;
        return {
          op: "asr",
          label: `Transcription ${done}/${total}`,
          done,
          total,
          pct: Math.round(100 * frac),
          overall_pct: 48 + 47 * frac,
        };
      }
      m = line.match(/^\s*(\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)\s+spans\+=/);
      if (m) {
        const sec = +m[1] * 3600 + +m[2] * 60 + parseFloat(m[3]);
        const total = Math.max(1, Math.round(dur) || Math.round(sec) || 1);
        const clock = `${m[1]}:${m[2]}:${m[3]}`;
        const frac = Math.min(1, sec / total);
        return {
          op: "vad",
          label: `Voice activity ${clock} / ${fmtDur(total)}`,
          done: Math.round(sec),
          total,
          pct: Math.round(100 * frac),
          overall_pct: 28 + 20 * frac,
          detail: clock,
        };
      }
      if (line.indexOf("adaptive preprocess") === 0)
        return { op: "enhance", label: "Noise reduction / AGC", overall_pct: 18 };
      if (line.indexOf("decode ") === 0) return { op: "decode", label: "Decode to PCM", overall_pct: 4 };
    }
    if (p && p.status === "queued") return { op: "queued", label: "Queued", overall_pct: 0 };
    return null;
  }

  function jobProgressHtml(p) {
    const busy = ["queued", "vad", "processing"].includes(p.status);
    const pr = jobProgressOf(p);
    if (!busy) {
      const ratio = Math.max(2, (p.speech_ratio || 0) * 100);
      return `<div class="progress" title="доля речи"><i style="width:${Math.min(100, ratio).toFixed(1)}%"></i></div>`;
    }
    return `<div class="job-progress" aria-live="polite">${jobLiveInner(pr)}</div>`;
  }

  function asrSnippetsFromJob(p, limit = 4) {
    const log = (p && p.job && p.job.log) || [];
    const out = [];
    for (let i = log.length - 1; i >= 0 && out.length < limit; i -= 1) {
      const m = String(log[i] || "").match(/^\[([^\]]+)\]\s+(.+)/);
      if (m && m[2].trim()) out.push({ clock: m[1], text: m[2].trim() });
    }
    return out.reverse();
  }

  function jobSnipsHtml(p) {
    const busy = ["queued", "vad", "processing"].includes(p.status);
    if (!busy) return "";
    const snips = asrSnippetsFromJob(p);
    if (!snips.length) return "";
    return `<div class="job-snips">${snips
      .map((s) => `<p><span class="mono faint">${esc(s.clock)}</span> ${esc(s.text)}</p>`)
      .join("")}</div>`;
  }

  function asrLiveHtml(p) {
    const snips = asrSnippetsFromJob(p, 6);
    if (!snips.length) return `<p class="faint">Ждём строки ASR…</p>`;
    return snips
      .map((s) => `<p><span class="mono faint">${esc(s.clock)}</span> ${esc(s.text)}</p>`)
      .join("");
  }

  async function syncWorkspaceLive(job) {
    if (!state.project || !state.group) return;
    const prevN = ((state.project.segments_detail || []).length);
    try {
      const rec = await api(
        `/api/projects/${encodeURIComponent(state.group.id)}/recordings/${encodeURIComponent(state.project.id)}`
      );
      state.project = { ...state.project, ...rec, job: { ...(rec.job || {}), ...job } };
    } catch {
      if (job) state.project.job = { ...(state.project.job || {}), ...job };
    }
    const live = app.querySelector(".job-live");
    if (live) live.innerHTML = jobLiveInner(jobProgressOf(state.project));
    const asrLive = app.querySelector("#asr-live");
    if (asrLive) asrLive.innerHTML = asrLiveHtml(state.project);
    const segs = state.project.segments_detail || [];
    const tbody = app.querySelector(".tx-table tbody");
    if (tbody && segs.length !== prevN) {
      tbody.innerHTML = segs.map((s) => rowHtml(s)).join("") || `<tr><td colspan="7" class="empty">Нет строк транскрипта</td></tr>`;
      bindTxTable();
    }
    const sc = app.querySelector("#seg-count");
    const tq = $("#tx-q");
    if (sc && !(tq && tq.value)) sc.textContent = String(segs.length);
  }

  function jobLiveInner(pr) {
    const pct = pr && pr.overall_pct != null ? Number(pr.overall_pct) : pr && pr.pct != null ? Number(pr.pct) : 0;
    const label = (pr && pr.label) || "Processing…";
    const opPct =
      pr && pr.pct != null && pr.total
        ? ` <span class="faint">${Number(pr.pct).toFixed(0)}%</span>`
        : "";
    return `<div class="job-op">${esc(label)}${opPct}</div>
      <div class="progress job" title="${esc(label)}"><i style="width:${Math.max(0, Math.min(100, pct)).toFixed(1)}%"></i></div>`;
  }

  function txHitsById() {
    const map = {};
    const hits = state.txHits;
    if (!hits || !hits.recordings) return map;
    hits.recordings.forEach((r) => {
      map[r.id] = r;
    });
    return map;
  }

  function hitActSvg(kind) {
    if (kind === "play")
      return `<svg viewBox="0 0 16 16" aria-hidden="true"><path fill="currentColor" d="M5 3.1v9.8L13.2 8z"/></svg>`;
    if (kind === "pause")
      return `<svg viewBox="0 0 16 16" aria-hidden="true"><path fill="currentColor" d="M4 3.2h2.6v9.6H4zm5.4 0H12v9.6H9.4z"/></svg>`;
    if (kind === "go")
      return `<svg viewBox="0 0 16 16" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" d="M6.5 3.5h-3v9h9v-3M8 8l5-5M9.2 3h3.8v3.8"/></svg>`;
    if (kind === "line")
      return `<svg viewBox="0 0 16 16" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" d="M8 2.5v8M5 8l3 3 3-3M3 13.5h10"/></svg>`;
    return `<svg viewBox="0 0 16 16" aria-hidden="true"><path fill="currentColor" d="M2.2 6.2h1.3v3.6H2.2zm2.4-2h1.3v7.6H4.6zm2.4-2.2h1.3v12H7zm2.4 3.4h1.3v5.2H9.4zm2.4-1.6h1.3v8.4h-1.3z"/></svg>`;
  }

  let hitAudio = null;
  let hitAudioStem = "";
  let hitAudioN = 0;

  function isHitPreviewPlaying(stem, n) {
    return !!(hitAudio && !hitAudio.paused && !hitAudio.ended && hitAudioStem === stem && hitAudioN === n);
  }

  function paintHitPlayButtons() {
    app.querySelectorAll("[data-hit-play]").forEach((b) => {
      const on = isHitPreviewPlaying(b.dataset.stem, +b.dataset.n);
      b.classList.toggle("on", on);
      b.classList.toggle("busy", false);
      b.setAttribute("aria-pressed", on ? "true" : "false");
      const label = on ? "Пауза" : "Слушать реплику";
      b.title = label;
      b.setAttribute("aria-label", label);
      b.innerHTML = hitActSvg(on ? "pause" : "play");
    });
  }

  function dropHitAudio(el) {
    if (!el) return;
    el.onplay = null;
    el.onpause = null;
    el.onended = null;
    el.onerror = null;
    el.pause();
    el.removeAttribute("src");
  }

  function stopHitPreview() {
    const el = hitAudio;
    hitAudio = null;
    hitAudioStem = "";
    hitAudioN = 0;
    dropHitAudio(el);
    paintHitPlayButtons();
  }

  function toggleHitPreview(stem, n) {
    if (!state.group || !n) return;
    if (isHitPreviewPlaying(stem, n)) {
      hitAudio.pause();
      paintHitPlayButtons();
      return;
    }
    const url = `/api/projects/${encodeURIComponent(state.group.id)}/recordings/${encodeURIComponent(stem)}/lines/${encodeURIComponent(n)}/export.mp3`;
    if (state.playing) stopPlay(true);
    if (hitAudio && hitAudioStem === stem && hitAudioN === n) {
      hitAudio.play().catch((err) => {
        openModal({
          title: "Слушать",
          text: err.message || String(err),
          fields: [],
          submitLabel: "OK",
          onSubmit: async () => {},
        });
      });
      return;
    }
    dropHitAudio(hitAudio);
    hitAudio = new Audio(url);
    hitAudio.preload = "auto";
    hitAudioStem = stem;
    hitAudioN = n;
    hitAudio.onplay = paintHitPlayButtons;
    hitAudio.onpause = paintHitPlayButtons;
    hitAudio.onended = paintHitPlayButtons;
    hitAudio.onerror = () => {
      openModal({
        title: "Слушать",
        text: "Не удалось воспроизвести реплику",
        fields: [],
        submitLabel: "OK",
        onSubmit: async () => {},
      });
      stopHitPreview();
    };
    const sel = `[data-hit-play][data-n="${n}"]`;
    app.querySelectorAll(sel).forEach((b) => {
      if (b.dataset.stem === stem) b.classList.add("busy");
    });
    hitAudio.play().catch((err) => {
      openModal({
        title: "Слушать",
        text: err.message || String(err),
        fields: [],
        submitLabel: "OK",
        onSubmit: async () => {},
      });
      stopHitPreview();
    });
  }

  function txHitsHtml(stem, txRec) {
    const lines = (txRec && txRec.lines) || [];
    if (!lines.length) return "";
    const q = (state.txHits && state.txHits.query) || state.q || "";
    const qTrim = q.trim();
    const gid = state.group ? state.group.id : "";
    const wordTitle = qTrim ? `Скачать фрагмент «${qTrim}»` : "Скачать фрагмент";
    return lines
      .map((ln) => {
        const qs = new URLSearchParams();
        if (ln.n) qs.set("n", String(ln.n));
        if (qTrim) qs.set("q", qTrim);
        const href = `#/g/${encodeURIComponent(gid)}/r/${encodeURIComponent(stem)}?${qs.toString()}`;
        const playing = isHitPreviewPlaying(stem, ln.n);
        const playLab = playing ? "Пауза" : "Слушать реплику";
        return `<div class="tx-hit">
          <div class="tx-hit-row">
            <a class="tx-hit-clock" href="${esc(href)}" data-open-hit="${esc(stem)}" data-n="${ln.n}">
              <span class="mono faint">${esc(ln.clock || `#${ln.n}`)}</span>
            </a>
            <span class="tx-hit-acts">
              <button type="button" class="tx-hit-act${playing ? " on" : ""}" data-hit-play data-stem="${esc(stem)}" data-n="${ln.n}" title="${playLab}" aria-label="${playLab}" aria-pressed="${playing ? "true" : "false"}">${hitActSvg(playing ? "pause" : "play")}</button>
              <a class="tx-hit-act" href="${esc(href)}" data-open-hit="${esc(stem)}" title="Перейти к реплике" aria-label="Перейти к реплике">${hitActSvg("go")}</a>
              <button type="button" class="tx-hit-act" data-hit-dl="line" data-stem="${esc(stem)}" data-n="${ln.n}" title="Скачать реплику" aria-label="Скачать реплику">${hitActSvg("line")}</button>
              <button type="button" class="tx-hit-act" data-hit-dl="match" data-stem="${esc(stem)}" data-n="${ln.n}" title="${esc(wordTitle)}" aria-label="${esc(wordTitle)}">${hitActSvg("word")}</button>
            </span>
          </div>
          <a class="tx-hit-text" href="${esc(href)}" data-open-hit="${esc(stem)}" data-n="${ln.n}"><p>${highlightMatch(ln.text, q)}</p></a>
        </div>`;
      })
      .join("");
  }

  async function downloadHitAudio(stem, n, mode, btn) {
    if (!state.group || !n) return;
    const q = ((state.txHits && state.txHits.query) || state.q || "").trim();
    const base = `/api/projects/${encodeURIComponent(state.group.id)}/recordings/${encodeURIComponent(stem)}/lines/${encodeURIComponent(n)}`;
    const url = mode === "match" ? `${base}/match.mp3?q=${encodeURIComponent(q)}` : `${base}/export.mp3`;
    if (btn) btn.disabled = true;
    try {
      const r = await fetch(url);
      if (!r.ok) {
        let detail = `${r.status}`;
        try {
          const body = await r.json();
          if (body && body.detail) detail = body.detail;
        } catch {
          /* keep status */
        }
        throw new Error(detail);
      }
      const blob = await r.blob();
      const cd = r.headers.get("content-disposition") || "";
      let name = mode === "match" ? `${stem}_n${n}_${q || "match"}.mp3` : `${stem}_n${n}.mp3`;
      const star = cd.match(/filename\*=UTF-8''([^;]+)/i);
      const plain = cd.match(/filename="?([^";]+)"?/i);
      if (star) name = decodeURIComponent(star[1]);
      else if (plain) name = plain[1];
      const a = document.createElement("a");
      const href = URL.createObjectURL(blob);
      a.href = href;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(href), 2000);
    } catch (err) {
      openModal({
        title: "Скачать",
        text: err.message || String(err),
        fields: [],
        submitLabel: "OK",
        onSubmit: async () => {},
      });
    } finally {
      if (btn) btn.disabled = false;
    }
  }



  function highlightMatch(text, q) {
    const src = String(text ?? "");
    const needle = String(q || "").trim();
    if (!needle) return esc(src);
    const hay = src.toLocaleLowerCase();
    const n = needle.toLocaleLowerCase();
    let out = "";
    let i = 0;
    while (i < src.length) {
      const at = hay.indexOf(n, i);
      if (at < 0) {
        out += esc(src.slice(i));
        break;
      }
      out += esc(src.slice(i, at)) + `<mark>${esc(src.slice(at, at + needle.length))}</mark>`;
      i = at + needle.length;
    }
    return out;
  }

  function applyGroupFilter() {
    if (state.view !== "group" || !state.group) return;
    const needle = (state.q || "").trim().toLowerCase();
    const hitsById = txHitsById();
    const qTrim = (state.q || "").trim();
    const txReady = !!(state.txHits && state.txHits.query === qTrim);
    let vis = 0;
    let recHits = 0;
    app.querySelectorAll(".card[data-open]").forEach((c) => {
      const id = (c.dataset.open || "").toLowerCase();
      const name = (c.querySelector("h3")?.textContent || "").toLowerCase();
      const comment = (c.dataset.comment || "").toLowerCase();
      const txRec = hitsById[c.dataset.open];
      const nameHit = !needle || id.includes(needle) || name.includes(needle) || comment.includes(needle);
      const txHit = !!(txRec && txRec.hits);
      const show = !needle || nameHit || (txReady && txHit);
      c.style.display = show ? "" : "none";
      if (show) vis += 1;
      if (txHit) recHits += 1;
      const box = c.querySelector(".tx-hits");
      if (box) box.innerHTML = txHitsHtml(c.dataset.open, txReady ? txRec : null);
      const count = c.querySelector(".hit-count");
      if (count) {
        const n = txReady && txRec ? txRec.hits : 0;
        count.hidden = !n;
        count.textContent = n ? `${n} matching line${n === 1 ? "" : "s"}` : "";
      }
    });
    const status = $("#search-status");
    if (status) {
      if (!needle) status.textContent = "";
      else if (!txReady) status.textContent = "Searching transcripts…";
      else if (state.txHits.total)
        status.textContent = `${state.txHits.total} line${state.txHits.total === 1 ? "" : "s"} in ${recHits} recording${recHits === 1 ? "" : "s"}`;
      else status.textContent = vis ? "Name match only" : "";
    }
    const empty = $("#search-empty");
    if (empty) {
      const showEmpty = !!needle && vis === 0 && (state.group.items || []).length > 0;
      empty.hidden = !showEmpty;
      if (showEmpty)
        empty.textContent = `Ничего не найдено по «${qTrim}». Попробуйте другое слово из расшифровки.`;
    }
    paintHitPlayButtons();
  }

  let txTimer = 0;
  function scheduleTranscriptSearch() {
    if (state.view !== "group" || !state.group) return;
    clearTimeout(txTimer);
    const q = (state.q || "").trim();
    if (!q) {
      state.txHits = null;
      stopHitPreview();
      applyGroupFilter();
      return;
    }
    txTimer = setTimeout(() => fetchTranscriptHits(q), 220);
  }

  async function fetchTranscriptHits(q) {
    if (!state.group || state.view !== "group") return;
    const gen = ++state.txGen;
    try {
      const data = await api(
        `/api/projects/${encodeURIComponent(state.group.id)}/search?q=${encodeURIComponent(q)}`
      );
      if (gen !== state.txGen || state.view !== "group") return;
      if ((state.q || "").trim() !== q) return;
      state.txHits = data;
      applyGroupFilter();
    } catch {
      /* keep last */
    }
  }

  function cssEscape(value) {
    if (window.CSS && CSS.escape) return CSS.escape(value);
    return String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
  }

  function focusTitleEditor() {
    if (!state.editingTitle || state.view !== "group") return;
    const input = app.querySelector("[data-title-input]");
    if (!input) return;
    input.focus();
    const n = input.value.length;
    try {
      input.setSelectionRange(n, n);
    } catch {
      /* ignore */
    }
  }

  async function patchRecording(stem, json) {
    if (!state.group) throw new Error("Нет проекта");
    return api(
      `/api/projects/${encodeURIComponent(state.group.id)}/recordings/${encodeURIComponent(stem)}`,
      { method: "PATCH", json }
    );
  }

  function applySummary(stem, rec) {
    const items = (state.group && state.group.items) || [];
    const item = items.find((p) => p.id === stem);
    if (item && rec) {
      item.title = rec.title || "";
      item.comment = rec.comment || "";
      item.name = rec.name || item.name;
      item.file_name = rec.file_name || item.file_name;
    }
    if (state.project && state.project.id === stem && rec) {
      state.project.title = rec.title || "";
      state.project.comment = rec.comment || "";
      state.project.name = rec.name || state.project.name;
      state.project.file_name = rec.file_name || state.project.file_name;
    }
    if (state.session && state.session.stem === stem && rec) {
      state.session.title = recTitle(rec);
      state.session.fileName = fileNameOf(rec);
      if (state.session.project) {
        state.session.project.title = rec.title || "";
        state.session.project.comment = rec.comment || "";
        state.session.project.file_name = rec.file_name || state.session.project.file_name;
        state.session.project.name = rec.name || state.session.project.name;
      }
      syncMini();
    }
  }

  async function commitTitle(input) {
    const stem = input.dataset.titleInput;
    if (!stem || state.editingTitle !== stem) return;
    state.editingTitle = null;
    const text = input.value.trim();
    const item = ((state.group && state.group.items) || []).find((p) => p.id === stem);
    const prev = String((item && item.title) || "").trim();
    if (text === prev || (!prev && text === stem)) {
      if (state.view === "group") renderGroup();
      return;
    }
    try {
      const rec = await patchRecording(stem, { title: text });
      applySummary(stem, rec);
    } catch (err) {
      openModal({ title: "Название", text: err.message, fields: [], submitLabel: "OK", onSubmit: async () => {} });
    }
    if (state.view === "group") renderGroup();
  }

  async function commitComment(ta) {
    const stem = ta.dataset.note;
    if (!stem || !state.group) return;
    const text = ta.value;
    const item = (state.group.items || []).find((p) => p.id === stem);
    const prev = String((item && item.comment) || "");
    if (text.trim() === prev.trim()) return;
    try {
      const rec = await patchRecording(stem, { comment: text });
      applySummary(stem, rec);
      if (state.view === "group" && !state.editingTitle) renderGroup();
    } catch (err) {
      openModal({ title: "Комментарий", text: err.message, fields: [], submitLabel: "OK", onSubmit: async () => {} });
    }
  }

  function bindRecordingNotes() {
    app.querySelectorAll("[data-edit-title]").forEach((b) => {
      b.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        state.editingTitle = b.dataset.editTitle;
        if (state.view === "group") renderGroup();
      });
    });
    app.querySelectorAll("[data-title-input]").forEach((input) => {
      input.addEventListener("click", (e) => e.stopPropagation());
      input.addEventListener("keydown", (e) => {
        e.stopPropagation();
        if (e.key === "Enter") {
          e.preventDefault();
          input.blur();
        } else if (e.key === "Escape") {
          e.preventDefault();
          state.editingTitle = null;
          if (state.view === "group") renderGroup();
        }
      });
      input.addEventListener("blur", () => commitTitle(input));
    });
    app.querySelectorAll("[data-note-toggle]").forEach((b) => {
      b.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const id = b.dataset.noteToggle;
        state.openNotes[id] = !state.openNotes[id];
        if (state.view === "group") renderGroup();
        if (state.openNotes[id]) {
          const ta = app.querySelector(`[data-note="${cssEscape(id)}"]`);
          if (ta) {
            ta.focus();
            const n = ta.value.length;
            try {
              ta.setSelectionRange(n, n);
            } catch {
              /* ignore */
            }
          }
        }
      });
    });
    app.querySelectorAll("[data-note]").forEach((ta) => {
      ta.addEventListener("click", (e) => e.stopPropagation());
      ta.addEventListener("keydown", (e) => e.stopPropagation());
      ta.addEventListener("input", () => {
        if (ta.value.length > COMMENT_MAX) ta.value = ta.value.slice(0, COMMENT_MAX);
        const n = ta.parentElement && ta.parentElement.querySelector("[data-note-count]");
        if (n) n.textContent = String(ta.value.length);
      });
      ta.addEventListener("blur", () => commitComment(ta));
    });
  }

  function syncMini() {
    const bar = $("#mini-player");
    if (!bar) return;
    const s = state.session;
    const show = !!(s && s.live);
    bar.hidden = !show;
    document.body.classList.toggle("mini-on", show);
    if (!show) return;
    const fname = s.fileName || s.stem || "";
    const title = s.title || s.stem || "";
    const name = $("#mini-name");
    const file = $("#mini-file");
    if (name) name.textContent = fname;
    if (file) {
      const extra = title && title !== fname ? title : "";
      file.textContent = extra;
      file.hidden = !extra;
    }
    const btn = $("#mini-play");
    if (btn) {
      btn.textContent = state.playing ? "❚❚" : "▶";
      btn.setAttribute("aria-label", state.playing ? "Пауза" : "Играть");
    }
    const rate = $("#mini-rate");
    if (rate && document.activeElement !== rate) rate.value = String(baseRate());
    const vol = $("#mini-vol");
    if (vol && document.activeElement !== vol) vol.value = String(Math.round((state.volMaster == null ? 1 : state.volMaster) * 100));
    const open = $("#mini-open");
    if (open) open.setAttribute("href", `#/g/${encodeURIComponent(s.groupId)}/r/${encodeURIComponent(s.stem)}`);
  }

  function bindMini() {
    const play = $("#mini-play");
    const rate = $("#mini-rate");
    const vol = $("#mini-vol");
    const open = $("#mini-open");
    if (play)
      play.addEventListener("click", () => {
        if (!state.session || !state.session.live) return;
        if (state.playing) stopPlay(true);
        else if (engineOwnsView()) playFrom(state.playhead || state.session.playhead || 0);
        else runPlay(state.session.playhead || 0);
      });
    if (rate)
      rate.addEventListener("change", () => {
        state.playRate = parseFloat(rate.value) || 1;
        applyRate();
      });
    if (vol)
      vol.addEventListener("input", () => {
        state.volMaster = +vol.value / 100;
        applyVolumes();
      });
    if (open)
      open.addEventListener("click", (e) => {
        if (!state.session) return;
        e.preventDefault();
        location.hash = `#/g/${encodeURIComponent(state.session.groupId)}/r/${encodeURIComponent(state.session.stem)}`;
      });
  }

  function bindDash() {
    app.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => (location.hash = b.dataset.go)));
    app.querySelectorAll("[data-archived]").forEach((b) =>
      b.addEventListener("click", () => {
        const next = b.dataset.archived === "1";
        if (next === state.showArchived) return;
        state.showArchived = next;
        load();
      })
    );
    const np = app.querySelector("[data-act=new-project]");
    if (np) np.addEventListener("click", () => openNewProject());
    const ns = app.querySelector("[data-act=new-speaker]");
    if (ns) ns.addEventListener("click", () => openNewSpeaker());
    app.querySelectorAll("[data-rename]").forEach((b) =>
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        openRename(b.dataset.rename);
      })
    );
    app.querySelectorAll("[data-archive]").forEach((b) =>
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        openArchive(b.dataset.archive);
      })
    );
    app.querySelectorAll("[data-delete]").forEach((b) =>
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        openDelete(b.dataset.delete);
      })
    );
    app.querySelectorAll("[data-open-group]").forEach((el) => {
      const go = () => {
        location.hash = `#/g/${el.dataset.openGroup}`;
      };
      el.addEventListener("click", (e) => {
        if (e.target.closest("[data-stop]")) return;
        go();
      });
      el.addEventListener("keydown", (e) => {
        if (e.target.closest("[data-stop]")) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          go();
        }
      });
    });
    app.querySelectorAll("[data-open]").forEach((el) => {
      const go = () => {
        if (!state.group) return;
        location.hash = `#/g/${state.group.id}/r/${el.dataset.open}`;
      };
      el.addEventListener("click", (e) => {
        if (e.target.closest("[data-stop],[data-open-hit],a,button,select,input,textarea,label")) return;
        go();
      });
      el.addEventListener("keydown", (e) => {
        if (e.target.closest("[data-stop],[data-open-hit],a,button,select,input,textarea,label")) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          go();
        }
      });
    });
    bindRecordingNotes();
    const q = $("#dash-q");
    if (q)
      q.addEventListener("input", () => {
        state.q = q.value;
        if (state.view === "group") {
          applyGroupFilter();
          scheduleTranscriptSearch();
          return;
        }
        const needle = state.q.toLowerCase();
        app.querySelectorAll(".card").forEach((c) => {
          const id = (c.dataset.openGroup || c.dataset.open || "").toLowerCase();
          const name = (c.querySelector("h3")?.textContent || "").toLowerCase();
          c.style.display = !needle || id.includes(needle) || name.includes(needle) ? "" : "none";
        });
      });
    if (state.view === "group" && (state.q || "").trim()) scheduleTranscriptSearch();
  }

  function renderWorkspace() {
    const p = state.project;
    const segs = p.segments_detail || [];
    const spans = p.spans_detail || [];
    const vad = (state.settings && state.settings.vad) || {};
    const enh = (state.settings && state.settings.enhance) || {};
    const shown = segs;
    const ins = state.inspect;
    app.innerHTML = `
      ${topbar(`<span class="pill">${p.clock_source === "anchor" ? "Калибровка часов" : "Absolute wall-clock"}</span>`)}
      <div class="ws ${state.showSet ? "" : "hide-set"} ${state.showIns ? "" : "hide-ins"} ${state.wsDir === "lr" ? "split-lr" : "split-tb"}">
        <div class="ws-head">
          <button class="icon-btn" data-go="${state.group ? `#/g/${state.group.id}` : "#/"}">←</button>
          <h2>${state.group ? `${esc(state.group.name)} <span class="faint">/</span> ` : ""}${recTitle(p) === p.id ? `<span class="mono">${esc(p.id)}</span>` : `${esc(recTitle(p))} <span class="mono faint">${esc(p.id)}</span>`}</h2>
          <span class="badge ${p.status}">${esc(p.status)}</span>
          ${["queued", "vad", "processing"].includes(p.status) ? `<span class="job-live">${jobLiveInner(jobProgressOf(p))}</span>` : ""}
          ${clockHeadHtml(p)}
          <span class="muted">${fmtDur(p.duration_sec)} · speech ${fmtDur(p.speech_sec)} · ${segs.length} lines</span>
          ${speakerStatsHtml(p, segs)}
          <span style="margin-left:auto;display:flex;gap:8px;flex-wrap:wrap">
            <button class="icon-btn ${state.wsDir === "tb" ? "on" : ""}" data-act="split-tb" title="Timeline сверху, текст снизу" aria-pressed="${state.wsDir === "tb"}">↑↓</button>
            <button class="icon-btn ${state.wsDir === "lr" ? "on" : ""}" data-act="split-lr" title="Timeline слева, текст справа" aria-pressed="${state.wsDir === "lr"}">↔</button>
            <button class="icon-btn" data-act="match-spk">Дикторы</button>
            <button class="icon-btn" data-act="fit">Fit</button>
            ${["pending", "error"].includes(p.status) ? `<button class="icon-btn" data-act="process-now">Process</button>` : ""}
            <a class="icon-btn" href="${recBase()}/export.srt">Export SRT</a>
            <a class="icon-btn" href="${recBase()}/export.mp3">MP3</a>
            <a class="icon-btn" href="${recBase()}/transcript.txt">TXT</a>
            <button class="icon-btn" data-act="parallel" ${state.selected.size < 2 ? "disabled" : ""}>Parallel (${state.selected.size})</button>
            <button class="icon-btn" data-act="toggle-fx">Слух / FX</button>
            <button class="icon-btn ${state.showSet ? "on" : ""}" data-act="toggle-set" aria-pressed="${state.showSet}">VAD</button>
            <button class="icon-btn ${state.showIns ? "on" : ""}" data-act="toggle-ins" aria-pressed="${state.showIns}">Inspect</button>
          </span>
        </div>
        <aside class="settings">
          <div class="panel-title">Voice activity (VAD) <span class="faint">adaptive</span>
            <button type="button" class="icon-btn" data-act="toggle-set" aria-label="Свернуть VAD">‹</button>
          </div>
          ${rowToggle("vad.enabled", "Enabled", vad.enabled !== false)}
          ${rowToggle("vad.adaptive", "Adaptive floor", vad.adaptive !== false)}
          ${rowNum("vad.threshold", "Threshold", vad.threshold ?? 0.42, 0.05, 0.95, 0.01)}
          ${rowNum("vad.min_speech_ms", "Min speech ms", vad.min_speech_ms ?? 350, 80, 2000, 10)}
          ${rowNum("vad.min_silence_ms", "Min silence ms", vad.min_silence_ms ?? 600, 80, 3000, 10)}
          ${rowNum("vad.merge_gap_s", "Max pause join s", vad.merge_gap_s ?? 0.7, 0.1, 3, 0.05)}
          <canvas class="spark" id="vad-spark" height="56"></canvas>
          <div class="panel-title">Enhancement</div>
          ${rowToggle("enhance.enabled", "Noise reduction", enh.enabled !== false)}
          ${rowToggle("enhance.agc", "AGC / dynaudnorm", enh.agc !== false)}
          <div class="field"><label>Engine</label><span class="mono">${esc(enh.engine || "afftdn")}</span></div>
          <p class="faint">После загрузки: streamed adaptive cleanup (highpass + afftdn tn=1 + dynaudnorm) на всём файле, затем VAD с порогом по окнам — и на длинных записях тоже.</p>
        </aside>
        <section class="center">
          <div class="timeline-wrap">
            <div class="transport">
              <button class="go" data-act="toggle-play" aria-label="Play">${viewIsPlaying() ? "❚❚" : "▶"}</button>
              <div class="track-sw" role="tablist">
                <button class="${state.track === "full" ? "on" : ""}" data-act="track-full">Полная</button>
                <button class="${state.track === "vad" ? "on" : ""}" data-act="track-vad">VAD-нарезка</button>
              </div>
              <div class="vol orig">
                <label>Исходник</label>
                <input type="range" id="vol-orig" min="0" max="100" value="${Math.round(state.volOrig * 100)}" />
                <span class="mono faint" id="vol-orig-n">${Math.round(state.volOrig * 100)}</span>
              </div>
              <div class="vol">
                <label>Очищенный</label>
                <input type="range" id="vol-clean" min="0" max="100" value="${Math.round(state.volClean * 100)}" />
                <span class="mono faint" id="vol-clean-n">${Math.round(state.volClean * 100)}</span>
              </div>
              <span class="clock-now ${p.clock_source === "anchor" ? "anchored" : ""}" id="clock-now" title="${p.clock_source === "anchor" ? "Время откалибровано по точке" : "Время из имени файла"}">${absLabelSafe(state.playhead)}</span>
              <button class="icon-btn" data-act="set-clock" title="Эта точка таймлайна — реальное время. Все таймкоды записи пересчитаются от неё.">Задать время</button>
              <button class="icon-btn" data-act="copy-shift" title="Скопировать сдвиг от времени в имени файла к откалиброванному началу">Копировать смещение</button>
              <button class="icon-btn" data-act="paste-shift" title="Прибавить скопированный сдвиг к времени из имени этой записи">Вставить смещение</button>
              <span class="faint" id="clock-note"></span>
              <span class="faint" id="loop-lab">${state.loop ? "петля вкл" : "петля выкл"}</span>
            </div>
            <div class="tl-tools">
              <span>TIMELINE · zoomable</span>
              <span class="mono" id="tl-range"></span>
              <button data-act="zoom-in">+</button>
              <button data-act="zoom-out">−</button>
              <button data-act="clear-loop">сброс петли</button>
              <button data-act="add-marker" title="Поставить метку в текущей точке (M)">метка</button>
              <button data-act="marker-prev" title="Предыдущая метка (Alt+←)">← метка</button>
              <button data-act="marker-next" title="Следующая метка (Alt+→)">метка →</button>
              <span class="faint" id="marker-note">${esc(state.markerNote || "")}</span>
              <span class="faint">клик — курсор · M — метка · Alt+←/→ — по меткам · space — play · колесо — зум · drag — панорама</span>
            </div>
            <div class="tl-stack" id="tl-stack">
              <div class="loop-ruler" id="loop-box"><canvas id="loop" height="28"></canvas></div>
              <div class="marker-ruler" id="marker-box" title="Клик — новая метка · перетаскивание двигает · Shift+клик — имя · двойной клик или правый клик — удалить"><canvas id="marks" height="22"></canvas></div>
              <canvas id="tl" height="196"></canvas>
              <div class="lanes">
                <div class="lane ${state.track === "full" ? "selected" : ""}">
                  <button class="lane-pick" data-act="track-full"><b>ПОЛНАЯ</b></button>
                  <div class="lane-track" id="lane-full"></div>
                </div>
                <div class="lane ${state.track === "vad" ? "selected" : ""}">
                  <button class="lane-pick" data-act="track-vad"><b>VAD</b></button>
                  <div class="lane-track" id="lane-vad"></div>
                </div>
              </div>
              <div id="play-cursor" class="play-cursor" aria-hidden="true"></div>
            </div>
          </div>
          <button type="button" class="split-handle" id="ws-split" aria-label="Resize panes"></button>
          <div class="tx">
            <div class="tx-head">
              <label class="muted"><input type="checkbox" id="sel-all" /> <span id="seg-count">${shown.length}</span> segments</label>
              <input type="search" id="tx-q" placeholder="Search transcript…" value="${esc(state.q)}" />
              <label class="muted" title="Прокручивать таблицу за playhead">
                <input type="checkbox" id="tx-follow" ${state.txFollow ? "checked" : ""} /> follow
              </label>
            </div>
            ${spkBarHtml(p, segs)}
            ${["queued", "vad", "processing"].includes(p.status) ? `<div class="asr-live" id="asr-live">${asrLiveHtml(p)}</div>` : ""}
            <div class="table-wrap">
              <table class="tx-table">
                <thead><tr>
                  <th></th><th></th><th>#</th><th>Диктор</th><th>Wall clock</th><th>Rel</th><th>Transcript</th><th>Clip</th>
                </tr></thead>
                <tbody>
                  ${shown.map((s) => rowHtml(s)).join("") || `<tr><td colspan="7" class="empty">Нет строк транскрипта</td></tr>`}
                </tbody>
              </table>
            </div>
          </div>
        </section>
        <aside class="inspector">
          <div class="panel-title">Inspector · clip player
            <button type="button" class="icon-btn" data-act="toggle-ins" aria-label="Свернуть инспектор">›</button>
          </div>
          ${ins ? inspectHtml(ins) : `<div class="empty">Выберите сегмент</div>`}
        </aside>
      </div>
      ${state.parallel ? parallelHtml() : ""}`;
    bindWs();
    applyWsSplit();
    bindSplitHandle();
    drawTimeline();
    drawVadSpark();
    if (ins && ins.clip) loadInspWave(ins);
  }

  function rowToggle(key, label, on) {
    return `<div class="toggle-row"><span>${esc(label)}</span>
      <button class="switch ${on ? "on" : ""}" data-tog="${key}" aria-pressed="${on}"><i></i></button></div>`;
  }
  function rowNum(key, label, val, min, max, step) {
    return `<div class="field"><label>${esc(label)}</label>
      <input class="num" type="number" data-num="${key}" value="${val}" min="${min}" max="${max}" step="${step}" /></div>`;
  }

  function relClock(sec) {
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${s.toFixed(2).padStart(5, "0")}`;
  }

  function speakerColor(id) {
    const pal = ["#22d3ee", "#f59e0b", "#34d399", "#a78bfa", "#fb923c", "#f87171", "#60a5fa"];
    if (!id) return "#5c6b82";
    let h = 0;
    for (let i = 0; i < id.length; i++) h = (h * 33 + id.charCodeAt(i)) >>> 0;
    return pal[h % pal.length];
  }

  function threshOf(p) {
    const t = (p && p.thresh) || {};
    const high = Math.min(0.95, Math.max(0.35, +t.high || 0.62));
    const low = Math.min(high, Math.max(0.1, +t.low || 0.4));
    const margin = Math.min(0.3, Math.max(0.01, +t.margin || 0.07));
    return { high, margin, low };
  }

  function classifyJs(scores, th) {
    const ranked = Object.entries(scores || {})
      .map(([id, sc]) => [id, +sc])
      .filter((x) => Number.isFinite(x[1]))
      .sort((a, b) => b[1] - a[1]);
    if (!ranked.length) return { speaker_id: null, conf: 0, status: "unmatched" };
    const [topId, top] = ranked[0];
    const second = ranked.length > 1 ? ranked[1][1] : 0;
    if (top >= th.high && top - second >= th.margin) return { speaker_id: topId, conf: top, status: "auto" };
    if (top >= th.low) return { speaker_id: topId, conf: top, status: "low" };
    return { speaker_id: null, conf: top, status: "unmatched" };
  }

  function liveSpeakerTally() {
    const th = state.spkThresh || threshOf(state.project);
    const names = Object.fromEntries((state.speakers || []).map((s) => [s.id, s.name]));
    const by = {};
    let auto = 0;
    let low = 0;
    let unmatched = 0;
    let manual = 0;
    (state.project.segments_detail || []).forEach((s) => {
      let st = s.speaker_status;
      let sid = s.speaker_id;
      if (st !== "manual") {
        const hit = classifyJs(s.speaker_scores, th);
        st = hit.status;
        sid = hit.speaker_id;
      }
      if (st === "manual" && sid) manual += 1;
      else if (st === "auto" && sid) auto += 1;
      else if (st === "low") low += 1;
      else unmatched += 1;
      if (sid) {
        const slot = (by[sid] = by[sid] || { id: sid, name: names[sid] || s.speaker || sid, n: 0 });
        slot.n += 1;
      }
    });
    return { auto, low, unmatched, manual, associated: auto + manual, by };
  }

  function speakerTallyInner() {
    const t = liveSpeakerTally();
    const parts = Object.values(t.by).map((s) => `${esc(s.name)} ${s.n}`);
    parts.push(`? ${t.low}`, `— ${t.unmatched}`);
    return `<b>${t.associated}</b> assigned · ${parts.join(" · ")}`;
  }

  function speakerStatsHtml(p, segs) {
    const st = (p && p.speaker_stats) || {};
    const low = segs.filter((s) => s.speaker_status === "low").length || st.low || 0;
    const auto = st.assigned || st.associated || segs.filter((s) => s.speaker_id).length;
    if (!auto && !low) return "";
    return `<span class="muted" id="spk-head-stats">дикторы ${auto}${low ? ` · ?${low}` : ""}</span>`;
  }

  function spkBarHtml(p, segs) {
    const hasScores = segs.some((s) => s.speaker_scores && Object.keys(s.speaker_scores).length);
    if (!hasScores && !(p.speaker_stats && p.speaker_stats.lines)) return "";
    const th = state.spkThresh || threshOf(p);
    return `<div class="spk-bar" id="spk-bar">
      <label title="Порог авто-назначения">high
        <input type="range" id="spk-high" min="35" max="95" step="1" value="${Math.round(th.high * 100)}" />
        <b id="spk-high-n">${th.high.toFixed(2)}</b>
      </label>
      <label title="Зазор до 2-го кандидата">gap
        <input type="range" id="spk-margin" min="1" max="30" step="1" value="${Math.round(th.margin * 100)}" />
        <b id="spk-margin-n">${th.margin.toFixed(2)}</b>
      </label>
      <label title="Ниже — не назначать">low
        <input type="range" id="spk-low" min="10" max="80" step="1" value="${Math.round(th.low * 100)}" />
        <b id="spk-low-n">${th.low.toFixed(2)}</b>
      </label>
      <div class="spk-tally" id="spk-tally">${speakerTallyInner()}</div>
      <button type="button" class="icon-btn" data-act="harvest-spk" title="Размеченные строки → эталоны диктора">в библиотеку</button>
    </div>`;
  }

  function applyThreshToRows() {
    if (!state.project) return;
    const th = state.spkThresh || threshOf(state.project);
    const names = Object.fromEntries((state.speakers || []).map((s) => [s.id, s.name]));
    (state.project.segments_detail || []).forEach((s) => {
      if (s.speaker_status === "manual") return;
      const hit = classifyJs(s.speaker_scores, th);
      s.speaker_id = hit.speaker_id;
      s.speaker = hit.speaker_id ? names[hit.speaker_id] || hit.speaker_id : "SPEECH";
      s.speaker_status = hit.status;
      s.speaker_conf = hit.conf;
      const sel = app.querySelector(`select[data-spk="${s.n}"]`);
      if (sel) {
        sel.value = hit.speaker_id || "";
        sel.className = `spk-sel ${hit.status || ""}`;
      }
      const tr = app.querySelector(`tr[data-n="${s.n}"]`);
      if (tr) {
        const q = tr.querySelector(".spk-q");
        if (hit.status === "low" && !q) {
          const cell = tr.querySelector(".spk");
          if (cell) cell.insertAdjacentHTML("beforeend", `<span class="spk-q" title="лоуконф">?</span>`);
        } else if (hit.status !== "low" && q) q.remove();
      }
    });
    const tally = $("#spk-tally");
    if (tally) tally.innerHTML = speakerTallyInner();
    const head = $("#spk-head-stats");
    if (head) {
      const t = liveSpeakerTally();
      head.textContent = `дикторы ${t.associated}${t.low ? ` · ?${t.low}` : ""}`;
    }
    const hn = $("#spk-high-n");
    const mn = $("#spk-margin-n");
    const ln = $("#spk-low-n");
    if (hn) hn.textContent = th.high.toFixed(2);
    if (mn) mn.textContent = th.margin.toFixed(2);
    if (ln) ln.textContent = th.low.toFixed(2);
  }

  let threshTimer = 0;
  function bindThreshSliders() {
    const read = () => {
      const high = ($("#spk-high") && +$("#spk-high").value / 100) || 0.62;
      const margin = ($("#spk-margin") && +$("#spk-margin").value / 100) || 0.07;
      let low = ($("#spk-low") && +$("#spk-low").value / 100) || 0.4;
      if (low > high) low = high;
      state.spkThresh = { high, margin, low };
      applyThreshToRows();
    };
    const commit = () => {
      if (!state.group || !state.project || !state.spkThresh) return;
      api(`${recBase()}/speakers`, { method: "PATCH", json: { thresh: state.spkThresh } }).catch(() => {});
    };
    ["spk-high", "spk-margin", "spk-low"].forEach((id) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.addEventListener("input", () => {
        read();
        clearTimeout(threshTimer);
        threshTimer = setTimeout(commit, 450);
      });
      el.addEventListener("change", commit);
    });
  }

  async function harvestToLibrary() {
    if (!state.group || !state.project) return;
    const ns = [...state.selected];
    try {
      const out = await api(`${recBase()}/speakers/harvest`, {
        method: "POST",
        json: ns.length ? { ns } : {},
      });
      const added = out.added || 0;
      const skipped = out.skipped || 0;
      openModal({
        title: "В библиотеку",
        text: ns.length
          ? `С выделенных строк: ${added} эталонов${skipped ? `, пропуск ${skipped}` : ""}.`
          : `С ручной разметки: ${added} эталонов${skipped ? `, пропуск ${skipped}` : ""}.`,
        fields: [],
        submitLabel: "OK",
        onSubmit: async () => {},
      });
      const data = await api("/api/speakers");
      state.speakers = data.speakers || [];
    } catch (err) {
      openModal({ title: "В библиотеку", text: err.message, fields: [], submitLabel: "OK", onSubmit: async () => {} });
    }
  }

  function speakerSelectHtml(s) {
    const list = state.speakers || [];
    const cur = s.speaker_id || "";
    const cands = s.speaker_candidates || [];
    const ids = new Set(list.map((sp) => sp.id));
    const extra = cands.filter((c) => c.id && !ids.has(c.id));
    const opts = [`<option value="">—</option>`]
      .concat(
        list.map(
          (sp) =>
            `<option value="${esc(sp.id)}" ${sp.id === cur ? "selected" : ""}>${esc(sp.name)}${
              cands.find((c) => c.id === sp.id) ? ` ${(Number(cands.find((c) => c.id === sp.id).score) * 100).toFixed(0)}%` : ""
            }</option>`
        )
      )
      .concat(extra.map((c) => `<option value="${esc(c.id)}">${esc(c.name || c.id)}</option>`));
    const st = s.speaker_status || "";
    const col = speakerColor(cur);
    return `<span class="spk-dot" style="background:${col}"></span>
      <select data-spk="${s.n}" class="spk-sel ${st}" title="${esc(st)}${s.speaker_cluster ? ` · ${s.speaker_cluster}` : ""}">${opts.join("")}</select>
      ${st === "low" ? `<span class="spk-q" title="лоуконф — выбери из списка">?</span>` : ""}`;
  }

  async function matchSpeakersNow() {
    if (!state.group || !state.project) return;
    if (!(state.speakers || []).length) {
      openModal({
        title: "Дикторы",
        text: "Сначала заведи дикторов в Speakers и положи короткий эталон голоса.",
        fields: [],
        submitLabel: "OK",
        onSubmit: async () => {
          location.hash = "#/speakers";
        },
      });
      return;
    }
    const btn = app.querySelector("[data-act=match-spk]");
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Матч…";
    }
    try {
      await api(`${recBase()}/speakers/match`, { method: "POST" });
      await load();
    } catch (err) {
      openModal({ title: "Дикторы", text: err.message, fields: [], submitLabel: "OK", onSubmit: async () => {} });
      if (btn) {
        btn.disabled = false;
        btn.textContent = "Дикторы";
      }
    }
  }

  function fxSnapshot() {
    const s = state.fx || defaultFxSettings();
    return {
      on: s.on !== false,
      eq: (s.eq || []).slice(),
      lfHz: s.lfHz,
      lfDb: s.lfDb,
      hfHz: s.hfHz,
      hfDb: s.hfDb,
      thresh: s.thresh,
      ratio: s.ratio,
      attack: s.attack,
      release: s.release,
      knee: s.knee,
      makeup: s.makeup,
      deecho: !!s.deecho,
      deechoAmt: +s.deechoAmt || 0,
      agc: s.agc !== false,
      agcTarget: +s.agcTarget || -20,
      agcMax: s.agcMax == null ? 18 : +s.agcMax,
      agcMargin: s.agcMargin == null ? 11 : +s.agcMargin,
      adapt: s.adapt !== false,
    };
  }

  function speakerAtTime(t) {
    const segs = (state.project && state.project.segments_detail) || [];
    const hit = segs.find((s) => t >= s.start_rel && t < s.end_rel - 0.001);
    return hit || null;
  }

  function fxForSpeaker(sid) {
    if (!sid) return null;
    const ov = state.project && state.project.fx_overrides && state.project.fx_overrides[sid];
    if (ov && Array.isArray(ov.eq) && ov.eq.length === EQ_HZ.length) return ov;
    const sp = (state.speakers || []).find((x) => x.id === sid);
    if (sp && sp.fx && Array.isArray(sp.fx.eq) && sp.fx.eq.length === EQ_HZ.length) return sp.fx;
    return null;
  }

  function applySpeakerFx(t, force) {
    if (!state.followFx || !state.project) return;
    const seg = speakerAtTime(t);
    const sid = seg && seg.speaker_id;
    if (!force && sid === state.fxSpeakerId) return;
    state.fxSpeakerId = sid || null;
    const next = fxForSpeaker(sid);
    if (!next) return;
    const pos = { x: state.fx.x, y: state.fx.y };
    state.fx = { ...defaultFxSettings(), ...next, eq: next.eq.slice(), ...pos };
    applyFxSettings();
    const panel = document.getElementById("fx-float");
    if (panel && state.showFx) paintFxPanel();
  }

  function rowHtml(s) {
    const on = state.selected.has(s.n);
    const ins = state.inspect && state.inspect.n === s.n;
    return `<tr class="${ins ? "sel" : ""}" data-n="${s.n}">
      <td><input type="checkbox" data-sel="${s.n}" ${on ? "checked" : ""} /></td>
      <td><button class="play" data-play="${s.n}" aria-label="Play">▶</button></td>
      <td class="mono faint">${s.n}</td>
      <td class="spk">${speakerSelectHtml(s)}</td>
      <td class="mono">${esc(s.clock || s.start_label || "")}</td>
      <td class="mono faint">${relClock(s.start_rel)}</td>
      <td class="t">${esc(s.text)}</td>
      <td class="mono faint">${esc(s.clip || "—")}</td>
    </tr>`;
  }

  function inspectHtml(s) {
    return `
      <div class="insp-player">
        <div class="insp-art">CLIP ${s.span_i ?? "—"} · ${esc(s.clip || "no file")}</div>
        <canvas id="insp-wave" height="48"></canvas>
        <div class="ctrls">
          <button class="play" data-play="${s.n}">▶</button>
          <input type="range" id="seek" min="0" max="1000" value="0" style="flex:1" />
          <span class="mono faint" id="tpos">0.0</span>
        </div>
      </div>
      <div class="seg-text">${esc(s.text)}</div>
      <div class="kv">
        <span>Wall start</span><b class="mono">${esc(s.start_label || "—")}</b>
        <span>Wall end</span><b class="mono">${esc(s.end_label || "—")}</b>
        <span>File offset</span><b class="mono">${relClock(s.start_rel)}–${relClock(s.end_rel)}</b>
        <span>Duration</span><b>${(s.dur || 0).toFixed(2)} s</b>
        <span>Диктор</span><b>${esc(s.speaker || s.speaker_id || "—")}${s.speaker_status === "low" ? " ?" : ""}</b>
        <span>VAD region</span><b>SPEECH</b>
        <span>Audio</span><b class="mono">${esc(s.clip || "—")}</b>
      </div>
      <button type="button" class="icon-btn" data-act="set-clock-seg" title="Начало этой строки — реальное время. Остальные события сдвинутся на ту же разницу.">Задать время начала</button>`;
  }

  function parallelHtml() {
    const segs = (state.project.segments_detail || []).filter((s) => state.selected.has(s.n));
    return `<div class="overlay" id="par">
      <div class="modal">
        <div class="tl-tools"><b>Parallel listen</b>
          <button data-act="play-all">Play all</button>
          <button data-act="stop-all">Stop</button>
          <button data-act="close-par" style="margin-left:auto">Close</button>
        </div>
        <div class="par-grid">
          ${segs
            .map(
              (s) => `<div class="par-card">
            <div class="mono faint">${esc(s.clock)} · #${s.n}</div>
            <p>${esc(s.text)}</p>
            <audio controls src="${s.clip ? `${recBase()}/clips/${encodeURIComponent(s.clip)}` : ""}"
              data-off="${s.clip_offset || 0}"></audio>
          </div>`
            )
            .join("")}
        </div>
      </div>
    </div>`;
  }

  function bindTxTable() {
    app.querySelectorAll("tr[data-n]").forEach((tr) => {
      if (tr.dataset.bound) return;
      tr.dataset.bound = "1";
      tr.addEventListener("click", (e) => {
        if (e.target.closest("input,button,select")) return;
        pick(+tr.dataset.n, false);
      });
    });
    app.querySelectorAll("[data-play]").forEach((b) => {
      if (b.dataset.bound) return;
      b.dataset.bound = "1";
      b.addEventListener("click", () => playSeg(+b.dataset.play));
    });
    app.querySelectorAll("[data-spk]").forEach((sel) => {
      if (sel.dataset.bound) return;
      sel.dataset.bound = "1";
      sel.addEventListener("change", async () => {
        const n = +sel.dataset.spk;
        const speakerId = sel.value || null;
        try {
          await api(`${recBase()}/speakers`, { method: "PATCH", json: { n, speaker_id: speakerId } });
          const seg = (state.project.segments_detail || []).find((x) => x.n === n);
          if (seg) {
            const sp = (state.speakers || []).find((x) => x.id === speakerId);
            seg.speaker_id = speakerId;
            seg.speaker = sp ? sp.name : speakerId || "SPEECH";
            seg.speaker_status = speakerId ? "manual" : "unmatched";
            seg.speaker_conf = speakerId ? 1 : null;
          }
          applySpeakerFx(state.playhead, true);
        } catch (err) {
          openModal({ title: "Диктор", text: err.message, fields: [], submitLabel: "OK", onSubmit: async () => {} });
        }
      });
      sel.addEventListener("click", (e) => e.stopPropagation());
    });
    app.querySelectorAll("[data-sel]").forEach((c) => {
      if (c.dataset.bound) return;
      c.dataset.bound = "1";
      c.addEventListener("change", () => {
        const n = +c.dataset.sel;
        if (c.checked) state.selected.add(n);
        else state.selected.delete(n);
        const par = app.querySelector("[data-act=parallel]");
        if (par) {
          par.disabled = state.selected.size < 2;
          par.textContent = `Parallel (${state.selected.size})`;
        }
      });
    });
  }

  function bindWs() {
    ensureSources();
    app.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => (location.hash = b.dataset.go)));
    app.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", () => onAct(b.dataset.act)));
    app.querySelectorAll("[data-tog]").forEach((b) =>
      b.addEventListener("click", () => {
        b.classList.toggle("on");
        b.setAttribute("aria-pressed", b.classList.contains("on"));
      })
    );
    const follow = $("#tx-follow");
    if (follow)
      follow.addEventListener("change", () => {
        state.txFollow = follow.checked;
        saveWsLayout();
      });
    const tq = $("#tx-q");
    if (tq)
      tq.addEventListener("input", () => {
        state.q = tq.value;
        const needle = state.q.toLowerCase();
        let vis = 0;
        app.querySelectorAll("tr[data-n]").forEach((tr) => {
          const hit = !needle || tr.innerText.toLowerCase().includes(needle);
          tr.style.display = hit ? "" : "none";
          if (hit) vis += 1;
        });
        const sc = $("#seg-count");
        if (sc) sc.textContent = String(vis);
      });
    if (tq && state.q) tq.dispatchEvent(new Event("input"));
    if (state.jumpN) {
      const n = state.jumpN;
      state.jumpN = null;
      pick(n, false);
      const trHit = app.querySelector(`tr[data-n="${n}"]`);
      if (trHit) trHit.scrollIntoView({ block: "center" });
    }
    bindTxTable();
    bindThreshSliders();
    const sa = $("#sel-all");
    if (sa)
      sa.addEventListener("change", () => {
        (state.project.segments_detail || []).forEach((s) => {
          if (sa.checked) state.selected.add(s.n);
          else state.selected.delete(s.n);
        });
        renderWorkspace();
      });
    const seek = $("#seek");
    if (seek)
      seek.addEventListener("input", () => {
        const d = state.project.duration_sec || 1;
        playFrom((+seek.value / 1000) * d);
      });
    const canvas = $("#tl");
    if (canvas) bindTimeline(canvas);
    bindLoopRuler();
    bindMarkerRuler();
    bindVolumes();
    applyVolumes();
    app.querySelectorAll(".chip-seg").forEach((el) =>
      el.addEventListener("click", () => {
        const n = +el.dataset.n;
        if (n) pick(n, true);
      })
    );
  }

  function onAct(act) {
    const d = state.project.duration_sec || 1;
    if (act === "fit") {
      state.viewT0 = 0;
      state.viewT1 = d;
      drawTimeline();
    } else if (act === "zoom-in") zoomAt(0.5, 0.8);
    else if (act === "zoom-out") zoomAt(0.5, 1.25);
    else if (act === "parallel") {
      if (state.selected.size >= 2) {
        state.parallel = true;
        renderWorkspace();
      }
    } else if (act === "close-par") {
      state.parallel = false;
      renderWorkspace();
    } else if (act === "play-all") {
      document.querySelectorAll(".par-card audio").forEach((a) => {
        const off = parseFloat(a.dataset.off || "0");
        a.currentTime = off;
        a.play().catch(() => {});
      });
    } else if (act === "stop-all") {
      document.querySelectorAll(".par-card audio").forEach((a) => a.pause());
      stopPlay(true);
    } else if (act === "toggle-set") {
      state.showSet = !state.showSet;
      saveWsLayout();
      renderWorkspace();
    } else if (act === "toggle-ins") {
      state.showIns = !state.showIns;
      saveWsLayout();
      renderWorkspace();
    } else if (act === "split-tb") {
      state.wsDir = "tb";
      saveWsLayout();
      renderWorkspace();
    } else if (act === "split-lr") {
      state.wsDir = "lr";
      saveWsLayout();
      renderWorkspace();
    } else if (act === "toggle-fx") {
      state.showFx = !state.showFx;
      syncFxPanel();
    } else if (act === "toggle-play") {
      togglePlay();
    } else if (act === "track-full") {
      state.track = "full";
      renderWorkspace();
      if (viewIsPlaying()) playFrom(state.playhead);
    } else if (act === "track-vad") {
      state.track = "vad";
      renderWorkspace();
      if (viewIsPlaying()) playFrom(state.playhead);
    } else if (act === "clear-loop") {
      state.loop = null;
      if (state.session && engineOwnsView()) state.session.loop = null;
      drawLoop();
      const lab = $("#loop-lab");
      if (lab) lab.textContent = "петля выкл";
    } else if (act === "add-marker") {
      addMarkerAt(state.playhead || 0);
    } else if (act === "marker-prev") {
      jumpMarker(-1);
    } else if (act === "marker-next") {
      jumpMarker(1);
    } else if (act === "harvest-spk" && state.group && state.project) {
      harvestToLibrary();
    } else if (act === "match-spk" && state.group && state.project) {
      matchSpeakersNow();
      return;
    } else if (act === "copy-shift") {
      copySavedShift().catch(showClockError);
    } else if (act === "paste-shift") {
      pasteShiftNow().catch(showClockError);
    } else if (act === "set-clock") {
      openClockModal(state.playhead || 0);
    } else if (act === "set-clock-seg") {
      const rel = state.inspect ? state.inspect.start_rel : state.playhead || 0;
      openClockModal(rel);
    } else if (act === "clear-clock" && state.group && state.project) {
      openModal({
        title: "Сбросить калибровку",
        submitLabel: "Сбросить",
        text: "Вернуть время из имени файла. Точка калибровки будет удалена, и все таймкоды пересчитаются заново.",
        fields: [],
        error: "",
        onSubmit: async () => {
          await api(`${recBase()}/clock`, { method: "DELETE" });
          state.keepPlace = true;
        },
      });
    } else if (act === "process-now" && state.group && state.project) {
      api(
        `/api/projects/${encodeURIComponent(state.group.id)}/recordings/${encodeURIComponent(state.project.id)}/process`,
        { method: "POST" }
      )
        .then(() => load())
        .catch((err) => {
          openModal({ title: "Process", text: err.message, fields: [], submitLabel: "OK", onSubmit: async () => {} });
        });
    }
  }

  function pick(n, play) {
    const s = (state.project.segments_detail || []).find((x) => x.n === n);
    if (!s) return;
    state.inspect = s;
    app.querySelectorAll("tr[data-n]").forEach((tr) => tr.classList.toggle("sel", +tr.dataset.n === n));
    const mid = s.start_rel;
    const span = Math.max(20, state.viewT1 - state.viewT0);
    const dur = state.project.duration_sec || mid + span;
    if (mid < state.viewT0 || mid > state.viewT1) {
      state.viewT0 = Math.max(0, mid - span * 0.25);
      state.viewT1 = Math.min(dur, state.viewT0 + span);
      state.viewT0 = Math.max(0, state.viewT1 - span);
    }
    seekCursor(s.start_rel, { play: !!(play || state.playing) });
    refreshInspector(s);
  }

  function playSeg(n) {
    const s = (state.project.segments_detail || []).find((x) => x.n === n);
    if (!s) return;
    state.inspect = s;
    playFrom(s.start_rel);
  }

  function onKey(e) {
    if (state.modal) {
      if (e.key === "Escape") {
        e.preventDefault();
        state.modal = null;
        render();
      }
      return;
    }
    if (state.view !== "workspace" || !state.project) return;
    if (e.target.closest && e.target.closest("#mini-player")) return;
    if (e.target.matches("input,textarea,select")) return;
    const segs = state.project.segments_detail || [];
    const cur = state.inspect ? segs.findIndex((s) => s.n === state.inspect.n) : -1;
    if (e.code === "Space") {
      e.preventDefault();
      togglePlay();
    } else if (e.code === "KeyM" && !e.repeat && !e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      addMarkerAt(state.playhead || 0);
    } else if (e.altKey && !e.ctrlKey && !e.metaKey && (e.code === "ArrowLeft" || e.code === "ArrowRight")) {
      e.preventDefault();
      jumpMarker(e.code === "ArrowRight" ? 1 : -1);
    } else if (e.key === "ArrowDown" && cur < segs.length - 1) {
      e.preventDefault();
      pick(segs[cur + 1].n, false);
    } else if (e.key === "ArrowUp" && cur > 0) {
      e.preventDefault();
      pick(segs[cur - 1].n, false);
    } else if (e.key === "Enter" && state.inspect) playFrom(state.inspect.start_rel);
    else if (e.key === "Escape") {
      if (state.parallel) {
        state.parallel = false;
        renderWorkspace();
      } else if (viewIsPlaying()) stopPlay(true);
    }
  }

  function sessionMatches(groupId, stem) {
    return !!(state.session && state.session.groupId === groupId && state.session.stem === stem);
  }

  function engineOwnsView() {
    return !!(
      state.session &&
      state.view === "workspace" &&
      state.project &&
      state.group &&
      state.session.stem === state.project.id &&
      state.session.groupId === state.group.id
    );
  }

  function viewIsPlaying() {
    return !!(state.playing && engineOwnsView());
  }

  function audioRec() {
    if (state.session && state.session.project) {
      if (engineOwnsView()) return state.project;
      return state.session.project;
    }
    return state.project;
  }

  function audioTrack() {
    if (state.session && state.session.live && !engineOwnsView()) return state.session.track === "vad" ? "vad" : "full";
    return state.track === "vad" ? "vad" : "full";
  }

  function audioLoop() {
    if (state.session && state.session.live && !engineOwnsView()) return state.session.loop;
    return state.loop;
  }

  function setAudioHead(t) {
    if (state.session) state.session.playhead = t;
    if (!state.session || engineOwnsView()) state.playhead = t;
  }

  function audioHead() {
    if (state.session && state.session.live && !engineOwnsView()) return state.session.playhead || 0;
    return state.playhead || 0;
  }

  function freezeSession() {
    if (!state.session || !state.session.live || !state.project || !state.group) return;
    if (state.session.stem !== state.project.id || state.session.groupId !== state.group.id) return;
    state.session.playhead = state.playhead;
    state.session.track = state.track === "vad" ? "vad" : "full";
    state.session.loop = state.loop;
    state.session.project = state.project;
    state.session.title = recTitle(state.project);
    state.session.fileName = fileNameOf(state.project);
    state.session.playing = !!state.playing;
  }

  function baseRate() {
    const r = Number(state.playRate) || 1;
    return Math.max(0.5, Math.min(2, r));
  }

  function applyRate() {
    const r = baseRate();
    orig.playbackRate = r;
    clean.playbackRate = r;
  }

  function spansOf(rec) {
    return (rec && rec.spans_detail) || [];
  }

  function spans() {
    return spansOf(state.project);
  }

  function spanAtIn(rec, t) {
    return spansOf(rec).find((s) => t >= s.start_rel && t < s.end_rel - 0.001) || null;
  }

  function spanAt(t) {
    return spanAtIn(state.project, t);
  }

  function spanAtOrAfterIn(rec, t) {
    return spanAtIn(rec, t) || spansOf(rec).find((s) => s.end_rel > t) || null;
  }

  function spanAtOrAfter(t) {
    return spanAtOrAfterIn(state.project, t);
  }

  function spanRatio(s) {
    const od = Math.max(0.001, (s.end_rel || 0) - (s.start_rel || 0));
    const cd = Math.max(0.001, s.clean_dur || od);
    return cd / od;
  }

  function origToCleanIn(rec, t) {
    const s = spanAtIn(rec, t);
    if (!s) return null;
    return (s.clean_start || 0) + (t - s.start_rel) * spanRatio(s);
  }

  function origToClean(t) {
    return origToCleanIn(state.project, t);
  }

  function cleanToOrigIn(rec, ct) {
    const list = spansOf(rec);
    const s = list.find((sp) => ct >= (sp.clean_start || 0) && ct < (sp.clean_end || 0) - 0.0005);
    if (s) {
      const cd = Math.max(0.001, s.clean_dur || s.end_rel - s.start_rel);
      return s.start_rel + ((ct - (s.clean_start || 0)) / cd) * Math.max(0.001, s.end_rel - s.start_rel);
    }
    const prev = [...list].reverse().find((sp) => ct >= (sp.clean_end || 0) - 0.02);
    return prev ? prev.end_rel : 0;
  }

  function cleanToOrig(ct) {
    return cleanToOrigIn(state.project, ct);
  }

  function applyVolumes() {
    const master = Math.max(0, Math.min(1, state.volMaster == null ? 1 : state.volMaster));
    orig.volume = Math.max(0, Math.min(1, state.volOrig * master));
    clean.volume = Math.max(0, Math.min(1, state.volClean * master));
    resumeFx();
  }

  const EQ_HZ = [80, 125, 200, 315, 500, 800, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000];
  const FX_STORE = "astro-fx-v1";
  const fx = {
    ctx: null,
    hp: null,
    lp: null,
    loShelf: null,
    hiShelf: null,
    eq: [],
    comp: null,
    makeup: null,
    agcGain: null,
    agcLim: null,
    agcAn: null,
    echoDry: null,
    echoTaps: [],
    inited: false,
  };
  const agc = {
    noise: 1e-4,
    speech: 0.02,
    gain: 1,
    gate: 1,
    speechAge: 1,
    lastTs: 0,
    attackMs: 120,
    capDb: 18,
    painted: 0,
    buf: null,
    loop: false,
  };
  const adapt = Object.assign(
    globalThis.astroAdapt
      ? globalThis.astroAdapt.createAdaptState()
      : { noise: [], peak: [], gain: [], hp: 45, lp: 15000, speechAge: 1, ready: false },
    { painted: 0, freq: null, bypassed: true, lastInfo: null }
  );
  const ECHO_TAPS = [
    { ms: 23, g: -0.34 },
    { ms: 39, g: -0.24 },
    { ms: 61, g: -0.16 },
    { ms: 87, g: -0.11 },
    { ms: 119, g: -0.07 },
  ];

  function defaultFxSettings() {
    return {
      on: true,
      eq: EQ_HZ.map((hz) => (hz >= 2000 && hz <= 4000 ? 3 : hz <= 125 ? -2 : 0)),
      lfHz: 90,
      lfDb: -4,
      hfHz: 9000,
      hfDb: -2,
      thresh: -22,
      ratio: 3,
      attack: 12,
      release: 180,
      knee: 10,
      makeup: 5,
      deecho: false,
      deechoAmt: 55,
      agc: true,
      agcTarget: -20,
      agcMax: 18,
      agcMargin: 11,
      adapt: true,
      x: 28,
      y: 88,
    };
  }

  function loadFxSettings() {
    try {
      const raw = JSON.parse(localStorage.getItem(FX_STORE) || "null");
      if (!raw || !Array.isArray(raw.eq) || raw.eq.length !== EQ_HZ.length) return defaultFxSettings();
      return { ...defaultFxSettings(), ...raw, eq: raw.eq };
    } catch {
      return defaultFxSettings();
    }
  }

  function saveFxSettings() {
    try {
      localStorage.setItem(FX_STORE, JSON.stringify(state.fx || defaultFxSettings()));
    } catch {
      /* ignore */
    }
  }

  if (!state.fx) state.fx = loadFxSettings();

  function ensureFxGraph() {
    if (fx.inited) return fx.ctx;
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    const ctx = new Ctx();
    const srcO = ctx.createMediaElementSource(orig);
    const srcC = ctx.createMediaElementSource(clean);
    const hp = ctx.createBiquadFilter();
    hp.type = "highpass";
    const loShelf = ctx.createBiquadFilter();
    loShelf.type = "lowshelf";
    loShelf.frequency.value = 140;
    const eq = EQ_HZ.map((hz) => {
      const n = ctx.createBiquadFilter();
      n.type = "peaking";
      n.frequency.value = hz;
      n.Q.value = 1.35;
      n.gain.value = 0;
      return n;
    });
    const hiShelf = ctx.createBiquadFilter();
    hiShelf.type = "highshelf";
    hiShelf.frequency.value = 7500;
    const lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    const echoHp = ctx.createBiquadFilter();
    echoHp.type = "highpass";
    echoHp.frequency.value = 480;
    echoHp.Q.value = 0.7;
    const echoLp = ctx.createBiquadFilter();
    echoLp.type = "lowpass";
    echoLp.frequency.value = 4300;
    const echoDry = ctx.createGain();
    echoDry.gain.value = 1;
    const echoMix = ctx.createGain();
    echoMix.gain.value = 1;
    const echoTaps = ECHO_TAPS.map((tap) => {
      const d = ctx.createDelay(0.25);
      d.delayTime.value = tap.ms / 1000;
      const g = ctx.createGain();
      g.gain.value = 0;
      return { delay: d, gain: g, base: tap.g };
    });
    const comp = ctx.createDynamicsCompressor();
    const makeup = ctx.createGain();
    const agcHp = ctx.createBiquadFilter();
    agcHp.type = "highpass";
    agcHp.frequency.value = 250;
    agcHp.Q.value = 0.7;
    const agcLp = ctx.createBiquadFilter();
    agcLp.type = "lowpass";
    agcLp.frequency.value = 3800;
    agcLp.Q.value = 0.7;
    const agcAn = ctx.createAnalyser();
    agcAn.fftSize = 2048;
    agcAn.smoothingTimeConstant = 0;
    const agcGain = ctx.createGain();
    agcGain.gain.value = 1;
    const agcLim = ctx.createDynamicsCompressor();
    agcLim.threshold.value = -1.5;
    agcLim.knee.value = 0;
    agcLim.ratio.value = 1;
    agcLim.attack.value = 0.003;
    agcLim.release.value = 0.08;
    srcO.connect(hp);
    srcC.connect(hp);
    hp.connect(loShelf);
    loShelf.connect(eq[0]);
    for (let i = 0; i < eq.length - 1; i++) eq[i].connect(eq[i + 1]);
    eq[eq.length - 1].connect(hiShelf);
    hiShelf.connect(lp);
    const adaptHp = ctx.createBiquadFilter();
    adaptHp.type = "highpass";
    adaptHp.frequency.value = 20;
    adaptHp.Q.value = 0.7;
    const adaptHz = (globalThis.astroAdapt && globalThis.astroAdapt.ADAPT_HZ) || [100, 200, 400, 800, 1600, 2500, 4000, 6500];
    const adaptEq = adaptHz.map((hz) => {
      const n = ctx.createBiquadFilter();
      n.type = "peaking";
      n.frequency.value = hz;
      n.Q.value = 1.05;
      n.gain.value = 0;
      return n;
    });
    const adaptLp = ctx.createBiquadFilter();
    adaptLp.type = "lowpass";
    adaptLp.frequency.value = 20000;
    adaptLp.Q.value = 0.7;
    const adaptAn = ctx.createAnalyser();
    adaptAn.fftSize = 2048;
    adaptAn.smoothingTimeConstant = 0.15;
    lp.connect(echoDry);
    echoDry.connect(echoMix);
    lp.connect(echoHp);
    echoHp.connect(echoLp);
    echoTaps.forEach((t) => {
      echoLp.connect(t.delay);
      t.delay.connect(t.gain);
      t.gain.connect(echoMix);
    });
    echoMix.connect(comp);
    comp.connect(makeup);
    makeup.connect(agcGain);
    makeup.connect(agcHp);
    agcHp.connect(agcLp);
    agcLp.connect(agcAn);
    agcGain.connect(adaptAn);
    agcGain.connect(adaptHp);
    adaptHp.connect(adaptEq[0]);
    for (let i = 0; i < adaptEq.length - 1; i++) adaptEq[i].connect(adaptEq[i + 1]);
    adaptEq[adaptEq.length - 1].connect(adaptLp);
    adaptLp.connect(agcLim);
    agcLim.connect(ctx.destination);
    fx.ctx = ctx;
    fx.agcGain = agcGain;
    fx.agcLim = agcLim;
    fx.agcAn = agcAn;
    fx.adaptHp = adaptHp;
    fx.adaptLp = adaptLp;
    fx.adaptEq = adaptEq;
    fx.adaptAn = adaptAn;
    fx.hp = hp;
    fx.lp = lp;
    fx.loShelf = loShelf;
    fx.hiShelf = hiShelf;
    fx.eq = eq;
    fx.echoDry = echoDry;
    fx.echoTaps = echoTaps;
    fx.comp = comp;
    fx.makeup = makeup;
    fx.inited = true;
    applyFxSettings();
    startSpeechAgc();
    return ctx;
  }

  function resumeFx() {
    const ctx = ensureFxGraph();
    if (ctx && ctx.state === "suspended") ctx.resume().catch(() => {});
  }

  function applyFxSettings() {
    if (!fx.inited || !state.fx) return;
    const s = state.fx;
    const now = fx.ctx.currentTime;
    const set = (param, v) => {
      try {
        param.cancelScheduledValues(now);
        param.setValueAtTime(v, now);
      } catch {
        param.value = v;
      }
    };
    const echoOn = !!(s.on && s.deecho);
    let amt = +s.deechoAmt || 0;
    if (amt > 1) amt /= 100;
    amt = echoOn ? Math.max(0, Math.min(1, amt)) : 0;
    if (fx.echoTaps) fx.echoTaps.forEach((t) => set(t.gain.gain, t.base * amt));
    if (fx.echoDry) set(fx.echoDry.gain, 1);
    if (!s.on) {
      set(fx.hp.frequency, 20);
      set(fx.lp.frequency, 20000);
      set(fx.loShelf.gain, 0);
      set(fx.hiShelf.gain, 0);
      fx.eq.forEach((n) => set(n.gain, 0));
      set(fx.comp.threshold, 0);
      set(fx.comp.ratio, 1);
      set(fx.makeup.gain, 1);
      setSpeechAgcBypass(true);
      setAdaptEnabled(false);
      return;
    }
    set(fx.hp.frequency, Math.max(20, +s.lfHz || 90));
    set(fx.lp.frequency, Math.max(2000, +s.hfHz || 9000));
    set(fx.loShelf.gain, Math.min(0, +s.lfDb || 0));
    set(fx.hiShelf.gain, Math.min(0, +s.hfDb || 0));
    fx.eq.forEach((n, i) => set(n.gain, +s.eq[i] || 0));
    set(fx.comp.threshold, +s.thresh || -22);
    set(fx.comp.ratio, Math.max(1, +s.ratio || 3));
    set(fx.comp.attack, Math.max(0, (+s.attack || 12) / 1000));
    set(fx.comp.release, Math.max(0.02, (+s.release || 180) / 1000));
    set(fx.comp.knee, Math.max(0, +s.knee || 10));
    const mk = Math.pow(10, (+s.makeup || 0) / 20);
    set(fx.makeup.gain, mk);
    setSpeechAgcBypass(s.agc === false);
    setAdaptEnabled(s.adapt !== false);
  }

  function linDb(x) {
    return 20 * Math.log10(Math.max(x, 1e-8));
  }

  function dbLin(db) {
    return Math.pow(10, db / 20);
  }

  function follow(cur, target, dt, tau) {
    const a = 1 - Math.exp(-dt / Math.max(0.004, tau));
    return cur + (target - cur) * a;
  }

  function setSpeechAgcBypass(bypass) {
    if (!fx.agcGain || !fx.ctx) return;
    const now = fx.ctx.currentTime;
    if (bypass) {
      try {
        fx.agcGain.gain.cancelScheduledValues(now);
        fx.agcGain.gain.setValueAtTime(1, now);
      } catch {
        fx.agcGain.gain.value = 1;
      }
    }
    if (fx.agcLim) {
      fx.agcLim.ratio.value = bypass ? 1 : 12;
      fx.agcLim.threshold.value = bypass ? 0 : -1.5;
    }
  }

  /** Speech-band level → gain. Pauses hold the last speech gain and duck back to unity. */
  function stepSpeechAgc(rms, dt, params) {
    const target = dbLin(params.targetDb);
    const margin = dbLin(params.marginDb);
    const speechOn = rms > agc.noise * margin && rms > 0.0008;
    if (rms < agc.noise) agc.noise = follow(agc.noise, rms, dt, 0.2);
    else if (!speechOn) agc.noise = follow(agc.noise, rms, dt, 3);
    agc.noise = Math.min(Math.max(agc.noise, 1e-6), Math.max(agc.speech * 0.5, 1e-5));
    if (speechOn) {
      if (agc.speechAge > 0.8) agc.speech = rms;
      agc.speech = follow(agc.speech, rms, dt, 0.1);
      agc.speechAge = 0;
    } else {
      agc.speechAge += dt;
    }
    const noiseDb = linDb(agc.noise);
    const capDb = Math.min(params.maxBoostDb, Math.max(0, params.targetDb - noiseDb - 6));
    let desired = target / Math.max(agc.speech, 1e-4);
    desired = Math.min(dbLin(capDb), Math.max(dbLin(-12), desired));
    const ratio = desired / Math.max(agc.gain, 1e-4);
    let tau;
    if (!speechOn && agc.speechAge < 1.2) tau = 8;
    else if (!speechOn) tau = 0.8;
    else if (ratio >= 1) tau = ratio > 4 ? 0.05 : ratio > 1.6 ? 0.12 : 0.28;
    else tau = ratio < 0.4 ? 0.04 : ratio < 0.75 ? 0.1 : 0.22;
    agc.gain = follow(agc.gain, desired, dt, tau);
    const gateTarget = speechOn || agc.speechAge < 0.12 ? 1 : Math.min(1, Math.max(0.15, 1 / agc.gain));
    agc.gate = follow(agc.gate, gateTarget, dt, gateTarget > agc.gate ? 0.012 : 0.09);
    agc.attackMs = Math.round(tau * 1000);
    agc.capDb = capDb;
    return {
      out: agc.gain * agc.gate,
      speechOn,
      speechDb: linDb(agc.speech),
      noiseDb,
      gainDb: linDb(agc.gain * agc.gate),
      capDb,
      attackMs: agc.attackMs,
    };
  }

  function speechAgcRms() {
    const an = fx.agcAn;
    if (!an) return 0;
    if (!agc.buf || agc.buf.length !== an.fftSize) agc.buf = new Float32Array(an.fftSize);
    an.getFloatTimeDomainData(agc.buf);
    let e = 0;
    for (let i = 0; i < agc.buf.length; i++) e += agc.buf[i] * agc.buf[i];
    return Math.sqrt(e / agc.buf.length);
  }

  function paintAgcLive(info) {
    const nowMs = performance.now();
    if (nowMs - agc.painted < 150) return;
    agc.painted = nowMs;
    const el = document.getElementById("agc-live");
    if (!el) return;
    if (!info) {
      const s = state.fx;
      el.textContent = s && s.on !== false && s.agc !== false ? "ждёт сигнал" : "выкл";
      return;
    }
    const mode = info.speechOn ? "речь" : "пауза";
    el.textContent = `речь ${info.speechDb.toFixed(1)} dB · шум ${info.noiseDb.toFixed(1)} dB · ${info.gainDb >= 0 ? "+" : ""}${info.gainDb.toFixed(1)} dB · потолок +${info.capDb.toFixed(0)} dB · ${info.attackMs} мс · ${mode}`;
  }

  function resetAdaptTracking() {
    const lib = globalThis.astroAdapt;
    if (!lib) return;
    const fresh = lib.createAdaptState();
    adapt.noise = fresh.noise;
    adapt.peak = fresh.peak;
    adapt.gain = fresh.gain;
    adapt.hp = fresh.hp;
    adapt.lp = fresh.lp;
    adapt.speechAge = fresh.speechAge;
    adapt.ready = false;
  }

  function applyAdaptTargets(info) {
    if (!fx.adaptHp || !fx.ctx) return;
    const now = fx.ctx.currentTime;
    const smooth = (param, v) => {
      try {
        param.setTargetAtTime(v, now, 0.06);
      } catch {
        param.value = v;
      }
    };
    if (!info) {
      smooth(fx.adaptHp.frequency, 20);
      smooth(fx.adaptLp.frequency, 20000);
      (fx.adaptEq || []).forEach((node) => smooth(node.gain, 0));
      return;
    }
    smooth(fx.adaptHp.frequency, info.hp);
    smooth(fx.adaptLp.frequency, info.lp);
    (fx.adaptEq || []).forEach((node, i) => smooth(node.gain, info.gains[i] || 0));
  }

  function setAdaptEnabled(on) {
    if (!fx.adaptHp) return;
    if (on) {
      adapt.bypassed = false;
      return;
    }
    if (adapt.bypassed) return;
    applyAdaptTargets(null);
    resetAdaptTracking();
    adapt.bypassed = true;
    adapt.lastInfo = null;
  }

  function readAdaptBands() {
    const an = fx.adaptAn;
    const lib = globalThis.astroAdapt;
    if (!an || !lib || !fx.ctx) return null;
    const n = an.frequencyBinCount;
    if (!adapt.freq || adapt.freq.length !== n) adapt.freq = new Float32Array(n);
    an.getFloatFrequencyData(adapt.freq);
    const sr = fx.ctx.sampleRate || 48000;
    const bands = lib.ADAPT_HZ.map((hz) => lib.bandDb(adapt.freq, sr, hz));
    let peak = -100;
    for (let i = 0; i < bands.length; i++) if (bands[i] > peak) peak = bands[i];
    if (peak < -72) return null;
    return bands;
  }

  function paintAdaptLive(info) {
    const nowMs = performance.now();
    if (nowMs - adapt.painted < 150) return;
    adapt.painted = nowMs;
    const el = document.getElementById("adapt-live");
    if (!el) return;
    const s = state.fx;
    const on = !!(s && s.on !== false && s.adapt !== false);
    if (!on) {
      el.textContent = "выкл";
      return;
    }
    if (!info) {
      el.textContent = "ждёт сигнал";
      return;
    }
    const hz = (globalThis.astroAdapt && globalThis.astroAdapt.ADAPT_HZ) || [];
    let speechG = -99;
    let noiseG = 99;
    info.gains.forEach((g, i) => {
      if (hz[i] >= 300 && hz[i] <= 4500) speechG = Math.max(speechG, g);
      noiseG = Math.min(noiseG, g);
    });
    const sg = speechG < -90 ? 0 : speechG;
    const mode = info.speechOn ? "речь" : "шум";
    el.textContent = `НЧ ${Math.round(info.hp)} Гц · ВЧ ${fmtHz(Math.round(info.lp))} · речь ${sg >= 0 ? "+" : ""}${sg.toFixed(1)} dB · шум ${noiseG.toFixed(1)} dB · ${mode}`;
  }

  function stepAdaptLive(dt, playing) {
    const s = state.fx;
    const on = !!(s && s.on !== false && s.adapt !== false && globalThis.astroAdapt);
    if (!on) {
      setAdaptEnabled(false);
      paintAdaptLive(null);
      return;
    }
    if (!playing) {
      paintAdaptLive(adapt.lastInfo || null);
      return;
    }
    const bands = readAdaptBands();
    if (!bands) {
      paintAdaptLive(adapt.lastInfo || null);
      return;
    }
    const info = globalThis.astroAdapt.stepAdaptFilter(bands, dt, adapt);
    adapt.lastInfo = info;
    applyAdaptTargets(info);
    paintAdaptLive(info);
  }

  function startSpeechAgc() {
    if (agc.loop || !fx.ctx) return;
    agc.loop = true;
    const tick = (ts) => {
      if (!fx.inited) {
        agc.loop = false;
        return;
      }
      const dt = Math.min(0.1, Math.max(0.001, (ts - (agc.lastTs || ts)) / 1000));
      agc.lastTs = ts;
      const s = state.fx;
      const enabled = !!(s && s.on !== false && s.agc !== false);
      const playing = state.playing && (!orig.paused || !clean.paused);
      if (!enabled) {
        if (!agc.bypassed) {
          setSpeechAgcBypass(true);
          agc.bypassed = true;
        }
        paintAgcLive(null);
      } else if (!playing) {
        paintAgcLive(agc.lastInfo || null);
      } else {
        agc.bypassed = false;
        const rms = speechAgcRms();
        if (rms >= 1e-5) {
          const info = stepSpeechAgc(rms, dt, {
            targetDb: +s.agcTarget || -20,
            maxBoostDb: s.agcMax == null ? 18 : +s.agcMax,
            marginDb: s.agcMargin == null ? 11 : +s.agcMargin,
          });
          agc.lastInfo = info;
          const now = fx.ctx.currentTime;
          try {
            fx.agcGain.gain.setValueAtTime(Math.max(0.05, Math.min(16, info.out)), now);
          } catch {
            fx.agcGain.gain.value = info.out;
          }
          paintAgcLive(info);
        }
      }
      stepAdaptLive(dt, playing);
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  function fmtHz(hz) {
    return hz >= 1000 ? `${(hz / 1000).toFixed(hz % 1000 === 0 ? 0 : 2).replace(/\.00$/, "")}k` : String(hz);
  }

  function ensureFxPanel() {
    let el = document.getElementById("fx-float");
    if (el) return el;
    el = document.createElement("aside");
    el.id = "fx-float";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-label", "Эффекты разборчивости");
    document.body.appendChild(el);
    bindFxDrag(el);
    return el;
  }

  function fxSlider(id, min, max, step, value, label) {
    return `<label class="fx-ctrl"><span>${esc(label)}</span>
      <input type="range" data-fx="${esc(id)}" min="${min}" max="${max}" step="${step}" value="${value}" />
      <b class="mono" data-fxv="${esc(id)}">${value}</b></label>`;
  }

  function paintFxPanel() {
    const el = ensureFxPanel();
    const s = state.fx;
    const bands = EQ_HZ.map(
      (hz, i) => `<div class="fx-band">
        <input type="range" orient="vertical" data-eq="${i}" min="-12" max="12" step="0.5" value="${s.eq[i]}" />
        <span>${fmtHz(hz)}</span>
      </div>`
    ).join("");
    el.style.left = `${s.x}px`;
    el.style.top = `${s.y}px`;
    el.innerHTML = `
      <header class="fx-head" id="fx-drag">
        <strong>Разборчивость</strong>
        <span class="faint">realtime</span>
        <label class="fx-bypass"><input type="checkbox" data-fx="on" ${s.on ? "checked" : ""} /> вкл</label>
        <label class="fx-bypass" title="Макрос: автоставить пресет диктора на playhead">
          <input type="checkbox" data-fx-act="follow" ${state.followFx ? "checked" : ""} /> за диктором
        </label>
        <button type="button" class="icon-btn" data-fx-act="save-spk">FX → диктор</button>
        <button type="button" class="icon-btn" data-fx-act="tune">Под сегмент</button>
        <button type="button" class="icon-btn fx-cut" data-fx-act="cut">Петля → cuts</button>
        <button type="button" class="icon-btn" data-fx-act="preset">Речь</button>
        <button type="button" class="icon-btn" data-fx-act="reset">Сброс</button>
        <button type="button" class="icon-btn" data-fx-act="close" aria-label="Закрыть">×</button>
      </header>
      <div class="fx-status" id="fx-status" hidden></div>
      <div class="fx-eq" title="16-полосный эквалайзер">${bands}</div>
      <div class="fx-grid">
        <section>
          <h4>НЧ аттенюатор</h4>
          ${fxSlider("lfHz", 40, 400, 5, s.lfHz, "HPF Hz")}
          ${fxSlider("lfDb", -18, 0, 0.5, s.lfDb, "полка dB")}
        </section>
        <section>
          <h4>ВЧ аттенюатор</h4>
          ${fxSlider("hfHz", 4000, 16000, 100, s.hfHz, "LPF Hz")}
          ${fxSlider("hfDb", -18, 0, 0.5, s.hfDb, "полка dB")}
        </section>
        <section class="fx-comp">
          <h4>Компрессор</h4>
          ${fxSlider("thresh", -48, 0, 1, s.thresh, "порог dB")}
          ${fxSlider("ratio", 1, 12, 0.1, s.ratio, "отношение")}
          ${fxSlider("attack", 1, 80, 1, s.attack, "атака мс")}
          ${fxSlider("release", 40, 800, 5, s.release, "восст. мс")}
          ${fxSlider("knee", 0, 40, 1, s.knee, "колено")}
          ${fxSlider("makeup", 0, 18, 0.5, s.makeup, "makeup dB")}
        </section>
        <section class="fx-comp">
          <h4>Нормализация речи</h4>
          <label class="fx-bypass"><input type="checkbox" data-fx="agc" ${s.agc !== false ? "checked" : ""} /> вкл</label>
          ${fxSlider("agcTarget", -30, -12, 1, s.agcTarget ?? -20, "цель dB")}
          ${fxSlider("agcMax", 0, 24, 1, s.agcMax ?? 18, "макс. +dB")}
          ${fxSlider("agcMargin", 6, 18, 1, s.agcMargin ?? 11, "порог речи")}
          <p class="faint" id="agc-live">ждёт сигнал</p>
          <p class="faint">Полоса 250–3800 Гц. Усиление ведётся по речи и держится между словами, шум в паузе не поднимается. Если шум близко к цели, потолок усиления снижается.</p>
        </section>
        <section class="fx-comp">
          <h4>De-echo</h4>
          <label class="fx-bypass"><input type="checkbox" data-fx="deecho" ${s.deecho ? "checked" : ""} /> вкл</label>
          ${fxSlider("deechoAmt", 0, 100, 1, s.deechoAmt ?? 55, "сила")}
          <p class="faint">Фикс. гребёнка (не adaptive). Выкл или сила 0 = dry.</p>
        </section>
        <section class="fx-comp">
          <h4>Адаптивный фильтр</h4>
          <label class="fx-bypass"><input type="checkbox" data-fx="adapt" ${s.adapt !== false ? "checked" : ""} /> вкл</label>
          <p class="faint" id="adapt-live">${s.adapt !== false ? "ждёт сигнал" : "выкл"}</p>
          <p class="faint">Слушает звук после эквалайзера, де-эхо, компрессора и нормализации речи. На ходу двигает свой срез низких, срез высоких и восемь полос. Гул и шипение прибираются, речь в 300–4000 Гц чуть поднимается. Ручные ползунки не меняются.</p>
        </section>
      </div>`;
    bindFxControls(el);
  }

  function bindFxControls(el) {
    el.querySelectorAll("[data-eq]").forEach((inp) => {
      inp.addEventListener("input", () => {
        state.fx.eq[+inp.dataset.eq] = +inp.value;
        applyFxSettings();
        saveFxSettings();
      });
    });
    el.querySelectorAll("[data-fx]").forEach((inp) => {
      inp.addEventListener("input", () => {
        if (inp.type === "checkbox") state.fx[inp.dataset.fx] = inp.checked;
        else state.fx[inp.dataset.fx] = +inp.value;
        const lab = el.querySelector(`[data-fxv="${inp.dataset.fx}"]`);
        if (lab) lab.textContent = inp.type === "checkbox" ? "" : inp.value;
        applyFxSettings();
        saveFxSettings();
      });
      inp.addEventListener("change", () => {
        if (inp.type === "checkbox") {
          state.fx[inp.dataset.fx] = inp.checked;
          applyFxSettings();
          saveFxSettings();
        }
      });
    });
    el.querySelectorAll("[data-fx-act]").forEach((b) =>
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        const act = b.dataset.fxAct;
        if (act === "close") {
          state.showFx = false;
          syncFxPanel();
        } else if (act === "reset") {
          const pos = { x: state.fx.x, y: state.fx.y };
          state.fx = { ...defaultFxSettings(), eq: EQ_HZ.map(() => 0), lfDb: 0, hfDb: 0, makeup: 0, thresh: 0, ratio: 1, agc: false, adapt: false, ...pos };
          state.fx.on = true;
          paintFxPanel();
          applyFxSettings();
          saveFxSettings();
        } else if (act === "preset") {
          const pos = { x: state.fx.x, y: state.fx.y };
          state.fx = { ...defaultFxSettings(), ...pos };
          paintFxPanel();
          applyFxSettings();
          saveFxSettings();
        } else if (act === "tune") {
          startFxTune();
        } else if (act === "cut") {
          exportLoopCut();
        } else if (act === "follow") {
          state.followFx = b.checked;
          if (state.group && state.project) {
            api(`${recBase()}/speakers`, { method: "PATCH", json: { follow_fx: state.followFx } }).catch(() => {});
          }
          if (state.followFx) applySpeakerFx(state.playhead, true);
        } else if (act === "save-spk") {
          saveFxToSpeaker();
        }
      })
    );
    const follow = el.querySelector("[data-fx-act=follow]");
    if (follow)
      follow.addEventListener("change", () => {
        state.followFx = follow.checked;
        if (state.group && state.project) {
          api(`${recBase()}/speakers`, { method: "PATCH", json: { follow_fx: state.followFx } }).catch(() => {});
        }
        if (state.followFx) applySpeakerFx(state.playhead, true);
      });
  }

  async function saveFxToSpeaker() {
    const seg = state.inspect || speakerAtTime(state.playhead);
    const sid = seg && seg.speaker_id;
    if (!sid) {
      setFxStatus("Сначала назначь диктора на сегмент", false);
      return;
    }
    const snap = fxSnapshot();
    try {
      await api(`/api/speakers/${encodeURIComponent(sid)}`, { method: "PATCH", json: { fx: snap } });
      if (state.group && state.project) {
        await api(`${recBase()}/speakers`, { method: "PATCH", json: { speaker_id: sid, fx: snap } });
        state.project.fx_overrides = { ...(state.project.fx_overrides || {}), [sid]: snap };
      }
      const sp = (state.speakers || []).find((x) => x.id === sid);
      if (sp) sp.fx = snap;
      setFxStatus(`FX сохранён для ${sp ? sp.name : sid}`, false);
    } catch (err) {
      setFxStatus(err.message || String(err), false);
    }
  }

  function setFxStatus(text, busy) {
    const el = document.getElementById("fx-float");
    const st = document.getElementById("fx-status");
    if (el) el.classList.toggle("fx-busy", !!busy);
    if (!st) return;
    if (!text) {
      st.hidden = true;
      st.textContent = "";
      return;
    }
    st.hidden = false;
    st.textContent = text;
  }

  function currentTuneSegment() {
    const p = state.project;
    if (!p) return null;
    if (state.inspect && (state.inspect.clip || state.inspect.start_rel != null)) return state.inspect;
    const t = state.playhead || 0;
    const segs = p.segments_detail || [];
    return segs.find((s) => t >= s.start_rel && t < s.end_rel) || (p.spans_detail || []).find((s) => t >= s.start_rel && t < s.end_rel) || segs[0] || null;
  }

  function goertzelPower(data, sr, freq, a, b) {
    const n = b - a;
    if (n < 64) return 1e-12;
    const w = (2 * Math.PI * freq) / sr;
    const coeff = 2 * Math.cos(w);
    let s0 = 0;
    let s1 = 0;
    let s2 = 0;
    for (let i = a; i < b; i++) {
      s0 = data[i] + coeff * s1 - s2;
      s2 = s1;
      s1 = s0;
    }
    const mag2 = s1 * s1 + s2 * s2 - coeff * s1 * s2;
    return mag2 / n;
  }

  function analyzeBufferForSpeech(buf) {
    const sr = buf.sampleRate;
    const ch0 = buf.getChannelData(0);
    let data = ch0;
    if (buf.numberOfChannels > 1) {
      const ch1 = buf.getChannelData(1);
      data = new Float32Array(ch0.length);
      for (let i = 0; i < ch0.length; i++) data[i] = 0.5 * (ch0[i] + ch1[i]);
    }
    const hop = Math.max(256, Math.floor(sr * 0.02));
    const win = hop * 2;
    const nFrames = Math.max(1, Math.floor((data.length - win) / hop));
    const rms = new Float32Array(nFrames);
    let peak = 1e-6;
    for (let f = 0; f < nFrames; f++) {
      const a = f * hop;
      let e = 0;
      for (let i = 0; i < win; i++) {
        const v = data[a + i];
        e += v * v;
        const av = Math.abs(v);
        if (av > peak) peak = av;
      }
      rms[f] = Math.sqrt(e / win);
    }
    const sorted = Array.from(rms).sort((a, b) => a - b);
    const nq = sorted[Math.floor(sorted.length * 0.2)] || 1e-6;
    const sq = sorted[Math.floor(sorted.length * 0.7)] || nq;
    const noisePow = new Array(EQ_HZ.length).fill(1e-12);
    const speechPow = new Array(EQ_HZ.length).fill(1e-12);
    let nN = 0;
    let nS = 0;
    let speechRms = 0;
    for (let f = 0; f < nFrames; f++) {
      const a = f * hop;
      const b = a + win;
      const isNoise = rms[f] <= nq * 1.15;
      const isSpeech = rms[f] >= sq * 0.9;
      if (!isNoise && !isSpeech) continue;
      const dest = isNoise ? noisePow : speechPow;
      if (isNoise) nN += 1;
      else {
        nS += 1;
        speechRms += rms[f];
      }
      for (let i = 0; i < EQ_HZ.length; i++) dest[i] += goertzelPower(data, sr, EQ_HZ[i], a, b);
    }
    if (nN) for (let i = 0; i < EQ_HZ.length; i++) noisePow[i] /= nN;
    if (nS) for (let i = 0; i < EQ_HZ.length; i++) speechPow[i] /= nS;
    speechRms = nS ? speechRms / nS : sorted[sorted.length - 1] || 0.05;
    const snr = EQ_HZ.map((_, i) => 10 * Math.log10((speechPow[i] + 1e-12) / (noisePow[i] + 1e-12)));
    const presence = [2000, 2500, 3150, 4000].map((hz) => {
      const i = EQ_HZ.indexOf(hz);
      return i >= 0 ? snr[i] : 0;
    });
    const pres = presence.reduce((a, b) => a + b, 0) / presence.length;
    const eq = EQ_HZ.map((hz, i) => {
      let g = 0;
      const s = snr[i];
      if (s < pres - 4) g -= Math.min(10, (pres - 4 - s) * 0.55);
      else if (hz >= 1600 && hz <= 4000) g += Math.min(6, 1.5 + (s - pres) * 0.15);
      if (hz <= 125) g -= 2.5 + Math.max(0, (nq / Math.max(sq, 1e-6)) * 4);
      if (hz >= 8000) g -= Math.max(0, 8 - s) * 0.35;
      return Math.round(Math.max(-12, Math.min(8, g)) * 2) / 2;
    });
    const lowNoise = (noisePow[0] + noisePow[1] + noisePow[2]) / 3;
    const midSpeech = (speechPow[8] + speechPow[9] + speechPow[10]) / 3;
    const highNoise = (noisePow[13] + noisePow[14] + noisePow[15]) / 3;
    const lfHz = Math.round(Math.min(280, Math.max(70, 70 + 180 * (lowNoise / (midSpeech + 1e-12)))) / 5) * 5;
    const lfDb = Math.round(Math.max(-16, Math.min(-1, -3 - 10 * Math.log10((lowNoise + 1e-12) / (midSpeech + 1e-12)))) * 2) / 2;
    const hfHz = Math.round(Math.min(12000, Math.max(5000, 11000 - 5000 * (highNoise / (midSpeech + 1e-12)))) / 100) * 100;
    const hfDb = Math.round(Math.max(-14, Math.min(0, -1 - 6 * (highNoise / (midSpeech + 1e-12)))) * 2) / 2;
    const crest = peak / Math.max(speechRms, 1e-4);
    const thresh = Math.round(Math.max(-36, Math.min(-14, -12 - 8 * Math.log10(Math.max(crest, 1.5)))));
    const ratio = Math.round(Math.min(8, Math.max(2.2, 1.8 + crest * 0.55)) * 10) / 10;
    const makeup = Math.round(Math.min(12, Math.max(2, (-thresh * (1 - 1 / ratio)) / 3)) * 2) / 2;
    return {
      on: true,
      eq,
      lfHz,
      lfDb: Math.min(0, lfDb),
      hfHz,
      hfDb: Math.min(0, hfDb),
      thresh,
      ratio,
      attack: crest > 4 ? 8 : 14,
      release: crest > 4 ? 120 : 200,
      knee: 8,
      makeup,
    };
  }

  function readFxSliders() {
    const el = document.getElementById("fx-float");
    if (!el) return;
    el.querySelectorAll("[data-eq]").forEach((inp) => {
      inp.value = String(state.fx.eq[+inp.dataset.eq]);
    });
    el.querySelectorAll("[data-fx]").forEach((inp) => {
      if (inp.type === "checkbox") {
        inp.checked = !!state.fx[inp.dataset.fx];
        return;
      }
      if (state.fx[inp.dataset.fx] == null) return;
      inp.value = String(state.fx[inp.dataset.fx]);
      const lab = el.querySelector(`[data-fxv="${inp.dataset.fx}"]`);
      if (lab) lab.textContent = inp.value;
    });
  }

  function lerp(a, b, t) {
    return a + (b - a) * t;
  }

  function ease(t) {
    return t * t * (3 - 2 * t);
  }

  function animateFxTo(target, ms) {
    const from = {
      eq: state.fx.eq.slice(),
      lfHz: +state.fx.lfHz,
      lfDb: +state.fx.lfDb,
      hfHz: +state.fx.hfHz,
      hfDb: +state.fx.hfDb,
      thresh: +state.fx.thresh,
      ratio: +state.fx.ratio,
      attack: +state.fx.attack,
      release: +state.fx.release,
      knee: +state.fx.knee,
      makeup: +state.fx.makeup,
    };
    const keys = ["lfHz", "lfDb", "hfHz", "hfDb", "thresh", "ratio", "attack", "release", "knee", "makeup"];
    return new Promise((resolve) => {
      const t0 = performance.now();
      const step = (now) => {
        const u = ease(Math.min(1, (now - t0) / ms));
        keys.forEach((k) => {
          state.fx[k] = lerp(from[k], +target[k], u);
        });
        state.fx.eq = from.eq.map((v, i) => lerp(v, +target.eq[i], u));
        state.fx.on = true;
        readFxSliders();
        applyFxSettings();
        if (u < 1 && fx.tuning) requestAnimationFrame(step);
        else {
          keys.forEach((k) => {
            state.fx[k] = +target[k];
          });
          state.fx.eq = target.eq.slice();
          readFxSliders();
          applyFxSettings();
          saveFxSettings();
          resolve();
        }
      };
      requestAnimationFrame(step);
    });
  }

  async function exportLoopCut() {
    if (exportLoopCut.busy) return;
    if (!state.group || !state.project) {
      setFxStatus("Откройте запись в workspace.", false);
      return;
    }
    if (!state.loop || state.loop.b - state.loop.a < 0.15) {
      setFxStatus("Сначала выделите петлю на таймлайне (перетащите по линейке).", false);
      return;
    }
    let start = state.loop.a;
    let end = state.loop.b;
    let source = "original";
    if (state.track === "vad") {
      const ca = origToClean(start);
      const cb = origToClean(end);
      if (ca != null && cb != null && cb - ca >= 0.12) {
        start = ca;
        end = cb;
        source = "cleaned";
      }
    }
    const aLab = absLabelSafe(state.loop.a);
    const bLab = absLabelSafe(state.loop.b);
    const gid = state.group.id;
    exportLoopCut.busy = true;
    const btn = document.querySelector("[data-fx-act=cut]");
    if (btn) btn.disabled = true;
    setFxStatus("Экспорт петли с текущим FX в cuts…", true);
    try {
      const cut = await api(`/api/projects/${encodeURIComponent(gid)}/cuts`, {
        method: "POST",
        json: {
          recording_id: state.project.id,
          start,
          end,
          source,
          name: `${state.project.id} ${aLab}–${bLab}`,
          fx: { ...state.fx, eq: (state.fx.eq || []).slice() },
        },
      });
      setFxStatus(`Сохранено в Cuts: ${cut.name || cut.id}`, false);
      const st = document.getElementById("fx-status");
      if (st) {
        const open = document.createElement("button");
        open.type = "button";
        open.className = "icon-btn";
        open.textContent = "Открыть Cuts";
        open.addEventListener("click", () => {
          location.hash = `#/g/${encodeURIComponent(gid)}/cuts`;
        });
        st.appendChild(open);
      }
    } catch (err) {
      setFxStatus(err.message || String(err), false);
    } finally {
      exportLoopCut.busy = false;
      const b2 = document.querySelector("[data-fx-act=cut]");
      if (b2) b2.disabled = false;
    }
  }

  async function startFxTune() {
    if (fx.tuning) return;
    const seg = currentTuneSegment();
    if (!seg) {
      setFxStatus("Нет текущего сегмента — выберите строку в таблице.", false);
      return;
    }
    fx.tuning = true;
    setFxStatus("Анализ сегмента…", true);
    resumeFx();
    try {
      let buf = null;
      const url = seg.clip && recBase() ? `${recBase()}/clips/${encodeURIComponent(seg.clip)}` : null;
      const ctx = ensureFxGraph();
      if (url && ctx) {
        const r = await fetch(url);
        if (!r.ok) throw new Error("клип недоступен");
        const raw = await r.arrayBuffer();
        buf = await ctx.decodeAudioData(raw.slice(0));
      }
      if (!buf || buf.length < 256) throw new Error("слишком короткий клип");
      setFxStatus("Считаю шум и речь по 16 полосам…", true);
      await new Promise((r) => setTimeout(r, 40));
      const target = analyzeBufferForSpeech(buf);
      target.x = state.fx.x;
      target.y = state.fx.y;
      setFxStatus("Подстройка ползунков под сегмент…", true);
      await animateFxTo(target, 1600);
      setFxStatus("Готово: шум срезан, речь поднята в 2–4 кГц.", false);
      setTimeout(() => {
        if (!fx.tuning) setFxStatus("", false);
      }, 2800);
    } catch (err) {
      setFxStatus(err.message || String(err), false);
    } finally {
      fx.tuning = false;
    }
  }

  function bindFxDrag(el) {
    let drag = null;
    el.addEventListener("pointerdown", (e) => {
      if (!e.target.closest("#fx-drag") || e.target.closest("input,button,label")) return;
      drag = { x: e.clientX - el.offsetLeft, y: e.clientY - el.offsetTop };
      el.setPointerCapture(e.pointerId);
    });
    el.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const x = Math.max(8, Math.min(window.innerWidth - el.offsetWidth - 8, e.clientX - drag.x));
      const y = Math.max(8, Math.min(window.innerHeight - 48, e.clientY - drag.y));
      el.style.left = `${x}px`;
      el.style.top = `${y}px`;
      state.fx.x = x;
      state.fx.y = y;
    });
    const end = () => {
      if (!drag) return;
      drag = null;
      saveFxSettings();
    };
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
  }

  function syncFxPanel() {
    const want = state.view === "workspace" && state.showFx;
    if (!want) {
      const el = document.getElementById("fx-float");
      if (el) el.hidden = true;
      return;
    }
    if (fx.tuning) {
      ensureFxPanel().hidden = false;
      return;
    }
    paintFxPanel();
    ensureFxGraph();
    document.getElementById("fx-float").hidden = false;
  }

  function bindVolumes() {
    const vo = $("#vol-orig");
    const vc = $("#vol-clean");
    if (vo)
      vo.addEventListener("input", () => {
        state.volOrig = +vo.value / 100;
        const n = $("#vol-orig-n");
        if (n) n.textContent = vo.value;
        applyVolumes();
      });
    if (vc)
      vc.addEventListener("input", () => {
        state.volClean = +vc.value / 100;
        const n = $("#vol-clean-n");
        if (n) n.textContent = vc.value;
        applyVolumes();
      });
  }

  function ensureSourcesFor(project, groupId) {
    if (!project || !groupId) return;
    const base = mediaBase(groupId, project.id);
    const o = `${base}/original`;
    const c = `${base}/cleaned`;
    if (project.has_original && orig.dataset.src !== o) {
      orig.src = o;
      orig.dataset.src = o;
    }
    if (project.has_cleaned && clean.dataset.src !== c) {
      clean.src = c;
      clean.dataset.src = c;
    }
  }

  function ensureSources() {
    if (!state.project || !state.group) return;
    if (state.session && state.session.live && !engineOwnsView()) return;
    ensureSourcesFor(state.project, state.group.id);
  }

  function whenReady(el, fn) {
    if (!el) return;
    if (el.readyState >= 1) fn();
    else el.addEventListener("loadedmetadata", fn, { once: true });
  }

  function seekMedia(el, t, gen, then) {
    if (!el || !el.src) {
      if (then) then();
      return;
    }
    const target = Math.max(0, Number(t) || 0);
    const done = () => {
      if (gen !== state.playGen) return;
      if (state.seekLock === gen) state.seekLock = 0;
      if (then) then();
    };
    const attempt = (n) => {
      if (gen !== state.playGen) return;
      let timer = 0;
      const close = () => Math.abs((el.currentTime || 0) - target) < 0.12;
      const finish = () => {
        el.removeEventListener("seeked", onSeeked);
        clearTimeout(timer);
        if (gen !== state.playGen) return;
        if (!close() && n < 5) {
          setTimeout(() => attempt(n + 1), 80);
          return;
        }
        done();
      };
      const onSeeked = () => finish();
      const start = () => {
        if (gen !== state.playGen) return;
        if (close() && el.readyState >= 2) {
          done();
          return;
        }
        el.addEventListener("seeked", onSeeked);
        try {
          el.currentTime = target;
        } catch (_) {
          finish();
          return;
        }
      };
      timer = setTimeout(finish, 900);
      if (el.readyState >= 2) start();
      else el.addEventListener("canplay", start, { once: true });
    };
    state.seekLock = gen;
    whenReady(el, () => attempt(0));
  }

  function togglePlay() {
    if (viewIsPlaying()) stopPlay(true);
    else if (state.project && state.group) playFrom(state.playhead || 0);
  }

  function stopPlay(keepHead) {
    state.playing = false;
    state.seekLock = 0;
    if (state.session) state.session.playing = false;
    orig.pause();
    clean.pause();
    applyRate();
    if (!keepHead) setAudioHead(0);
    const btn = app.querySelector("[data-act=toggle-play]");
    if (btn) btn.textContent = "▶";
    syncMini();
  }

  function adoptSession() {
    if (!state.project || !state.group) return;
    state.session = {
      groupId: state.group.id,
      stem: state.project.id,
      project: state.project,
      fileName: fileNameOf(state.project),
      title: recTitle(state.project),
      track: state.track === "vad" ? "vad" : "full",
      loop: state.loop,
      playhead: state.playhead || 0,
      playing: true,
      live: true,
    };
  }

  function runPlay(t) {
    const rec = audioRec();
    const gid = state.session && state.session.groupId;
    if (!rec || !gid) return;
    const gen = ++state.playGen;
    state.seekLock = gen;
    const d = rec.duration_sec || 0;
    const loop = audioLoop();
    let at = Math.max(0, Math.min(d || t, t));
    if (loop && (at < loop.a || at >= loop.b)) at = loop.a;
    state.playing = true;
    state.session.playing = true;
    state.session.live = true;
    state.scrubbing = false;
    setAudioHead(at);
    ensureSourcesFor(rec, gid);
    applyVolumes();
    applyRate();
    stopHitPreview();
    const btn = app.querySelector("[data-act=toggle-play]");
    if (btn && engineOwnsView()) btn.textContent = "❚❚";
    if (audioTrack() === "vad") startVad(at, gen);
    else startFull(at, gen);
    if (engineOwnsView()) {
      followTranscript(at, true);
      placeCursor();
      drawTimeline();
      drawLoop();
    }
    syncMini();
  }

  function playFrom(t) {
    if (!state.project || !state.group) return;
    adoptSession();
    runPlay(t);
  }

  function startTogether(jobs, gen) {
    if (!jobs.length) return;
    let left = jobs.length;
    const ready = [];
    jobs.forEach((job) => {
      seekMedia(job.el, job.t, gen, () => {
        if (gen !== state.playGen || !state.playing) return;
        ready.push(job.el);
        left -= 1;
        if (left > 0) return;
        applyRate();
        ready.forEach((el) => el.play().catch(() => {}));
      });
    });
  }

  function startFull(t, gen) {
    const rec = audioRec();
    if (!rec) return;
    if (!rec.has_original) {
      startVad(t, gen);
      return;
    }
    orig.pause();
    clean.pause();
    const jobs = [{ el: orig, t }];
    const ct = origToCleanIn(rec, t);
    if (rec.has_cleaned && ct != null) jobs.push({ el: clean, t: ct });
    else if (!clean.paused) clean.pause();
    startTogether(jobs, gen);
  }

  function startVad(t, gen) {
    const rec = audioRec();
    const loop = audioLoop();
    const s = spanAtOrAfterIn(rec, t);
    if (!s) {
      stopPlay(true);
      return;
    }
    const start = Math.max(t, s.start_rel);
    if (loop && start >= loop.b) {
      runPlay(loop.a);
      return;
    }
    setAudioHead(start);
    orig.pause();
    clean.pause();
    const jobs = [];
    if (rec.has_cleaned) jobs.push({ el: clean, t: origToCleanIn(rec, start) ?? 0 });
    if (rec.has_original) jobs.push({ el: orig, t: start });
    if (!jobs.length) stopPlay(true);
    else startTogether(jobs, gen);
  }

  function nudge(el, target) {
    if (!el || !el.src || el.readyState < 2) return;
    const base = baseRate();
    const drift = (el.currentTime || 0) - target;
    const ad = Math.abs(drift);
    if (ad < 0.03) {
      if (Math.abs(el.playbackRate - base) > 0.001) el.playbackRate = base;
      return;
    }
    if (ad < 0.08) {
      el.playbackRate = drift > 0 ? base * 0.97 : base * 1.03;
      return;
    }
    el.playbackRate = base;
    try {
      el.currentTime = Math.max(0, target);
    } catch (_) {
      /* ignore */
    }
  }

  function syncPair(masterT) {
    if (!state.playing || state.seekLock || (engineOwnsView() && state.scrubbing)) return;
    const rec = audioRec();
    if (!rec) return;
    if (audioTrack() === "vad") {
      if (rec.has_original) {
        const want = cleanToOrigIn(rec, clean.currentTime || 0);
        if (orig.paused) {
          try {
            orig.currentTime = want;
          } catch (_) {
            /* ignore */
          }
          orig.play().catch(() => {});
        } else nudge(orig, want);
      }
      return;
    }
    const ct = origToCleanIn(rec, masterT);
    if (ct == null) {
      if (!clean.paused) {
        clean.pause();
        clean.playbackRate = baseRate();
      }
      return;
    }
    if (clean.paused) {
      try {
        clean.currentTime = Math.max(0, ct);
      } catch (_) {
        /* ignore */
      }
      clean.play().catch(() => {});
      return;
    }
    nudge(clean, ct);
  }

  function nextVadSpan() {
    const rec = audioRec();
    const list = spansOf(rec);
    const head = audioHead();
    const loop = audioLoop();
    const cur = spanAtIn(rec, head) || spanAtOrAfterIn(rec, head);
    const after = cur ? cur.end_rel - 0.005 : head;
    let next = list.find((s) => s.start_rel >= after);
    if (loop) {
      next = list.find((s) => s.start_rel >= after && s.start_rel < loop.b && s.end_rel > loop.a);
      if (!next) {
        next = list.find((s) => s.end_rel > loop.a && s.start_rel < loop.b);
        if (next) {
          runPlay(Math.max(next.start_rel, loop.a));
          return;
        }
        stopPlay(true);
        return;
      }
    }
    if (!next) {
      stopPlay(true);
      return;
    }
    runPlay(next.start_rel);
  }

  function maybeLoop(t) {
    const loop = audioLoop();
    if (!loop || !state.playing) return false;
    if (t >= loop.b - 0.02) {
      runPlay(loop.a);
      return true;
    }
    return false;
  }

  function onOrigTime() {
    const rec = audioRec();
    if (!state.playing || !rec || state.seekLock) return;
    if (engineOwnsView() && state.scrubbing) return;
    if (audioTrack() === "vad") {
      if (rec.has_cleaned && !clean.paused) {
        setAudioHead(cleanToOrigIn(rec, clean.currentTime || 0));
      } else if (rec.has_original && !orig.paused) {
        const t = orig.currentTime;
        const s = spanAtIn(rec, t);
        if (!s || t >= (s.end_rel || 0) - 0.05) {
          nextVadSpan();
          return;
        }
        setAudioHead(t);
      }
      if (maybeLoop(audioHead())) return;
      syncPair(audioHead());
      tickPlayhead();
      return;
    }
    if (rec.has_original) {
      setAudioHead(orig.currentTime);
      if (maybeLoop(audioHead())) return;
      syncPair(audioHead());
    }
    tickPlayhead();
  }

  function onOrigEnded() {
    if (!state.playing || (engineOwnsView() && state.scrubbing)) return;
    const rec = audioRec();
    if (audioTrack() === "vad") {
      if (rec && rec.has_cleaned && clean.currentTime < (clean.duration || 0) - 0.15) return;
      nextVadSpan();
      return;
    }
    const loop = audioLoop();
    if (loop) runPlay(loop.a);
    else stopPlay(true);
  }

  function onCleanEnded() {
    if (!state.playing || audioTrack() !== "vad") return;
    const loop = audioLoop();
    if (loop) runPlay(loop.a);
    else stopPlay(true);
  }

  function tickPlayhead() {
    syncMini();
    if (!engineOwnsView()) return;
    const now = $("#clock-now");
    if (now) now.textContent = absLabelSafe(state.playhead);
    const tpos = $("#tpos");
    if (tpos) tpos.textContent = state.playhead.toFixed(2);
    if (state.playing) keepPlayheadInView();
    followTranscript(state.playhead, false);
    applySpeakerFx(state.playhead, false);
    drawTimeline();
    drawLoop();
    placeCursor();
  }

  function lineAt(t) {
    const segs = (state.project && state.project.segments_detail) || [];
    if (!segs.length) return null;
    let cur = null;
    for (let i = 0; i < segs.length; i++) {
      if (segs[i].start_rel <= t + 0.05) cur = segs[i];
      else break;
    }
    return cur || segs[0];
  }

  function scrollRowToTop(row) {
    const wrap = $(".table-wrap");
    if (!wrap || !row) return;
    const head = wrap.querySelector("thead");
    const headH = head ? head.offsetHeight : 0;
    wrap.scrollTop = Math.max(0, row.offsetTop - headH);
  }

  function followTranscript(t, forceTop) {
    const cur = lineAt(t);
    if (!cur) return;
    const wrap = $(".table-wrap");
    if (!wrap) return;
    wrap.querySelectorAll("tr.now").forEach((r) => r.classList.remove("now"));
    const row = wrap.querySelector(`tr[data-n="${cur.n}"]`);
    if (!row) return;
    row.classList.add("now");
    if (!state.txFollow) return;
    const changed = !state.inspect || state.inspect.n !== cur.n;
    state.inspect = cur;
    wrap.querySelectorAll("tr.sel").forEach((r) => r.classList.remove("sel"));
    row.classList.add("sel");
    if (changed || forceTop) scrollRowToTop(row);
  }

  function refreshInspector(s) {
    const box = document.querySelector(".inspector");
    if (!box) return;
    box.innerHTML =
      `<div class="panel-title">Inspector · clip player</div>` +
      (s ? inspectHtml(s) : `<div class="empty">Выберите сегмент</div>`);
    box.querySelectorAll("[data-play]").forEach((b) =>
      b.addEventListener("click", () => playSeg(+b.dataset.play))
    );
    box.querySelectorAll("[data-act]").forEach((b) =>
      b.addEventListener("click", () => onAct(b.dataset.act))
    );
    if (s && s.clip) loadInspWave(s);
  }

  async function loadPeaks(id) {
    const base = recBase();
    if (!base) return;
    try {
      const o = await api(`${base}/wave/original?bins=1400`);
      if (state.project && state.project.id === id) {
        state.peaksOrig = o.peaks || [];
        drawTimeline();
      }
    } catch {
      state.peaksOrig = null;
    }
    try {
      const c = await api(`${base}/wave/cleaned?bins=800`);
      if (state.project && state.project.id === id) state.peaksClean = c.peaks || [];
    } catch {
      state.peaksClean = null;
    }
  }

  function adoptMarkers(groupId, stem, snap) {
    const key = `${groupId}\n${stem}`;
    const same = state.markersFor === key;
    const editedDuring = !!(snap && (markerRev !== snap.rev || markerAck !== snap.ack));
    if (same && (editedDuring || markerRev !== markerAck)) {
      if (state.project) state.project.markers = state.markers.map((m) => ({ ...m }));
      return;
    }
    state.markers = normalizeMarkers(state.project && state.project.markers);
    state.markersFor = key;
    state.markersDirty = false;
    markerAck = markerRev;
  }

  function normalizeMarkers(raw) {
    const src = Array.isArray(raw) ? raw : [];
    const rows = [];
    for (const item of src) {
      if (!item || typeof item !== "object") continue;
      let t = Number(item.t);
      if (!Number.isFinite(t) || t < 0 || t > 48 * 3600) continue;
      t = Math.round(t * 1000) / 1000;
      let name = String(item.name || "")
        .replace(/[\r\n\u0000]/g, " ")
        .replace(/\s+/g, " ")
        .trim();
      if (name.length > 80) name = name.slice(0, 80);
      const row = { t };
      if (name) row.name = name;
      rows.push(row);
    }
    rows.sort((a, b) => a.t - b.t || String(a.name || "").localeCompare(String(b.name || "")));
    const out = [];
    for (const row of rows) {
      const prev = out[out.length - 1];
      if (prev && Math.abs(prev.t - row.t) < 0.01) {
        if (!prev.name && row.name) prev.name = row.name;
        else if (prev.name && row.name && prev.name !== row.name) out.push(row);
        continue;
      }
      out.push(row);
    }
    return out.slice(0, 200);
  }

  function clampMarkerT(t) {
    const d = state.project && Number(state.project.duration_sec);
    const cap = Number.isFinite(d) && d > 0 ? Math.min(d, 48 * 3600) : 0;
    if (!Number.isFinite(t)) t = 0;
    return Math.round(Math.max(0, Math.min(cap, t)) * 1000) / 1000;
  }

  function flashMarker(text) {
    state.markerNote = text || "";
    const el = $("#marker-note");
    if (el) el.textContent = state.markerNote;
  }

  function markerCaption(m, i) {
    const when = relClock(m.t || 0);
    return m.name ? `${m.name} · ${when}` : `${i + 1} · ${when}`;
  }

  function saveMarkers() {
    const base = recBase();
    const key = state.markersFor;
    if (!base || !key) return;
    const rev = markerRev;
    const payload = state.markers.map((m) => ({ ...m }));
    state.markersDirty = true;
    api(`${base}/markers`, { method: "PUT", json: { markers: payload } })
      .then((body) => {
        if (rev !== markerRev || state.markersFor !== key) return;
        markerAck = rev;
        state.markersDirty = false;
        state.markers = normalizeMarkers(body && body.markers);
        if (state.project) state.project.markers = state.markers.map((m) => ({ ...m }));
        flashMarker(state.markers.length ? `Меток: ${state.markers.length}` : "Метки сняты");
        drawTimeline();
      })
      .catch((err) => {
        if (state.markersFor !== key) return;
        flashMarker((err && err.message) || "Не удалось сохранить метки");
      });
  }

  function commitMarkers() {
    if (!state.project || !state.group) return;
    state.markersFor = `${state.group.id}\n${state.project.id}`;
    markerRev += 1;
    state.markers = normalizeMarkers(state.markers);
    state.markersDirty = true;
    state.project.markers = state.markers.map((m) => ({ ...m }));
    drawTimeline();
    saveMarkers();
  }

  function addMarkerAt(t) {
    if (!state.project) return;
    if (state.markers.length >= 200) {
      flashMarker("Не больше 200 меток");
      return;
    }
    t = clampMarkerT(t);
    const hit = state.markers.findIndex((m) => Math.abs(m.t - t) < 0.01);
    if (hit >= 0) {
      flashMarker("Метка уже есть");
      seekCursor(state.markers[hit].t);
      return;
    }
    state.markers.push({ t });
    commitMarkers();
    const made = state.markers.find((m) => Math.abs(m.t - t) < 0.005);
    if (made) flashMarker(markerCaption(made, state.markers.indexOf(made)));
  }

  function removeMarker(i) {
    if (i < 0 || i >= state.markers.length) return;
    state.markers.splice(i, 1);
    commitMarkers();
  }

  function renameMarker(i) {
    const m = state.markers[i];
    if (!m) return;
    const next = window.prompt("Имя метки", m.name || "");
    if (next == null) return;
    const name = String(next)
      .replace(/[\r\n\u0000]/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 80);
    if (name) m.name = name;
    else delete m.name;
    commitMarkers();
  }

  function revealTime(t) {
    if (!state.project) return;
    if (t >= state.viewT0 && t <= state.viewT1) return;
    const span = Math.max(4, state.viewT1 - state.viewT0 || 4);
    const d = state.project.duration_sec || span;
    state.viewT0 = Math.max(0, t - span * 0.35);
    state.viewT1 = Math.min(d, state.viewT0 + span);
    state.viewT0 = Math.max(0, state.viewT1 - span);
  }

  function jumpMarker(dir) {
    const list = state.markers;
    if (!list.length || !state.project) {
      flashMarker("Меток нет");
      return;
    }
    const t = state.playhead || 0;
    const eps = 0.005;
    let i = -1;
    if (dir > 0) {
      i = list.findIndex((m) => m.t > t + eps);
      if (i < 0) i = 0;
    } else {
      for (let k = list.length - 1; k >= 0; k--) {
        if (list[k].t < t - eps) {
          i = k;
          break;
        }
      }
      if (i < 0) i = list.length - 1;
    }
    const m = list[i];
    revealTime(m.t);
    seekCursor(m.t);
    flashMarker(markerCaption(m, i));
  }

  function markerAtX(x, w) {
    let best = -1;
    let bestDx = 10;
    state.markers.forEach((m, i) => {
      const dx = Math.abs(xOf(m.t, w) - x);
      if (dx <= bestDx) {
        bestDx = dx;
        best = i;
      }
    });
    return best;
  }

  function bindMarkerRuler() {
    const canvas = $("#marks");
    if (!canvas) return;
    let drag = null;
    const localX = (e) => {
      const r = canvas.getBoundingClientRect();
      return { x: e.clientX - r.left, w: r.width || 1 };
    };
    canvas.addEventListener("pointerdown", (e) => {
      if (e.button != null && e.button !== 0) return;
      const { x, w } = localX(e);
      const i = markerAtX(x, w);
      drag = {
        i,
        x,
        moved: false,
        t: tOf(x, w),
        t0: i >= 0 ? state.markers[i].t : null,
      };
      try {
        canvas.setPointerCapture(e.pointerId);
      } catch (_) {
        /* synthetic */
      }
    });
    canvas.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const { x, w } = localX(e);
      if (Math.abs(x - drag.x) > 3) drag.moved = true;
      if (drag.i < 0 || !drag.moved) return;
      state.markers[drag.i].t = clampMarkerT(tOf(x, w));
      drawTimeline();
    });
    canvas.addEventListener("pointerup", (e) => {
      if (!drag) return;
      const info = drag;
      drag = null;
      if (info.i < 0) {
        if (!info.moved) addMarkerAt(info.t);
        return;
      }
      if (!info.moved) {
        if (e.shiftKey) renameMarker(info.i);
        else seekCursor(state.markers[info.i].t);
        return;
      }
      state.markers[info.i].t = clampMarkerT(state.markers[info.i].t);
      commitMarkers();
    });
    canvas.addEventListener("pointercancel", () => {
      if (drag && drag.i >= 0 && drag.t0 != null && state.markers[drag.i]) {
        state.markers[drag.i].t = drag.t0;
        drawTimeline();
      }
      drag = null;
    });
    canvas.addEventListener("dblclick", (e) => {
      e.preventDefault();
      const { x, w } = localX(e);
      const i = markerAtX(x, w);
      if (i >= 0) removeMarker(i);
    });
    canvas.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      const { x, w } = localX(e);
      const i = markerAtX(x, w);
      if (i >= 0) removeMarker(i);
    });
  }

  function drawMarkerFlags(ctx, w, h) {
    const list = state.markers || [];
    if (!list.length) return;
    ctx.save();
    ctx.font = "10px IBM Plex Sans, sans-serif";
    ctx.lineWidth = 1.5;
    let lastLabelX = -999;
    list.forEach((m, i) => {
      const x = xOf(m.t, w);
      if (x < -20 || x > w + 20) return;
      ctx.strokeStyle = "#fbbf24";
      ctx.fillStyle = "#fbbf24";
      ctx.beginPath();
      ctx.moveTo(x, 16);
      ctx.lineTo(x, h);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(x, 16);
      ctx.lineTo(x + 8, 21);
      ctx.lineTo(x, 26);
      ctx.closePath();
      ctx.fill();
      if (x - lastLabelX > 36) {
        ctx.fillStyle = "#fde68a";
        ctx.fillText(m.name || String(i + 1), x + 10, 26);
        lastLabelX = x;
      }
    });
    ctx.restore();
  }

  function drawMarkers() {
    const canvas = $("#marks");
    if (!canvas || !state.project) return;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 1;
    const h = 22;
    canvas.width = Math.max(1, Math.floor(w * dpr));
    canvas.height = Math.floor(h * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.font = "10px IBM Plex Sans, sans-serif";
    let lastLabelX = -999;
    (state.markers || []).forEach((m, i) => {
      const x = xOf(m.t, w);
      if (x < -16 || x > w + 16) return;
      ctx.fillStyle = "#fbbf24";
      ctx.fillRect(x - 0.75, 2, 1.5, h - 4);
      ctx.beginPath();
      ctx.moveTo(x, 3);
      ctx.lineTo(x + 8, 8);
      ctx.lineTo(x, 13);
      ctx.closePath();
      ctx.fill();
      if (x - lastLabelX > 36) {
        ctx.fillStyle = "#fde68a";
        ctx.fillText(m.name || String(i + 1), x + 10, 16);
        lastLabelX = x;
      }
    });
  }

  function bindLoopRuler() {
    const box = $("#loop-box");
    const canvas = $("#loop");
    if (!box || !canvas) return;
    let drag = null;
    const pos = (e) => {
      const r = canvas.getBoundingClientRect();
      return tOf(e.clientX - r.left, r.width);
    };
    canvas.addEventListener("pointerdown", (e) => {
      canvas.setPointerCapture(e.pointerId);
      const t = pos(e);
      drag = { a: t, b: t };
    });
    canvas.addEventListener("pointermove", (e) => {
      if (!drag) return;
      drag.b = pos(e);
      const a = Math.min(drag.a, drag.b);
      const b = Math.max(drag.a, drag.b);
      state.loop = b - a > 0.15 ? { a, b } : null;
      if (state.session && engineOwnsView()) state.session.loop = state.loop;
      drawLoop();
    });
    canvas.addEventListener("pointerup", () => {
      if (!drag) return;
      const a = Math.min(drag.a, drag.b);
      const b = Math.max(drag.a, drag.b);
      state.loop = b - a > 0.15 ? { a, b } : null;
      if (state.session && engineOwnsView()) state.session.loop = state.loop;
      drag = null;
      const lab = $("#loop-lab");
      if (lab) lab.textContent = state.loop ? "петля вкл" : "петля выкл";
      drawLoop();
    });
    canvas.addEventListener("dblclick", (e) => {
      e.preventDefault();
      state.loop = null;
      if (state.session && engineOwnsView()) state.session.loop = null;
      const lab = $("#loop-lab");
      if (lab) lab.textContent = "петля выкл";
      drawLoop();
    });
    drawLoop();
  }

  function drawLoop() {
    const canvas = $("#loop");
    if (!canvas || !state.project) return;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 1;
    const h = 28;
    canvas.width = Math.max(1, Math.floor(w * dpr));
    canvas.height = Math.floor(h * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (state.loop) {
      const x = xOf(state.loop.a, w);
      const x2 = xOf(state.loop.b, w);
      ctx.fillStyle = "rgba(245,158,11,0.35)";
      ctx.fillRect(x, 2, Math.max(2, x2 - x), h - 4);
      ctx.strokeStyle = "#f59e0b";
      ctx.strokeRect(x, 2, Math.max(2, x2 - x), h - 4);
    }
    if (state.playhead != null) {
      const x = xOf(state.playhead, w);
      ctx.strokeStyle = "#e2e8f0";
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, h);
      ctx.stroke();
    }
  }

  function xOf(t, w) {
    const span = state.viewT1 - state.viewT0 || 1;
    return ((t - state.viewT0) / span) * w;
  }
  function tOf(x, w) {
    const span = state.viewT1 - state.viewT0 || 1;
    return state.viewT0 + (x / w) * span;
  }

  function zoomAt(frac, factor) {
    const d = state.project.duration_sec || 1;
    const mid = state.viewT0 + (state.viewT1 - state.viewT0) * frac;
    let span = (state.viewT1 - state.viewT0) * factor;
    span = Math.max(4, Math.min(d, span));
    state.viewT0 = Math.max(0, mid - span * frac);
    state.viewT1 = Math.min(d, state.viewT0 + span);
    state.viewT0 = Math.max(0, state.viewT1 - span);
    drawTimeline();
    placeCursor();
  }

  function timeFromClientX(el, clientX) {
    const r = el.getBoundingClientRect();
    return tOf(clientX - r.left, r.width || 1);
  }

  function seekCursor(t, opts) {
    if (!state.project) return;
    const d = state.project.duration_sec || 0;
    t = Math.max(0, Math.min(d, t));
    state.playhead = t;
    const now = $("#clock-now");
    if (now) now.textContent = absLabelSafe(t);
    followTranscript(t, true);
    placeCursor();
    drawTimeline();
    drawLoop();
    if (opts && opts.play === true) playFrom(t);
    else if (opts && opts.play === false) return;
    else if (viewIsPlaying()) playFrom(t);
  }

  function placeCursor() {
    const el = $("#play-cursor");
    const stack = $("#tl-stack");
    const canvas = $("#tl");
    if (!el || !stack || !canvas || !state.project) return;
    const w = canvas.clientWidth || 1;
    const x = xOf(state.playhead || 0, w);
    if (x < -2 || x > w + 2) {
      el.style.opacity = "0";
    } else {
      el.style.opacity = "1";
      el.style.left = `${Math.round(x)}px`;
    }
  }

  function keepPlayheadInView() {
    if (!state.project) return;
    const t = state.playhead || 0;
    const span = state.viewT1 - state.viewT0;
    if (span <= 0) return;
    if (t >= state.viewT0 && t <= state.viewT1) return;
    const d = state.project.duration_sec || span;
    state.viewT0 = Math.max(0, t - span * 0.2);
    state.viewT1 = Math.min(d, state.viewT0 + span);
    state.viewT0 = Math.max(0, state.viewT1 - span);
  }

  function bindTimeline(canvas) {
    let drag = null;
    canvas.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = canvas.getBoundingClientRect();
      const frac = (e.clientX - r.left) / r.width;
      zoomAt(frac, e.deltaY > 0 ? 1.18 : 0.85);
      placeCursor();
    }, { passive: false });
    const applyClick = (e) => {
      const r = canvas.getBoundingClientRect();
      const t = tOf(e.clientX - r.left, r.width);
      const y = e.clientY - r.top;
      if (y > r.height * 0.52) state.track = "vad";
      else state.track = "full";
      return t;
    };
    canvas.addEventListener("pointerdown", (e) => {
      if (e.button != null && e.button !== 0) return;
      const t = applyClick(e);
      drag = {
        x: e.clientX,
        t0: state.viewT0,
        t1: state.viewT1,
        t,
        pan: false,
        wasPlaying: viewIsPlaying(),
      };
      state.scrubbing = true;
      if (viewIsPlaying()) {
        orig.pause();
        clean.pause();
      }
      seekCursor(t, { play: false });
      try {
        canvas.setPointerCapture(e.pointerId);
      } catch (_) {
        /* synthetic / already released */
      }
    });
    canvas.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const w = canvas.getBoundingClientRect().width;
      if (!drag.pan && Math.abs(e.clientX - drag.x) > 8) drag.pan = true;
      if (!drag.pan) {
        seekCursor(timeFromClientX(canvas, e.clientX), { play: false });
        return;
      }
      const dt = ((drag.x - e.clientX) / w) * (drag.t1 - drag.t0);
      const d = state.project.duration_sec || 1;
      let a = drag.t0 + dt;
      let b = drag.t1 + dt;
      if (a < 0) {
        b -= a;
        a = 0;
      }
      if (b > d) {
        a -= b - d;
        b = d;
      }
      state.viewT0 = Math.max(0, a);
      state.viewT1 = Math.min(d, b);
      drawTimeline();
      placeCursor();
    });
    canvas.addEventListener("pointerup", (e) => {
      if (!drag) return;
      const wasPan = drag.pan;
      const wasPlaying = drag.wasPlaying;
      const t = wasPan ? state.playhead : timeFromClientX(canvas, e.clientX);
      drag = null;
      state.scrubbing = false;
      document.querySelectorAll(".lane").forEach((el) => el.classList.remove("selected"));
      const lane = document.getElementById(state.track === "vad" ? "lane-vad" : "lane-full");
      if (lane) lane.closest(".lane")?.classList.add("selected");
      document.querySelectorAll(".track-sw button").forEach((b) => {
        b.classList.toggle("on", b.dataset.act === (state.track === "vad" ? "track-vad" : "track-full"));
      });
      if (wasPan) {
        placeCursor();
        if (wasPlaying) playFrom(state.playhead);
        return;
      }
      seekCursor(t, { play: wasPlaying });
    });
    document.querySelectorAll(".lane-track").forEach((track) => {
      track.addEventListener("click", (e) => {
        if (e.target.closest(".chip-seg")) return;
        const t = timeFromClientX(track, e.clientX);
        if (track.id === "lane-vad") state.track = "vad";
        if (track.id === "lane-full") state.track = "full";
        seekCursor(t, { play: viewIsPlaying() });
      });
    });
    placeCursor();
  }

  function clockHeadHtml(p) {
    const start = esc(p.recording_start_label || "—");
    if (p.clock_source === "anchor" && p.clock_anchor) {
      const a = p.clock_anchor;
      const shift = p.clock_shift_label ? ` · сдвиг ${esc(p.clock_shift_label)}` : "";
      const file = p.filename_start_label ? ` · файл ${esc(p.filename_start_label)}` : "";
      return `<span class="muted">калибровка <span class="mono">${esc(a.rel_label)} = ${esc(a.at_label)}</span>${shift}${file}</span>
        <span class="muted">старт <span class="mono">${start}</span></span>
        <button class="icon-btn" data-act="clear-clock" title="Вернуть время из имени файла">сброс часов</button>`;
    }
    return `<span class="muted">recorder start <span class="mono">${start}</span></span>`;
  }

  function wallParts(rel) {
    const start = state.project && state.project.recording_start;
    let d = null;
    if (start) {
      const base = new Date(start);
      if (!Number.isNaN(base.getTime())) d = new Date(base.getTime() + (rel || 0) * 1000);
    }
    if (!d) d = new Date();
    const pad = (n, w = 2) => String(n).padStart(w, "0");
    return {
      d,
      date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`,
      hms: `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`,
    };
  }

  function wallInputValue(rel) {
    const p = wallParts(rel);
    return `${p.date}T${p.hms}`;
  }

  function absStamp(rel) {
    const start = state.project && state.project.recording_start;
    if (!start) return "нет отметки";
    const base = new Date(start);
    if (Number.isNaN(base.getTime())) return "нет отметки";
    const p = wallParts(rel);
    return `${p.date} ${p.hms}`;
  }

  const CLOCK_SHIFT_MARK = "astrotranscript-clock-shift";

  function modalValues(form) {
    const values = {};
    ((state.modal && state.modal.fields) || []).forEach((f) => {
      const el = form.elements[f.name];
      values[f.name] = el ? el.value : "";
    });
    return values;
  }

  function rememberModalValues(values) {
    if (!state.modal) return;
    (state.modal.fields || []).forEach((f) => {
      if (values[f.name] !== undefined) f.value = values[f.name];
    });
  }

  function clockShiftText(sec) {
    const ms = Math.round(Number(sec) * 1000);
    return `${CLOCK_SHIFT_MARK}\nshift_sec=${(ms / 1000).toFixed(3)}\n`;
  }

  function parseClockShiftText(text) {
    const raw = String(text || "");
    if (!raw.toLowerCase().includes(CLOCK_SHIFT_MARK)) return null;
    const m = raw.match(/shift_sec\s*[:=]\s*([+-]?\d+(?:\.\d+)?)/i);
    if (!m) return null;
    const n = Number(m[1]);
    return Number.isFinite(n) ? n : null;
  }

  function naiveWallMs(text) {
    const m = String(text || "")
      .trim()
      .match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?/);
    if (!m) return null;
    const frac = (m[7] || "").padEnd(3, "0").slice(0, 3);
    return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0), +frac);
  }

  function parseRelClient(value) {
    const text = String(value || "").trim().replace(",", ".");
    if (!text) throw new Error("Укажите точку таймлайна");
    if (/^\d+(?:\.\d+)?$/.test(text)) return Math.max(0, parseFloat(text));
    const m = text.match(/^(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)$/);
    if (!m) throw new Error("Точка таймлайна: секунды или чч:мм:сс");
    const minute = +m[2];
    const sec = parseFloat(m[3]);
    if (minute >= 60 || sec >= 60) throw new Error("Точка таймлайна: секунды или чч:мм:сс");
    return (+m[1] || 0) * 3600 + minute * 60 + sec;
  }

  function formatShiftLabel(sec) {
    const sign = sec >= 0 ? "+" : "−";
    const ms = Math.round(Math.abs(sec) * 1000);
    const total = Math.floor(ms / 1000);
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    const frac = ms % 1000;
    const body = h
      ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
      : `${m}:${String(s).padStart(2, "0")}`;
    return frac ? `${sign}${body}.${String(frac).padStart(3, "0")}` : `${sign}${body}`;
  }

  function savedShiftSec(p) {
    if (!p) return null;
    if (p.clock_shift_sec != null && Number.isFinite(+p.clock_shift_sec)) return +p.clock_shift_sec;
    if (p.filename_start && p.recording_start) {
      const a = naiveWallMs(p.filename_start);
      const b = naiveWallMs(p.recording_start);
      if (a != null && b != null) return (b - a) / 1000;
    }
    return null;
  }

  function shiftFromCorrection(relText, atText) {
    const file = state.project && state.project.filename_start;
    if (!file) throw new Error("У этой записи нет времени в имени файла — смещение не от чего считать");
    const rel = parseRelClient(relText);
    const at = naiveWallMs(atText);
    const base = naiveWallMs(file);
    if (at == null) throw new Error("Укажите дату и время");
    if (base == null) throw new Error("У этой записи нет времени в имени файла — смещение не от чего считать");
    return (at - rel * 1000 - base) / 1000;
  }

  function flashClock(msg) {
    const el = $("#clock-note");
    if (!el) return;
    el.textContent = msg;
    clearTimeout(flashClock.t);
    flashClock.t = setTimeout(() => {
      if (el.isConnected && el.textContent === msg) el.textContent = "";
    }, 2800);
  }

  function showClockError(err) {
    openModal({
      title: "Смещение",
      text: err.message || String(err),
      fields: [],
      submitLabel: "OK",
      onSubmit: async () => {},
    });
  }

  async function writeShiftClipboard(sec) {
    const text = clockShiftText(sec);
    if (!navigator.clipboard || !navigator.clipboard.writeText) {
      throw new Error("Буфер обмена недоступен в этом браузере");
    }
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      throw new Error("Не удалось записать в буфер обмена. Разрешите доступ к буферу.");
    }
    return formatShiftLabel(sec);
  }

  async function readShiftClipboard() {
    if (!navigator.clipboard || !navigator.clipboard.readText) {
      throw new Error("Буфер обмена недоступен в этом браузере");
    }
    let text = "";
    try {
      text = await navigator.clipboard.readText();
    } catch {
      throw new Error("Нет доступа к буферу обмена");
    }
    const sec = parseClockShiftText(text);
    if (sec == null) throw new Error("В буфере нет смещения часов. Сначала нажмите «Копировать смещение».");
    return sec;
  }

  async function copySavedShift() {
    const sec = savedShiftSec(state.project);
    if (sec == null) throw new Error("У этой записи нет времени в имени файла — смещение не от чего считать");
    const label = await writeShiftClipboard(sec);
    flashClock(`Смещение ${label} скопировано`);
  }

  async function copyFormShift(values) {
    const sec = shiftFromCorrection(values.rel, values.at);
    const label = await writeShiftClipboard(sec);
    if (!state.modal) return;
    state.modal.error = "";
    state.modal.note = `Скопировано ${label}. В другой записи нажмите «Вставить смещение».`;
    const note = $("#modal-note");
    if (note) {
      note.hidden = false;
      note.textContent = state.modal.note;
    }
    const err = document.querySelector("#app-modal .err");
    if (err) err.remove();
  }

  async function pasteShiftNow() {
    const sec = await readShiftClipboard();
    if (!state.group || !state.project) throw new Error("Откройте запись");
    if (!state.project.filename_start) {
      throw new Error("У этой записи нет времени в имени файла — смещение не к чему прибавить");
    }
    await api(`${recBase()}/clock-shift`, {
      method: "PUT",
      json: { shift_sec: Math.round(sec * 1000) / 1000 },
    });
    state.modal = null;
    state.keepPlace = true;
    await load();
    flashClock(`Смещение ${formatShiftLabel(sec)} прибавлено к времени из имени файла`);
  }

  function openClockModal(rel) {
    if (!state.project || !state.group) return;
    const point = Number.isFinite(+rel) ? +rel : 0;
    openModal({
      title: "Реальное время точки",
      submitLabel: "Пересчитать",
      text: `Точка ${relClock(point)} сейчас считается как ${absStamp(point)}. Укажите, когда она была на самом деле. Все строки, шкала и экспорт сдвинутся на ту же разницу. Смещения внутри файла не меняются.`,
      fields: [
        { name: "rel", label: "Точка таймлайна (чч:мм:сс)", type: "text", value: relClock(point) },
        { name: "at", label: "Реальные дата и время", type: "datetime-local", value: wallInputValue(point), step: "0.001" },
      ],
      extras: [
        { act: "copy-shift", label: "Копировать смещение" },
        { act: "paste-shift", label: "Вставить смещение" },
      ],
      error: "",
      note: "",
      onSubmit: async (v) => {
        const at = (v.at || "").trim();
        const where = (v.rel || "").trim();
        if (!where) throw new Error("Укажите точку таймлайна");
        if (!at) throw new Error("Укажите дату и время");
        await api(`${recBase()}/clock`, { method: "PUT", json: { rel_sec: where, at } });
        state.keepPlace = true;
      },
    });
  }

  function absLabelSafe(rel) {
    if (!state.project) return relClock(rel || 0);
    return absLabel(rel || 0);
  }

  function absLabel(rel) {
    const start = state.project && state.project.recording_start;
    if (!start) return relClock(rel || 0);
    const d = new Date(start);
    if (Number.isNaN(d.getTime())) return relClock(rel || 0);
    const x = new Date(d.getTime() + (rel || 0) * 1000);
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(x.getHours())}:${pad(x.getMinutes())}:${pad(x.getSeconds())}`;
  }

  /** Scale abs peaks so the loudest bin in the current view fills the lane. */
  function visPeakGain(peaks, i0, i1) {
    let hi = 0;
    const a = Math.max(0, i0 | 0);
    const b = Math.min(peaks.length, i1 | 0);
    for (let i = a; i < b; i++) {
      const v = peaks[i];
      if (v > hi) hi = v;
    }
    return hi > 0 ? 1 / hi : 1;
  }

  function drawTimeline() {
    const canvas = $("#tl");
    if (!canvas || !state.project) return;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth;
    const h = 196;
    canvas.width = Math.max(1, Math.floor(w * dpr));
    canvas.height = Math.floor(h * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#080e18";
    ctx.fillRect(0, 0, w, h);
    const spanList = state.project.spans_detail || [];
    const segs = state.project.segments_detail || [];
    const span = state.viewT1 - state.viewT0 || 1;
    const dur = state.project.duration_sec || 1;

    const yFull0 = 20;
    const yFull1 = 96;
    const yVad0 = 108;
    const yVad1 = 186;

    ctx.fillStyle = state.track === "full" ? "rgba(245,158,11,0.08)" : "#080e18";
    ctx.fillRect(0, yFull0, w, yFull1 - yFull0);
    ctx.fillStyle = state.track === "vad" ? "rgba(34,211,238,0.08)" : "#080e18";
    ctx.fillRect(0, yVad0, w, yVad1 - yVad0);

    const step = niceStep(span);
    ctx.strokeStyle = "#1a2538";
    ctx.fillStyle = "#6b7c94";
    ctx.font = "11px IBM Plex Mono, monospace";
    ctx.beginPath();
    const tStart = Math.floor(state.viewT0 / step) * step;
    for (let t = tStart; t <= state.viewT1; t += step) {
      const x = xOf(t, w);
      ctx.moveTo(x, 16);
      ctx.lineTo(x, h);
      ctx.fillText(absLabel(t), x + 4, 13);
    }
    ctx.stroke();

    ctx.fillStyle = "#94a3b8";
    ctx.font = "10px IBM Plex Sans, sans-serif";
    ctx.fillText("ПОЛНАЯ", 6, yFull0 + 12);
    ctx.fillText("VAD", 6, yVad0 + 12);

    const peaks = state.peaksOrig;
    if (peaks && peaks.length) {
      const i0 = Math.max(0, Math.floor((state.viewT0 / dur) * peaks.length));
      const i1 = Math.min(peaks.length, Math.ceil((state.viewT1 / dur) * peaks.length) + 1);
      const mid = (yFull0 + yFull1) / 2;
      const amp = (yFull1 - yFull0) / 2 - 4;
      const gain = visPeakGain(peaks, i0, i1);
      ctx.fillStyle = "rgba(251,191,36,0.75)";
      for (let i = i0; i < i1; i++) {
        const t = (i / peaks.length) * dur;
        const x = xOf(t, w);
        const x2 = xOf(((i + 1) / peaks.length) * dur, w);
        const bh = Math.max(1, peaks[i] * gain * amp);
        ctx.fillRect(x, mid - bh, Math.max(1, x2 - x), bh * 2);
      }
    }

    ctx.fillStyle = "rgba(34,211,238,0.28)";
    spanList.forEach((s) => {
      const x = xOf(s.start_rel, w);
      const x2 = xOf(s.end_rel, w);
      if (x2 < 0 || x > w) return;
      ctx.fillRect(x, yVad0 + 8, Math.max(1, x2 - x), yVad1 - yVad0 - 16);
    });
    ctx.fillStyle = "rgba(52,211,153,0.5)";
    segs.forEach((s) => {
      const x = xOf(s.start_rel, w);
      const x2 = xOf(s.end_rel, w);
      if (x2 < 0 || x > w) return;
      ctx.fillRect(x, yVad0 + 14, Math.max(1, x2 - x), yVad1 - yVad0 - 28);
    });

    if (state.loop) {
      const x = xOf(state.loop.a, w);
      const x2 = xOf(state.loop.b, w);
      ctx.fillStyle = "rgba(245,158,11,0.08)";
      ctx.fillRect(x, 16, Math.max(2, x2 - x), h - 16);
      ctx.strokeStyle = "rgba(245,158,11,0.7)";
      ctx.setLineDash([4, 3]);
      ctx.strokeRect(x, 16, Math.max(2, x2 - x), h - 16);
      ctx.setLineDash([]);
    }

    if (state.inspect) {
      const x = xOf(state.inspect.start_rel, w);
      const x2 = xOf(state.inspect.end_rel, w);
      ctx.strokeStyle = "#f59e0b";
      ctx.lineWidth = 2;
      ctx.strokeRect(x, yVad0 + 6, Math.max(2, x2 - x), yVad1 - yVad0 - 12);
      ctx.lineWidth = 1;
    }
    drawMarkerFlags(ctx, w, h);
    const anc = state.project.clock_anchor;
    if (anc && Number.isFinite(+anc.rel_sec)) {
      const ax = xOf(+anc.rel_sec, w);
      if (ax >= -8 && ax <= w + 8) {
        ctx.save();
        ctx.strokeStyle = "#f472b6";
        ctx.fillStyle = "#f472b6";
        ctx.lineWidth = 1.5;
        ctx.setLineDash([2, 3]);
        ctx.beginPath();
        ctx.moveTo(ax, 14);
        ctx.lineTo(ax, h);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.font = "10px IBM Plex Sans, sans-serif";
        ctx.fillText("ref", ax + 4, 28);
        ctx.restore();
      }
    }
    const px = xOf(state.playhead || 0, w);
    ctx.save();
    ctx.strokeStyle = "#22d3ee";
    ctx.lineWidth = 2;
    ctx.shadowColor = "rgba(34,211,238,0.9)";
    ctx.shadowBlur = 8;
    ctx.beginPath();
    ctx.moveTo(px, 0);
    ctx.lineTo(px, h);
    ctx.stroke();
    ctx.restore();
    ctx.fillStyle = "#22d3ee";
    ctx.beginPath();
    ctx.moveTo(px, 0);
    ctx.lineTo(px - 6, 10);
    ctx.lineTo(px + 6, 10);
    ctx.closePath();
    ctx.fill();
    const range = $("#tl-range");
    if (range) range.textContent = `${absLabel(state.viewT0)}  →  ${absLabel(state.viewT1)}`;

    const fullBar = [{ start_rel: 0, end_rel: dur, n: 0 }];
    paintLane("lane-full", fullBar, "full");
    paintLane("lane-vad", spanList.map((s) => ({ ...s, n: s.i })), "speech");
    drawMarkers();
    placeCursor();
  }

  function paintLane(id, items, cls) {
    const el = document.getElementById(id);
    if (!el) return;
    const w = el.clientWidth || 1;
    el.innerHTML = items
      .map((s) => {
        const a = s.start_rel;
        const b = s.end_rel;
        const left = (xOf(a, w) / w) * 100;
        const width = Math.max(0.15, ((xOf(b, w) - xOf(a, w)) / w) * 100);
        if (left > 100 || left + width < 0) return "";
        const on = state.inspect && (state.inspect.n === s.n || state.inspect.span_i === s.i);
        return `<i class="chip-seg ${cls} ${on ? "on" : ""}" data-n="${s.n || ""}" style="left:${left}%;width:${width}%"></i>`;
      })
      .join("");
    el.querySelectorAll(".chip-seg").forEach((n) =>
      n.addEventListener("click", () => {
        if (id === "lane-full") {
          state.track = "full";
          seekCursor(state.playhead || 0, { play: state.playing });
          return;
        }
        if (n.dataset.n) pick(+n.dataset.n, true);
      })
    );
  }

  function niceStep(span) {
    const raw = span / 8;
    const pow = Math.pow(10, Math.floor(Math.log10(raw)));
    const n = raw / pow;
    if (n < 1.5) return pow;
    if (n < 3.5) return 2 * pow;
    if (n < 7.5) return 5 * pow;
    return 10 * pow;
  }

  function drawVadSpark() {
    const c = $("#vad-spark");
    if (!c || !state.project) return;
    const dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth || 240;
    const h = 56;
    c.width = Math.floor(w * dpr);
    c.height = Math.floor(h * dpr);
    const ctx = c.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#0b1524";
    ctx.fillRect(0, 0, w, h);
    const dur = state.project.duration_sec || 1;
    const spans = state.project.spans_detail || [];
    ctx.fillStyle = "#22d3ee";
    spans.forEach((s) => {
      const x = (s.start_rel / dur) * w;
      const ww = Math.max(1, ((s.end_rel - s.start_rel) / dur) * w);
      ctx.globalAlpha = 0.7;
      ctx.fillRect(x, 18, ww, 22);
    });
    ctx.globalAlpha = 1;
  }

  async function loadInspWave(s) {
    const canvas = $("#insp-wave");
    if (!canvas || !s.clip) return;
    try {
      const data = await api(
        `${recBase()}/peaks/${encodeURIComponent(s.clip)}?bins=96`
      );
      const peaks = data.peaks || [];
      const dpr = window.devicePixelRatio || 1;
      const w = canvas.clientWidth || 280;
      const h = 48;
      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
      const ctx = canvas.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const bar = w / peaks.length;
      const gain = visPeakGain(peaks, 0, peaks.length);
      peaks.forEach((v, i) => {
        const bh = Math.max(1, v * gain * (h - 4));
        ctx.fillStyle = i / peaks.length < (s.clip_offset || 0) / Math.max(s.dur, 0.01) ? "#334155" : "#22d3ee";
        ctx.fillRect(i * bar, h - bh, Math.max(1, bar - 0.6), bh);
      });
    } catch {
      /* ignore */
    }
  }

  window.__astro = {
    playFrom,
    get s() {
      return {
        playhead: state.playhead,
        playing: state.playing,
        lock: state.seekLock,
        gen: state.playGen,
        track: state.track,
        oT: orig.currentTime,
        cT: clean.currentTime,
      };
    },
  };

  boot();
})();
