/* Music Practice synced player. Loaded once in <head>; wires up every .mp-player that
   appears (Gradio re-renders the HTML after each analysis). No dependencies. */
(function () {
  if (window.__mpPlayer) return;
  window.__mpPlayer = true;

  let actx = null;
  function audioCtx() {
    if (!actx) actx = new (window.AudioContext || window.webkitAudioContext)();
    if (actx.state === "suspended") actx.resume();
    return actx;
  }
  function click(when, accent) {
    const c = audioCtx(), o = c.createOscillator(), g = c.createGain();
    o.frequency.value = accent ? 1600 : 1050;
    g.gain.setValueAtTime(0.0001, when);
    g.gain.exponentialRampToValueAtTime(accent ? 0.6 : 0.4, when + 0.002);
    g.gain.exponentialRampToValueAtTime(0.0001, when + 0.06);
    o.connect(g).connect(c.destination);
    o.start(when); o.stop(when + 0.08);
    return o;
  }
  const fmt = (t) => { t = Math.max(0, t); return Math.floor(t / 60) + ":" + String(Math.floor(t % 60)).padStart(2, "0"); };
  /** index of the last element <= t, or -1 */
  function lastLE(arr, t, key) {
    let lo = 0, hi = arr.length - 1, r = -1;
    while (lo <= hi) {
      const m = (lo + hi) >> 1, v = key ? arr[m][key] : arr[m];
      if (v <= t + 1e-4) { r = m; lo = m + 1; } else hi = m - 1;
    }
    return r;
  }

  function init(root) {
    root.dataset.ready = "1";
    // Gradio re-renders by patching the existing DOM, so a new analysis can land on the very
    // same elements a previous player instance is still driving. One instance per element:
    // tear the old one down completely (audio, animation loop, every listener).
    if (root.__mpDestroy) root.__mpDestroy();
    const ac = new AbortController(), sig = { signal: ac.signal };
    let alive = true;
    root.__mpKey = root.dataset.key;
    const D = JSON.parse(root.dataset.mp);
    const $ = (s) => root.querySelector(s);
    const audio = $(".mp-audio"), playBtn = $(".mp-play"), srcSel = $(".mp-src");
    const speed = $(".mp-speed"), speedOut = $(".mp-speed-out");
    const loopOn = $(".mp-loop-on"), fromIn = $(".mp-from"), toIn = $(".mp-to"), secSel = $(".mp-sec-sel");
    const countIn = $(".mp-countin"), clickOn = $(".mp-click"), follow = $(".mp-follow");
    const nowEl = $(".mp-chord-now"), nextEl = $(".mp-chord-next"), nextIn = $(".mp-next-in");
    const barNow = $(".mp-bar-now"), timeEl = $(".mp-time"), head = $(".mp-head");
    const sheet = $(".mp-sheet"), barEls = [...root.querySelectorAll(".mp-bar")];
    const secEls = [...root.querySelectorAll(".mp-sec")];
    const bars = D.bars, beats = D.beats, chords = (D.chords || []).filter((c) => c.label !== "N");
    const barSet = new Set(bars.map((b) => b.toFixed(3)));
    const nBars = bars.length;
    let curBar = -2, lastT = -0.002, counting = false, rate = 1;

    // ---------- drum kit ----------
    const K = D.drums;
    const kitEls = {}, hitT = [], hitP = [], hitV = [], perPiece = {}, soonNow = {};
    if (K) {
      root.querySelectorAll(".mp-pc").forEach((g) => { kitEls[g.dataset.p] = g; });
      K.hits.forEach(([t, p, v]) => {
        hitT.push(t); hitP.push(K.pieces[p]); hitV.push(v);
        (perPiece[K.pieces[p]] = perPiece[K.pieces[p]] || []).push(t);
      });
    }
    function flash(piece, vel) {
      const g = kitEls[piece]; if (!g) return;
      g.style.setProperty("--vel", (0.55 + 0.45 * vel).toFixed(2));
      g.classList.remove("is-hit"); void g.getBBox(); g.classList.add("is-hit");
      g.dataset.hits = String((parseInt(g.dataset.hits || "0", 10)) + 1);  // for tests
    }
    // grid marks by hit index (a mark can hold several hits that quantize to the same 16th)
    const markOf = [];
    root.querySelectorAll(".mp-row i[data-h]").forEach((m) => {
      m.dataset.h.split(",").forEach((k) => { markOf[+k] = m; });
    });
    function pop(el, vel) {               // restart the CSS "impact" animation on an element
      el.style.setProperty("--vel", (0.55 + 0.45 * vel).toFixed(2));
      el.classList.remove("is-hit"); void el.offsetWidth; el.classList.add("is-hit");
    }
    function kitHits(from, to) {           // flash every hit in (from, to]: kit piece + its grid mark
      for (let i = lastLE(hitT, from) + 1; i < hitT.length && hitT[i] <= to; i++) {
        flash(hitP[i], hitV[i]);
        const m = markOf[i];
        if (m) { pop(m, hitV[i]); m.dataset.pops = String((parseInt(m.dataset.pops || "0", 10)) + 1); }
        if (STEPS) STEPS.hit(i, hitV[i]);
      }
    }
    function kitAnticipate(t) {
      const win = 60 / D.tempo;            // one beat of song time
      for (const piece in kitEls) {
        const ts = perPiece[piece]; let v = 0;
        if (ts) {
          const j = lastLE(ts, t) + 1;
          if (j < ts.length) { const dt = ts[j] - t; if (dt < win) v = 1 - dt / win; }
        }
        const r = Math.round(v * 20) / 20;
        if (soonNow[piece] !== r) { soonNow[piece] = r; kitEls[piece].style.setProperty("--soon", r); }
      }
    }
    root.mpKit = { hitCount: (p) => parseInt((kitEls[p] && kitEls[p].dataset.hits) || "0", 10) };  // tests
    const HW = K && window.MPHighway ? window.MPHighway({
      root, D, audio, sig, lastLE, audioCtx, click,
      getRate: () => rate, isAlive: () => alive, flash: (p, v) => flash(p, v), toggle: () => toggle(),
    }) : null;
    root.__mpHW = HW;  // tests
    let STEPS = null;    // the "Steps" view (set up further down)
    // Saving a drum fix re-renders the whole player; remember where we were so the new one
    // picks up at the same spot (position, loop, speed, panel open).
    let showGrid = null;
    function rememberSpot() {
      if (counting) cancelCountIn();
      audio.pause();
      window.__mpResume = { t: audio.currentTime, loop: loopOn.checked, from: fromIn.value,
        to: toIn.value, speed: speed.value, fix: true, y: window.scrollY };
    }
    const FIX = K && window.MPDrumEdit ? window.MPDrumEdit({
      root, D, sig, onSave: rememberSpot, onToggle: (v) => { if (v && showGrid) showGrid(); },
    }) : null;
    root.__mpFix = FIX;  // tests

    const barEnd = (i) => (i + 1 < nBars ? bars[i + 1] : D.duration);
    // Bar lines come from beat tracking and can land ~20 ms after the real downbeat.
    // Loops and jumps start a little early so the first hit's attack isn't cut off.
    const PREROLL = 0.04;
    const barStart = (i) => Math.max(bars[i] - PREROLL, 0);
    const clampBar = (v) => Math.min(Math.max(parseInt(v || "1", 10) || 1, 1), nBars);
    function loopRange() {
      let a = clampBar(fromIn.value), b = clampBar(toIn.value);
      if (b < a) [a, b] = [b, a];
      return [barStart(a - 1), Math.max(barEnd(b - 1) - PREROLL, barStart(a - 1) + 0.1), a, b];
    }
    root.mpLoopRange = loopRange;  // exposed for tests

    // ---------- sources ----------
    function setSource(id, keepTime) {
      const s = D.sources.find((x) => x.id === id) || D.sources[0];
      if (!s) return;
      const t = keepTime ? audio.currentTime : 0, wasPlaying = !audio.paused;
      audio.src = s.url;
      audio.addEventListener("loadedmetadata", function once() {
        audio.removeEventListener("loadedmetadata", once);
        audio.currentTime = t;
        applyRate();
        if (wasPlaying) audio.play();
      }, sig);
    }
    // ---------- live mixer: one audio element per instrument stem ----------
    // The page's <audio> is the master (drives time, loop, highlight); one follower per other
    // stem plays alongside. Followers are kept in step by nudging their speed a few percent
    // (inaudible) and, if they ever fall far behind, by a hard re-seek.
    const M = D.stems || null;
    const strips = [], followers = [];
    let resumeT = null;          // where to land once the master track has loaded (after a save)
    // Every stem is downloaded whole into memory first. Streaming them instead keeps one
    // connection open per stem, and the browser allows only ~6 per server: the last stem
    // would never load. In memory, seeks are also instant, which keeps the stems in step.
    const blobUrls = [];
    if (M) {
      const status = root.querySelector(".mp-mix-status");
      let loaded = 0;
      M.forEach((st, k) => {
        const el = k === 0 ? audio : new Audio();
        if (k) { el.preload = "auto"; followers.push(el); }
        fetch(st.url, { signal: ac.signal }).then((r) => r.blob()).then((b) => {
          if (!alive) return;
          const u = URL.createObjectURL(b); blobUrls.push(u);
          const t = audio.currentTime;
          el.src = u;
          el.addEventListener("loadedmetadata", () => {
            if (el !== audio) el.currentTime = t;
            else if (resumeT !== null) { audio.currentTime = resumeT; lastT = resumeT - 0.002; resumeT = null; render(true); }
            applyRate();
          }, { once: true, signal: ac.signal });
          loaded++;
          if (status) status.textContent = loaded < M.length ? `(loading tracks ${loaded}/${M.length})` : "";
          if (loaded === M.length) root.dataset.stemsReady = "1";
        }).catch(() => { if (status && alive) status.textContent = "(a track failed to load)"; });
        const box = root.querySelector(`.mp-strip[data-stem="${st.name}"]`);
        strips.push({ name: st.name, el, box,
          range: box.querySelector('input[type="range"]'), val: box.querySelector(".mp-strip-val"),
          mute: box.querySelector(".mp-m"), solo: box.querySelector(".mp-s") });
      });
    }
    const pressed = (b) => b.getAttribute("aria-pressed") === "true";
    function applyGains() {
      const anySolo = strips.some((x) => pressed(x.solo));
      strips.forEach((x) => {
        const silent = pressed(x.mute) || (anySolo && !pressed(x.solo));
        x.el.volume = silent ? 0 : x.range.value / 100;
        x.box.classList.toggle("is-silent", x.el.volume === 0);
        x.box.classList.toggle("is-solo", pressed(x.solo));
      });
      root.dataset.gains = JSON.stringify(Object.fromEntries(strips.map((x) => [x.name, +x.el.volume.toFixed(2)])));
    }
    function preset(id) {
      if (id === "custom") return;
      strips.forEach((x) => {
        x.mute.setAttribute("aria-pressed", "false"); x.solo.setAttribute("aria-pressed", "false");
        const mine = x.name === D.mine;
        x.range.value = id === "along" ? (mine ? 0 : 100) : id === "solo" ? (mine ? 100 : 0) : 100;
      });
      applyGains();
    }
    function toCustom() { if (srcSel) srcSel.value = "custom"; applyGains(); }
    strips.forEach((x) => {
      x.range.addEventListener("input", toCustom, sig);
      [x.mute, x.solo].forEach((b) => b.addEventListener("click", () => {
        b.setAttribute("aria-pressed", pressed(b) ? "false" : "true"); toCustom();
      }, sig));
    });
    // A seek on a follower takes time (a new range request to the local server), during which
    // it doesn't advance while the master does. So: never re-seek a follower that is already
    // in place, and when a seek is unavoidable, aim ahead by the measured seek latency.
    let seekLag = 0.12;                                   // seconds, learned from real seeks
    function seekFollower(f, t, ahead) {
      f.__seekAt = performance.now();
      f.currentTime = t + (ahead ? seekLag * rate : 0);
    }
    if (M) {
      followers.forEach((f) => f.addEventListener("seeked", () => {
        if (f.__seekAt) { seekLag = 0.7 * seekLag + 0.3 * Math.min(0.6, (performance.now() - f.__seekAt) / 1000); f.__seekAt = 0; }
      }, sig));
      audio.addEventListener("play", () => followers.forEach((f) => {
        if (Math.abs(f.currentTime - audio.currentTime) > 0.04) seekFollower(f, audio.currentTime, false);
        f.play().catch(() => {});
      }), sig);
      audio.addEventListener("pause", () => followers.forEach((f) => f.pause()), sig);
      // master jumped (loop, bar click...): followers jump too; while paused, to the exact spot
      audio.addEventListener("seeking", () => followers.forEach((f) => seekFollower(f, audio.currentTime, !audio.paused)), sig);
    }
    root.__mpFollowers = followers;   // tests
    let lastSync = 0;
    function syncFollowers(now) {
      if (!followers.length || audio.paused || now - lastSync < 120) return;
      lastSync = now;
      const t = audio.currentTime; let worst = 0;
      followers.forEach((f) => {
        if (f.paused) f.play().catch(() => {});
        if (f.seeking) return;                            // mid-seek: judge it once it lands
        const d = t - f.currentTime;                      // > 0: follower is behind
        worst = Math.max(worst, Math.abs(d));
        if (Math.abs(d) > 0.35) { seekFollower(f, t, true); f.playbackRate = rate; }
        else if (Math.abs(d) > 0.008) f.playbackRate = rate * (1 + Math.max(-0.1, Math.min(0.1, d * 2)));
        else if (f.playbackRate !== rate) f.playbackRate = rate;
      });
      root.dataset.drift = worst.toFixed(4);                // for tests
      const mx = parseFloat(root.dataset.driftMax || "0");
      if (worst > mx && t > 1.5) root.dataset.driftMax = worst.toFixed(4);
    }
    srcSel && srcSel.addEventListener("change", () => (M ? preset(srcSel.value) : setSource(srcSel.value, true)), sig);

    // ---------- speed ----------
    function applyRate() {
      [audio, ...followers].forEach((el) => {
        el.preservesPitch = true; el.mozPreservesPitch = true; el.webkitPreservesPitch = true;
        el.defaultPlaybackRate = rate; el.playbackRate = rate;
      });
    }
    function onSpeed() {
      rate = parseInt(speed.value, 10) / 100;
      speedOut.textContent = "% (" + Math.round(D.tempo * rate) + " BPM)";
      applyRate();
    }
    speed.addEventListener("input", onSpeed, sig);

    // ---------- play / count-in ----------
    // Count-in only when starting on a bar line (song start, a jump, a section, a loop
    // repeat). Resuming from a pause mid-bar continues right away from the same spot.
    function atBarStart() {
      const t = audio.currentTime;
      if (t < 0.06) return true;
      const i = lastLE(bars, t + PREROLL + 0.01);
      return i >= 0 && Math.abs(t - barStart(i)) < 0.06;
    }
    let countTimer = null;
    function cancelCountIn() {
      if (countTimer) clearTimeout(countTimer);
      countTimer = null; counting = false; root.classList.remove("is-counting");
      if (countOsc) { countOsc.forEach((o) => { try { o.stop(); } catch (e) {} }); countOsc = null; }
    }
    let countOsc = null;
    function startPlayback() {
      audioCtx();
      if (loopOn.checked) {
        const [a, b] = loopRange();
        if (audio.currentTime < a - 0.05 || audio.currentTime >= b - 0.05) audio.currentTime = a;
      }
      if (countIn.checked && atBarStart()) doCountIn(() => audio.play()); else audio.play();
    }
    function doCountIn(then) {
      counting = true; root.classList.add("is-counting");
      const c = audioCtx(), beat = 60 / (D.tempo * rate), t0 = c.currentTime + 0.05;
      countOsc = [];
      for (let i = 0; i < 4; i++) countOsc.push(click(t0 + i * beat, i === 0));
      countTimer = setTimeout(() => { countTimer = null; countOsc = null; counting = false;
        root.classList.remove("is-counting"); then(); }, (0.05 + 4 * beat) * 1000);
    }
    // One button, three states: stopped -> play; counting in -> cancel; playing -> pause.
    function toggle() {
      if (counting) { cancelCountIn(); return; }
      if (audio.paused) startPlayback(); else audio.pause();
    }
    root.mpToggle = toggle;  // tests
    playBtn.addEventListener("click", toggle, sig);
    audio.addEventListener("play", () => root.classList.add("is-playing"), sig);
    audio.addEventListener("pause", () => root.classList.remove("is-playing"), sig);
    audio.addEventListener("ended", () => { if (loopOn.checked) wrap(); }, sig);
    // after any jump, hits exactly at the new position (a loop's downbeat) must still fire
    audio.addEventListener("seeked", () => { lastT = audio.currentTime - 0.002; }, sig);

    function wrap() {
      const [a] = loopRange();
      audio.pause(); audio.currentTime = a; lastT = a - 0.002;
      if (countIn.checked) doCountIn(() => audio.play()); else audio.play();
    }

    // ---------- loop range ----------
    function setRange(a, b, seek) {
      fromIn.value = a; toIn.value = b; markRange();
      if (seek) { audio.currentTime = barStart(a - 1); lastT = audio.currentTime - 0.002; }
    }
    function markRange() {
      const [, , a, b] = loopRange();
      barEls.forEach((el, i) => el.classList.toggle("in-loop", loopOn.checked && i + 1 >= a && i + 1 <= b));
    }
    [fromIn, toIn, loopOn].forEach((el) => el.addEventListener("change", markRange), sig);
    function pickSection(k, seek) {
      const s = D.sections[k]; if (!s) return;
      setRange(s.start_bar, s.end_bar, seek);
      secSel.value = String(k);
      secEls.forEach((el, j) => el.classList.toggle("is-picked", j === k));
    }
    secSel.addEventListener("change", () => { if (secSel.value !== "") pickSection(parseInt(secSel.value, 10), true); }, sig);
    secEls.forEach((el) => el.addEventListener("click", () => { loopOn.checked = true; pickSection(parseInt(el.dataset.k, 10), true); }, sig));
    barEls.forEach((el) => el.addEventListener("click", () => {
      audio.currentTime = barStart(parseInt(el.dataset.i, 10)); lastT = audio.currentTime - 0.002; render(true);
    }, sig));

    // ---------- repeated patterns: show where each one is played, loop the typical bar ----------
    const bandEls = [...root.querySelectorAll(".mp-patband i")];
    const patShows = [...root.querySelectorAll(".mp-pat-show")];
    function focusPattern(k) {          // k: pattern index as a string, or null to clear
      if (k === null) delete root.dataset.patFocus; else root.dataset.patFocus = k;
      patShows.forEach((b) => b.setAttribute("aria-pressed", String(b.parentElement.dataset.pat === k)));
      barEls.forEach((el) => el.classList.toggle("is-pat", k !== null && el.dataset.pat === k));
      bandEls.forEach((el) => el.classList.toggle("is-pat", k !== null && el.dataset.pat === k));
    }
    patShows.forEach((b) => b.addEventListener("click", () => {
      const k = b.parentElement.dataset.pat;
      focusPattern(root.dataset.patFocus === k ? null : k);
    }, sig));
    root.querySelectorAll(".mp-pat-loop").forEach((b) => b.addEventListener("click", () => {
      const bar = parseInt(b.dataset.bar, 10) + 1;
      loopOn.checked = true; secSel.value = ""; secEls.forEach((el) => el.classList.remove("is-picked"));
      setRange(bar, bar, true); render(true);
    }, sig));
    bandEls.forEach((el) => el.addEventListener("click", () => {
      audio.currentTime = barStart(parseInt(el.dataset.i, 10)); lastT = audio.currentTime - 0.002; render(true);
    }, sig));

    // ---------- beat figures: show every beat that plays the same one-beat figure ----------
    const figChips = [...root.querySelectorAll(".mp-fig-chip")];
    const figEls = [...root.querySelectorAll(".mp-sheet .mp-fig")];
    function focusFigure(k) {          // k: figure index as a string, or null to clear
      if (k === null) delete root.dataset.figFocus; else root.dataset.figFocus = k;
      figChips.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.f === k)));
      figEls.forEach((el) => el.classList.toggle("is-fig", k !== null && el.dataset.f === k));
    }
    figChips.forEach((b) => b.addEventListener("click", () => {
      focusFigure(root.dataset.figFocus === b.dataset.f ? null : b.dataset.f);
    }, sig));

    // ---------- steps view: the song as a few steps that repeat ----------
    // Shows one bar: the groove of the current step, drawn once. Every time the groove comes
    // round again the playhead sweeps back to the start of the SAME bar and a counter ticks
    // ("3 of 8"), so a long song feels like a handful of things done several times.
    const SD = K && D.drums.steps, stepsEl = $(".mp-steps");
    if (SD && stepsEl) {
      const q = (s) => stepsEl.querySelector(s);
      const stage = q(".mp-steps-stage"), noEl = q(".mp-steps-no"), nameEl = q(".mp-steps-name");
      const repEl = q(".mp-steps-rep"), dotsEl = q(".mp-steps-dots"), passEl = q(".mp-steps-pass");
      const nextEl = q(".mp-steps-next"), list = q(".mp-steps-list");
      const chips = [...stepsEl.querySelectorAll(".mp-step")];
      const barStep = new Array(nBars).fill(-1);
      SD.steps.forEach((st, k) => { for (let b = st.a; b <= st.b; b++) barStep[b] = k; });
      const markPos = [];          // hit index -> [row, slot] inside its bar's grid
      markOf.forEach((m, k) => {
        if (!m) return;
        const row = m.parentElement, rowsOf = [...row.parentElement.querySelectorAll(":scope > .mp-row")];
        markPos[k] = [rowsOf.indexOf(row), [...row.children].indexOf(m)];
      });
      const isGroove = (b) => SD.ids[b] >= 0 && !SD.pats[SD.ids[b]].fill;
      const srcBar = (b) => (isGroove(b) ? SD.pats[SD.ids[b]].rep : b);
      let visible = false, shown = -1, atBar = -1, cloneRows = [], cloneCur = null;
      function replay(cls) { stage.classList.remove("is-again", "is-new"); void stage.offsetWidth; stage.classList.add(cls); }
      function showBar(src, again) {
        if (src !== shown) {
          const c = barEls[src].cloneNode(true);
          c.classList.add("is-now", "is-line-start", "is-line-end");
          c.classList.remove("in-loop", "is-pat");
          c.removeAttribute("title");
          c.querySelectorAll(".is-hit").forEach((e) => e.classList.remove("is-hit"));
          stage.replaceChildren(c);
          cloneRows = [...c.querySelectorAll(".mp-row")]; cloneCur = c.querySelector(".mp-cursor");
          shown = src; replay("is-new");
        } else if (again) replay("is-again");
      }
      function texts(i) {
        const k = barStep[i];
        if (k < 0) { noEl.textContent = ""; nameEl.textContent = "Rest"; repEl.textContent = ""; dotsEl.replaceChildren(); passEl.textContent = ""; nextEl.textContent = ""; return; }
        const st = SD.steps[k], off = i - st.a, ph = st.phrase, pass = Math.floor(off / ph), j = off % ph;
        const pk = SD.ids[i], groove = st.p >= 0 && pk === st.p;
        let r = 0;
        for (let b = st.a + pass * ph; b <= i; b++) if (SD.ids[b] === st.p) r++;
        noEl.textContent = `Step ${k + 1} of ${SD.steps.length}`;
        nameEl.textContent = groove ? SD.pats[st.p].name
          : pk >= 0 ? `${SD.pats[pk].name}${st.p >= 0 ? ": closing the phrase" : ""}`
          : pk === -2 ? (st.p >= 0 ? "Fill: closing the phrase" : "Played once") : "Rest";
        repEl.textContent = groove ? `${r} of ${st.reps}` : "";
        if (groove && r > 1) repEl.classList.remove("is-bump"), void repEl.offsetWidth, repEl.classList.add("is-bump");
        const dots = [];
        for (let b = 0; b < ph; b++) {
          const d = document.createElement("i"), bar = st.a + pass * ph + b;
          d.className = (SD.ids[bar] === st.p ? "" : "is-fill ") + (b < j ? "is-done" : b === j ? "is-now" : "");
          dots.push(d);
        }
        dotsEl.replaceChildren(...dots);
        passEl.textContent = st.times > 1 ? `Pass ${pass + 1} of ${st.times}` : "";
        const lastBar = j === ph - 1;
        nextEl.textContent = !lastBar ? "" : pass < st.times - 1 ? "Next: the same step again"
          : k + 1 < SD.steps.length ? `Next: step ${k + 2}, ${SD.steps[k + 1].name}` : "Last bar of the song";
        stage.style.setProperty("--pc", st.p >= 0 ? SD.pats[st.p].color : "#9aa3ae");
        chips.forEach((c, n) => { c.classList.toggle("is-now", n === k); c.classList.toggle("is-done", n < k); });
        const c = chips[k];
        if (c) {                                   // keep the current step visible in the strip
          const L = c.offsetLeft - list.offsetLeft, R = L + c.offsetWidth;
          if (L < list.scrollLeft || R > list.scrollLeft + list.clientWidth) list.scrollLeft = Math.max(0, L - 40);
        }
      }
      function update(t, i) {
        if (!visible) return;
        if (i !== atBar) {
          const prev = atBar; atBar = i;
          showBar(srcBar(i), prev >= 0 && i === prev + 1 && srcBar(prev) === srcBar(i));
          texts(i);
        }
        if (cloneCur) cloneCur.style.left = (cursorFrac(i, t) * 100).toFixed(2) + "%";
      }
      function hit(k, vel) {
        if (!visible) return;
        const pos = markPos[k], row = pos && cloneRows[pos[0]], cell = row && row.children[pos[1]];
        if (cell && (cell.classList.contains("mp-o") || cell.classList.contains("mp-x"))) pop(cell, vel);
      }
      chips.forEach((c) => c.addEventListener("click", () => {
        const st = SD.steps[parseInt(c.dataset.s, 10)];
        audio.currentTime = barStart(st.a); lastT = audio.currentTime - 0.002; render(true);
      }, sig));
      STEPS = {
        show(v) { visible = v; stepsEl.hidden = !v; atBar = -1; shown = -1; if (v) render(true); },
        update, hit, state: () => ({ shown, atBar, rep: repEl.textContent, step: noEl.textContent }),  // tests
      };
      root.__mpSteps = STEPS;
    }

    // ---------- keyboard ----------
    function onKey(e) {
      if (!alive) return;
      const tag = (e.target.tagName || "").toLowerCase();
      if (["input", "select", "textarea"].includes(tag) && e.target.type !== "range" && e.target.type !== "checkbox") return;
      if (e.code === "Space") { e.preventDefault(); toggle(); }
      else if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault();
        const i = Math.min(Math.max(lastLE(bars, audio.currentTime + PREROLL + 0.001) + (e.key === "ArrowRight" ? 1 : -1), 0), nBars - 1);
        audio.currentTime = barStart(i); lastT = barStart(i) - 0.002; render(true);
      } else if (e.key === "l" || e.key === "L") { loopOn.checked = !loopOn.checked; markRange(); }
    }
    document.addEventListener("keydown", onKey, sig);

    // ---------- typed values: every slider has a number box next to it ----------
    // Typing applies on Enter or when leaving the box (not on every keystroke, or typing "85"
    // would pass through 8%). Out-of-range values are clamped to the slider's limits.
    const numPairs = [];
    root.querySelectorAll("input.mp-num").forEach((num) => {
      const range = num.parentElement.querySelector('input[type="range"]');
      if (!range) return;
      numPairs.push([range, num]);
      num.value = range.value;
      const apply = () => {
        let v = parseFloat(String(num.value).replace(",", "."));
        if (!isFinite(v)) { num.classList.add("is-bad"); return; }
        num.classList.remove("is-bad");
        const lo = parseFloat(range.min), hi = parseFloat(range.max), st = parseFloat(range.step) || 1;
        v = Math.min(hi, Math.max(lo, Math.round(v / st) * st));
        num.value = String(v);
        if (range.value !== String(v)) {
          range.value = String(v);
          range.dispatchEvent(new Event("input", { bubbles: true }));
          range.dispatchEvent(new Event("change", { bubbles: true }));
        }
      };
      num.addEventListener("change", apply, sig);
      num.addEventListener("keydown", (e) => {
        e.stopPropagation();                              // no player shortcuts while typing
        if (e.key === "Enter") { e.preventDefault(); apply(); num.select(); }
        else if (e.key === "Escape") { num.value = range.value; num.classList.remove("is-bad"); num.blur(); }
      }, sig);
      num.addEventListener("focus", () => num.select(), sig);
      range.addEventListener("input", () => { if (document.activeElement !== num) num.value = range.value; }, sig);
    });
    function syncNums() {     // sliders also move from code (presets, calibration, reset)
      for (const [range, num] of numPairs) {
        if (document.activeElement !== num && num.value !== range.value) { num.value = range.value; num.classList.remove("is-bad"); }
      }
    }

    // ---------- playhead position inside the bar ----------
    // A grid mark is drawn in the middle of its 16th-note cell, so a hit exactly on slot s
    // sits at (s + 0.5) / slots of the bar's width. The playhead follows the beats (not a
    // straight line from bar line to bar line, beats aren't perfectly even) and is shifted
    // half a cell, so it crosses each mark at the moment the hit sounds.
    const beatGrid = beats.length ? beats.concat([beats[beats.length - 1] +
      (beats.length > 1 ? beats[beats.length - 1] - beats[beats.length - 2] : 0.5)]) : [];
    const barBeat = bars.map((b) => {
      let j = Math.max(lastLE(beats, b), 0);
      if (j + 1 < beats.length && Math.abs(beats[j + 1] - b) < Math.abs(beats[j] - b)) j++;
      return j;
    });
    // Beat tracking tends to put beats a little after the attacks (~20-30 ms). With drums we
    // can measure it: the median distance from each hit to its grid slot. The playhead is
    // shifted by that much so it meets the marks when they actually sound.
    let gridLead = 0;
    if (K && K.hits.length >= 8 && beatGrid.length >= 2) {
      const res = [];
      for (const [t] of K.hits) {
        const j = Math.min(Math.max(lastLE(beatGrid, t), 0), beatGrid.length - 2);
        const len = (beatGrid[j + 1] - beatGrid[j]) / 4, f = (t - beatGrid[j]) / len;
        res.push((f - Math.round(f)) * len);
      }
      res.sort((a, b) => a - b);
      gridLead = Math.min(0.06, Math.max(-0.06, res[res.length >> 1]));
    }
    root.dataset.gridLead = (gridLead * 1000).toFixed(1);   // ms, for tests
    const barSlots = barEls.map((el) => { const r = el.querySelector(".mp-row"); return r ? r.children.length : 0; });
    function cursorFrac(i, t) {
      const n = barSlots[i];
      if (!D.hasTab || !n || beatGrid.length < 2) {
        return Math.min(Math.max((t - bars[i]) / Math.max(barEnd(i) - bars[i], 1e-3), 0), 1);
      }
      t -= gridLead;
      const j = Math.min(Math.max(lastLE(beatGrid, t), 0), beatGrid.length - 2);
      const q = (j - barBeat[i]) * 4 + 4 * (t - beatGrid[j]) / Math.max(beatGrid[j + 1] - beatGrid[j], 1e-3);
      return Math.min(Math.max((q + 0.5) / n, 0), 1);
    }
    root.mpCursorFrac = cursorFrac;  // tests

    // ---------- render loop ----------
    function render(force) {
      const t = audio.currentTime;
      const i = Math.max(lastLE(bars, t + PREROLL), 0);   // a jump lands PREROLL early
      if (i !== curBar || force) {
        if (barEls[curBar]) barEls[curBar].classList.remove("is-now");
        if (barEls[i]) {
          barEls[i].classList.add("is-now");
        }
        followKick = performance.now() + 1500;
        if (bandEls[curBar]) bandEls[curBar].classList.remove("is-now");
        if (bandEls[i]) bandEls[i].classList.add("is-now");   // a jump while paused still brings the bar into view
        curBar = i;
        barNow.textContent = i + 1;
        secEls.forEach((el, k) => {
          const s = D.sections[k];
          el.classList.toggle("is-now", i + 1 >= s.start_bar && i + 1 <= s.end_bar);
        });
      }
      const frac = cursorFrac(i, t);
      const cur = barEls[i] && barEls[i].querySelector(".mp-cursor");
      if (cur) cur.style.left = (frac * 100).toFixed(2) + "%";
      head.style.left = ((t / D.duration) * 100).toFixed(3) + "%";
      followScroll(t, i);
      if (STEPS) STEPS.update(t, i);
      timeEl.textContent = fmt(t) + " / " + fmt(D.duration);

      const c = lastLE(chords, t, "start");
      nowEl.textContent = c >= 0 && t < chords[c].end + 0.05 ? chords[c].label : " ";
      let n = c + 1;
      while (n < chords.length && c >= 0 && chords[n].label === chords[c].label) n++;
      if (n < chords.length) {
        nextEl.textContent = chords[n].label;
        const beatsAway = lastLE(beats, chords[n].start - 0.02) - lastLE(beats, t);
        nextIn.textContent = beatsAway > 0 && beatsAway <= 8 ? "in " + beatsAway + (beatsAway === 1 ? " beat" : " beats") : "";
      } else { nextEl.textContent = " "; nextIn.textContent = ""; }
    }

    function tick() {
      if (!alive) return;
      if (!root.isConnected) { destroy(); return; }
      const t = audio.currentTime;
      if (!audio.paused) {
        if (loopOn.checked) {
          const [, b] = loopRange();
          if (t >= b - 0.02) { wrap(); requestAnimationFrame(tick); return; }
        }
        if (K && t > lastT && t - lastT < 0.5) kitHits(lastT, t);
        if (t > lastT && t - lastT < 0.5) {                     // the playhead thumps on every beat
          const j = lastLE(beats, t);
          if (j >= 0 && beats[j] > lastT) {
            const cur = barEls[curBar] && barEls[curBar].querySelector(".mp-cursor");
            if (cur) {
              cur.classList.toggle("is-downbeat", barSet.has(beats[j].toFixed(3)));
              cur.classList.remove("is-beat"); void cur.offsetWidth; cur.classList.add("is-beat");
            }
          }
        }
        if (clickOn.checked && t > lastT && t - lastT < 0.5) {
          const j = lastLE(beats, t);
          if (j >= 0 && beats[j] > lastT) click(audioCtx().currentTime, barSet.has(beats[j].toFixed(3)));
        }
      }
      lastT = t;
      if (M) syncFollowers(performance.now());
      if (K) kitAnticipate(t);
      syncNums();
      render(false);
      requestAnimationFrame(tick);
    }

    // ---------- sheet: which bars start and end a line (labels go in the line's gutter) ----------
    function markLines() {
      let prevTop = null;
      barEls.forEach((el, k) => {
        const top = el.offsetTop, next = barEls[k + 1];
        el.classList.toggle("is-line-start", prevTop === null || Math.abs(top - prevTop) > 2);
        el.classList.toggle("is-line-end", !next || Math.abs(next.offsetTop - top) > 2);
        prevTop = top;
      });
    }

    // ---------- follow: the page glides to the next line, in time with the music ----------
    // Instead of jumping when the new line starts, the sheet slides up during the last beat of
    // the line (eased in and out), so the next line is on top exactly when its first bar starts
    // and the eye never loses its place. The slide is computed from the song position every
    // frame, so it follows the speed, pauses with the music and is never late.
    let lineTop = [], nextLine = [], followKick = 0, userScrollUntil = 0;
    const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    function measureLines() {
      if (!sheet || !barEls.length) return;
      const starts = [];
      const lineOf = barEls.map((el, k) => {
        if (k === 0 || Math.abs(el.offsetTop - barEls[k - 1].offsetTop) > 2) starts.push(k);
        return starts.length - 1;
      });
      lineTop = barEls.map((el) => el.offsetTop - sheetPadTop);
      nextLine = lineOf.map((l) => (l + 1 < starts.length ? starts[l + 1] : -1));
    }
    function followScroll(t, i) {
      if (!follow.checked || !sheet || !lineTop.length || !sheet.offsetParent) return;
      const now = performance.now();
      if (now < userScrollUntil || (audio.paused && now > followKick)) return;
      let target = lineTop[i];
      const n = nextLine[i];
      if (n >= 0 && !reduceMotion) {
        const lead = Math.min(0.7, Math.max(0.3, 60 / D.tempo / rate)) * rate;   // ~1 beat, in song s
        const p = (t - (bars[n] - lead)) / lead;
        if (p > 0) {
          const e = Math.min(p, 1), ease = e * e * (3 - 2 * e);
          target = lineTop[i] + (lineTop[n] - lineTop[i]) * ease;
        }
      }
      const cur = sheet.scrollTop, d = target - cur;
      if (Math.abs(d) < 0.5) return;
      // during a glide the steps are small; after a jump (click, loop, seek) ease in quickly
      sheet.scrollTop = Math.abs(d) <= 60 || reduceMotion ? target : cur + d * 0.22;
    }
    if (sheet) {
      const userScroll = () => { userScrollUntil = performance.now() + 2500; };
      sheet.addEventListener("wheel", userScroll, { passive: true, signal: ac.signal });
      sheet.addEventListener("touchmove", userScroll, { passive: true, signal: ac.signal });
      sheet.addEventListener("pointerdown", (e) => { if (e.target === sheet) userScroll(); }, sig);  // scrollbar
    }
    audio.addEventListener("play", () => { userScrollUntil = 0; }, sig);
    root.mpLines = () => ({ lineTop, nextLine });   // tests

    // ---------- sheet: exactly two lines of bars ----------
    let sheetPadTop = 0;
    // How many lines of music the sheet shows (2 by default; drag the grip under it for more).
    // The height always fits whole lines, and Follow keeps the line being played on top, so
    // extra lines are music ahead.
    let nLines = 2, gripDragging = false;
    function lineTops() {
      const tops = [];
      barEls.forEach((el) => { const t = el.offsetTop; if (!tops.length || t > tops[tops.length - 1] + 2) tops.push(t); });
      return tops;
    }
    function heightFor(k, tops, cs) {      // sheet height that shows exactly k whole lines
      const last = barEls[barEls.length - 1];
      const inner = k < tops.length ? tops[k] - tops[0] - (parseFloat(cs.rowGap) || 0)
                                    : last.offsetTop + last.offsetHeight - tops[0];
      return Math.ceil(inner + sheetPadTop + (parseFloat(cs.paddingBottom) || 0));
    }
    function fitTwoLines() {
      if (!sheet || !barEls.length || gripDragging) return;
      const cs = getComputedStyle(sheet);
      sheetPadTop = parseFloat(cs.paddingTop) || 0;
      const tops = lineTops();
      const lineH = tops.length > 1 ? tops[1] - tops[0] : barEls[0].offsetHeight;
      const n = Math.max(1, Math.min(nLines, tops.length));
      sheet.style.height = heightFor(n, tops, cs) + "px";
      sheet.style.maxHeight = "none";
      root.dataset.lineHeight = String(lineH);                             // for tests
      root.dataset.lines = String(n);
      const gl = root.querySelector(".mp-grip-n");
      if (gl) gl.textContent = n + (n === 1 ? " line" : " lines");
      markLines();
      measureLines();
    }
    const ro = new ResizeObserver(() => { fitTwoLines(); curBar = -2; render(true); });
    if (sheet) ro.observe(sheet);
    if (barEls[0]) ro.observe(barEls[0]);                 // bars grow when the web fonts arrive
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { if (alive) fitTwoLines(); });
    ac.signal.addEventListener("abort", () => ro.disconnect());

    // ---------- layout: resizable kit / grid, show-hide, tab size (remembered) ----------
    const LKEY = "music-practice-layout";
    function loadLayout() { try { return JSON.parse(localStorage.getItem(LKEY) || "{}"); } catch (e) { return {}; } }
    function saveLayout(p) { try { localStorage.setItem(LKEY, JSON.stringify({ ...loadLayout(), ...p })); } catch (e) {} }
    function setupLayout() {
      const L = loadLayout();
      const body = $(".mp-body--drums"), split = $(".mp-split");
      const showKit = $(".mp-show-kit"), showSheet = $(".mp-show-sheet");
      const zIn = $(".mp-zoom-in"), zOut = $(".mp-zoom-out");
      let z = Math.min(1.6, Math.max(0.7, L.zoom || 1));
      function applyZoom() {
        if (sheet) sheet.style.setProperty("--z", z.toFixed(2));
        root.dataset.zoom = z.toFixed(2);
        if (zOut) zOut.disabled = z <= 0.71; if (zIn) zIn.disabled = z >= 1.59;
      }
      zIn && zIn.addEventListener("click", () => { z = Math.min(1.6, z + 0.15); applyZoom(); saveLayout({ zoom: z }); }, sig);
      zOut && zOut.addEventListener("click", () => { z = Math.max(0.7, z - 0.15); applyZoom(); saveLayout({ zoom: z }); }, sig);
      applyZoom();
      const mixer = $(".mp-mixer");
      if (mixer) {
        if (L.mixerOpen === false) mixer.open = false;
        mixer.addEventListener("toggle", () => saveLayout({ mixerOpen: mixer.open }), sig);
      }
      if (!body) return;
      function setKitW(px) {
        const max = Math.max(260, body.clientWidth - 280);
        const w = Math.round(Math.min(max, Math.max(220, px)));
        body.style.setProperty("--kit-w", w + "px");
        return w;
      }
      if (L.kitW) setKitW(L.kitW);
      // right-hand panel shows the grid OR the highway (or nothing); never hide everything
      const showHw = $(".mp-show-hw"), showSteps = $(".mp-show-steps");
      let right = L.right || (L.showSheet === false ? null : "grid");
      if (right === "steps" && !$(".mp-show-steps")) right = "grid";
      let kitOn = L.showKit !== false;
      function applyShow() {
        if (!kitOn && !right) right = "grid";
        body.classList.toggle("no-kit", !kitOn);
        body.classList.toggle("no-sheet", !right);
        body.classList.toggle("show-hw", right === "hw");
        body.classList.toggle("show-steps", right === "steps");
        if (showSteps) { showSteps.setAttribute("aria-pressed", String(right === "steps")); showSteps.disabled = right === "steps" && !kitOn; }
        if (STEPS) STEPS.show(right === "steps");
        showKit.setAttribute("aria-pressed", String(kitOn));
        showSheet.setAttribute("aria-pressed", String(right === "grid"));
        if (showHw) showHw.setAttribute("aria-pressed", String(right === "hw"));
        showKit.disabled = kitOn && !right;
        showSheet.disabled = right === "grid" && !kitOn;
        if (showHw) showHw.disabled = right === "hw" && !kitOn;
        if (HW) HW.show(right === "hw");
        if (right === "grid") { fitTwoLines(); curBar = -2; render(true); }
        saveLayout({ showKit: kitOn, right, showSheet: right === "grid" });
      }
      showGrid = () => { if (right !== "grid") { right = "grid"; applyShow(); } };
      showKit.addEventListener("click", () => { kitOn = !kitOn; applyShow(); }, sig);
      showSheet.addEventListener("click", () => { right = right === "grid" ? null : "grid"; applyShow(); }, sig);
      if (showHw) showHw.addEventListener("click", () => { right = right === "hw" ? null : "hw"; applyShow(); }, sig);
      if (showSteps) showSteps.addEventListener("click", () => { right = right === "steps" ? null : "steps"; applyShow(); }, sig);
      applyShow();
      // drag the divider (pointer), arrows (keyboard), double-click resets
      let drag = null;
      split.addEventListener("pointerdown", (e) => {
        drag = { x: e.clientX, w: $(".mp-kit").getBoundingClientRect().width };
        split.setPointerCapture(e.pointerId); body.classList.add("is-resizing"); e.preventDefault();
      }, sig);
      split.addEventListener("pointermove", (e) => { if (drag) setKitW(drag.w + e.clientX - drag.x); }, sig);
      const endDrag = () => {
        if (!drag) return; drag = null; body.classList.remove("is-resizing");
        saveLayout({ kitW: $(".mp-kit").getBoundingClientRect().width });
      };
      split.addEventListener("pointerup", endDrag, sig);
      split.addEventListener("pointercancel", endDrag, sig);
      split.addEventListener("keydown", (e) => {
        if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
        e.preventDefault(); e.stopPropagation();
        const w = setKitW($(".mp-kit").getBoundingClientRect().width + (e.key === "ArrowRight" ? 30 : -30));
        saveLayout({ kitW: w });
      }, sig);
      split.addEventListener("dblclick", () => { body.style.removeProperty("--kit-w"); saveLayout({ kitW: null }); }, sig);
    }

    function destroy() {
      if (!alive) return;
      alive = false; ac.abort();
      try { cancelCountIn(); } catch (e) {}
      audio.pause();
      followers.forEach((f) => { f.pause(); f.removeAttribute("src"); f.load(); });
      blobUrls.forEach((u) => URL.revokeObjectURL(u));
      root.classList.remove("is-playing", "is-counting");
      barEls.forEach((el) => el.classList.remove("is-now", "in-loop"));
      if (root.__mpDestroy === destroy) root.__mpDestroy = null;
    }
    root.__mpDestroy = destroy;
    // only one practice player makes sound at a time
    audio.addEventListener("play", () => {
      document.querySelectorAll("audio.mp-audio").forEach((a) => { if (a !== audio) a.pause(); });
    }, sig);
    onSpeed();
    if (M) { applyRate(); preset(srcSel ? srcSel.value : "full"); }
    else if (srcSel) setSource(srcSel.value, false); else if (D.sources[0]) setSource(D.sources[0].id, false);
    setupLayout();
    // ---------- sheet height: drag the grip under the sheet to see more lines ahead ----------
    const grip = $(".mp-sheet-grip");
    if (grip && sheet) {
      const MAXL = 8;
      nLines = Math.min(MAXL, Math.max(1, loadLayout().lines || 2));
      const setLines = (k) => {
        nLines = Math.min(MAXL, Math.max(1, k)); saveLayout({ lines: nLines });
        fitTwoLines(); curBar = -2; render(true);
      };
      let drag = null;
      grip.addEventListener("pointerdown", (e) => {
        drag = { y: e.clientY, h: sheet.offsetHeight }; gripDragging = true;
        grip.setPointerCapture(e.pointerId); root.classList.add("is-grip-drag"); e.preventDefault();
      }, sig);
      const nearest = () => {
        const cs = getComputedStyle(sheet), tops = lineTops(), h = sheet.offsetHeight;
        let best = 1;
        for (let k = 1; k <= Math.min(MAXL, tops.length); k++) {
          if (Math.abs(heightFor(k, tops, cs) - h) < Math.abs(heightFor(best, tops, cs) - h)) best = k;
        }
        return best;
      };
      grip.addEventListener("pointermove", (e) => {
        if (!drag) return;
        sheet.style.height = Math.max(60, drag.h + e.clientY - drag.y) + "px";
        const k = nearest(), gl = grip.querySelector(".mp-grip-n");
        if (gl) gl.textContent = k + (k === 1 ? " line" : " lines");
      }, sig);
      const end = () => {
        if (!drag) return;
        drag = null; gripDragging = false; root.classList.remove("is-grip-drag");
        setLines(nearest());
      };
      grip.addEventListener("pointerup", end, sig);
      grip.addEventListener("pointercancel", end, sig);
      grip.addEventListener("dblclick", () => setLines(2), sig);
      grip.addEventListener("keydown", (e) => {
        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
          e.preventDefault(); e.stopPropagation();
          setLines(nLines + (e.key === "ArrowDown" ? 1 : -1));
        }
      }, sig);
      fitTwoLines();
    }
    const R = window.__mpResume;
    if (R) {                     // back after saving a drum fix
      window.__mpResume = null;
      speed.value = R.speed; onSpeed();
      loopOn.checked = R.loop; fromIn.value = R.from; toIn.value = R.to;
      if (M) resumeT = R.t;      // the master's src is set when its download finishes
      else {
        const go = () => { audio.currentTime = R.t; lastT = R.t - 0.002; render(true); };
        if (audio.readyState >= 1 && audio.src) go(); else audio.addEventListener("loadedmetadata", go, { once: true, signal: ac.signal });
      }
      if (R.fix && FIX) FIX.setOpen(true);
      requestAnimationFrame(() => window.scrollTo(0, R.y));
    }
    markRange();
    render(true);
    requestAnimationFrame(tick);
  }

  // (Re)start a player whenever its content key changes, not just when it first appears.
  function scan() {
    document.querySelectorAll(".mp-player[data-mp]").forEach((r) => {
      if (r.__mpKey !== r.dataset.key || !r.__mpDestroy) init(r);
    });
  }
  new MutationObserver(scan).observe(document.documentElement,
    { childList: true, subtree: true, attributes: true, attributeFilter: ["data-key", "data-mp"] });
  if (document.readyState !== "loading") scan(); else document.addEventListener("DOMContentLoaded", scan);
})();
