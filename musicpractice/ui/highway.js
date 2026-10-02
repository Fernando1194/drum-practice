/* Music Practice: drum "highway" (Guitar Hero / Rock Band style) + Web MIDI scoring.

   Notes fall toward a hit line, one lane per kit piece (kit colors), the kick as a bar across
   all lanes. Hits from an electronic drum kit (Web MIDI) are matched to the nearest unplayed
   note on the same piece and judged by timing in REAL time (so 50% speed is judged the same):
   Perfect <= 30 ms, Good <= 60 ms, OK <= 100 ms, nothing within 150 ms = extra hit.
   Notes that pass the line by more than 150 ms unplayed = missed.

   window.MPHighway(api) is called by the player for drum songs. api = { root, D, audio, sig,
   getRate(), isAlive(), flash(piece, vel) }. */
(function () {
  if (window.MPHighway) return;

  // General MIDI drum notes plus the extra zones common on Roland / Yamaha / Alesis kits
  const GM = {
    35: "kick", 36: "kick",
    38: "snare", 40: "snare", 37: "snare",
    42: "hihat", 44: "hihat", 46: "hihat", 22: "hihat", 26: "hihat",
    49: "crash", 57: "crash", 55: "crash", 52: "crash",
    51: "ride", 59: "ride", 53: "ride",
    48: "tom_high", 50: "tom_high", 45: "tom_mid", 47: "tom_mid",
    41: "tom_floor", 43: "tom_floor", 58: "tom_floor",
  };
  const LANE_ORDER = ["hihat", "snare", "tom_high", "tom_mid", "tom_floor", "ride", "crash"];
  const CYM = new Set(["crash", "ride"]);
  const WIN = { perfect: 30, good: 60, ok: 100, catch: 150 };       // ms, real time
  const CREDIT = { perfect: 1, good: 0.8, ok: 0.5, miss: 0 };
  const LOOK_S = 2.0;                                                  // seconds of real time on screen
  const SKEY = "music-practice-midi";
  const load = () => { try { return JSON.parse(localStorage.getItem(SKEY) || "{}"); } catch (e) { return {}; } };
  const save = (p) => { try { localStorage.setItem(SKEY, JSON.stringify({ ...load(), ...p })); } catch (e) {} };

  window.MPHighway = function (api) {
    const { root, D, audio, sig } = api;
    const K = D.drums;
    const panel = root.querySelector(".mp-hw");
    if (!K || !panel) return null;
    const $ = (s) => panel.querySelector(s);
    const canvas = $(".mp-hw-canvas"), g = canvas.getContext("2d");
    const status = $(".mp-midi-status"), offsetIn = $(".mp-offset"), offsetOut = $(".mp-offset-out");
    const merge = $(".mp-cym-merge");
    const lastLE = api.lastLE;

    // ---------- reference notes ----------
    const notes = K.hits.map(([t, p, v], i) => ({ t, piece: K.pieces[p], v, i, state: null, off: 0 }));
    const used = new Set(notes.map((n) => n.piece));
    const lanes = LANE_ORDER.filter((p) => used.has(p));
    const color = {};
    root.querySelectorAll(".mp-pc").forEach((el) => { color[el.dataset.p] = el.style.getPropertyValue("--c").trim(); });
    const times = notes.map((n) => n.t);
    const scored = (p) => { const c = panel.querySelector(`.mp-score-on[data-p="${p}"]`); return c ? c.checked : true; };
    const sameKind = (a, b) => a === b || (merge.checked && CYM.has(a) && CYM.has(b));

    // ---------- settings ----------
    const S = load();
    const map = { ...GM, ...(S.map || {}) };
    let offsetMs = S.offset || 0;
    function showOffset() { offsetIn.value = offsetMs; offsetOut.textContent = "ms"; }
    showOffset();
    offsetIn.addEventListener("input", () => { offsetMs = parseInt(offsetIn.value, 10); showOffset(); save({ offset: offsetMs }); }, sig);

    // ---------- score ----------
    const st = { perfect: 0, good: 0, ok: 0, miss: 0, extra: 0, combo: 0, best: 0, offs: [] };
    let pass = null;                                      // per loop pass
    const passes = [];
    const pops = [];                                      // on-screen judgement texts
    const padFlash = {};                                  // lane -> perf time of last pad hit
    function accuracy(s) {
      const n = s.perfect + s.good + s.ok + s.miss;
      return n ? (100 * (s.perfect * CREDIT.perfect + s.good * CREDIT.good + s.ok * CREDIT.ok)) / n : null;
    }
    function mean(a) { return a.length ? a.reduce((x, y) => x + y, 0) / a.length : null; }
    function tendency(m) {
      if (m === null) return "--";
      if (Math.abs(m) < 8) return "on time";
      return m < 0 ? `rushing ${Math.round(-m)} ms` : `dragging ${Math.round(m)} ms`;
    }
    function paintScore() {
      const a = accuracy(st);
      $(".mp-hw-acc b").textContent = a === null ? "--" : `${Math.round(a)}%`;
      $(".mp-hw-combo").textContent = `${st.combo}`;
      for (const k of ["perfect", "good", "ok", "miss", "extra"]) $(`.mp-hw-n-${k}`).textContent = st[k];
      $(".mp-hw-mean").textContent = tendency(mean(st.offs));
      $(".mp-hw-passes").innerHTML = passes.slice(-8).map((p, k) =>
        `<li>Pass ${passes.length - Math.min(8, passes.length) + k + 1}: <b>${Math.round(p.acc)}%</b> <span>${tendency(p.mean)}</span></li>`).join("");
    }
    function newPass() { pass = { perfect: 0, good: 0, ok: 0, miss: 0, extra: 0, offs: [] }; }
    function closePass() {
      if (pass && pass.perfect + pass.good + pass.ok + pass.miss >= 4) passes.push({ acc: accuracy(pass), mean: mean(pass.offs) });
      newPass();
    }
    newPass();
    function count(kind, off) {
      st[kind]++; pass[kind]++;
      if (kind === "miss") st.combo = 0;
      else if (kind !== "extra") { st.combo++; st.best = Math.max(st.best, st.combo); st.offs.push(off); pass.offs.push(off); }
      paintScore();
    }
    function resetScore() {
      Object.assign(st, { perfect: 0, good: 0, ok: 0, miss: 0, extra: 0, combo: 0, best: 0, offs: [] });
      passes.length = 0; newPass();
      notes.forEach((n) => { n.state = null; n.off = 0; });
      paintScore();
    }
    $(".mp-hw-reset").addEventListener("click", resetScore, sig);

    // ---------- clock: song time of a MIDI event ----------
    const rate = () => api.getRate();
    function songTimeAt(perfTs) {
      // media time now, moved back by how long ago the hit happened, minus the system latency
      const ageS = (performance.now() - perfTs) / 1000;
      return audio.currentTime - (ageS + offsetMs / 1000) * rate();
    }

    // ---------- judging ----------
    let segStart = 0;                                    // notes before this (song time) aren't judged
    function judge(piece, vel, perfTs) {
      const lane = CYM.has(piece) && merge.checked && !lanes.includes(piece) ? lanes.find((l) => CYM.has(l)) || piece : piece;
      padFlash[lane] = performance.now();
      api.flash(piece, vel);
      if (audio.paused || !scored(piece)) return;
      const ts = songTimeAt(perfTs), r = rate();
      const lo = lastLE(times, ts - (WIN.catch / 1000) * r);
      let best = null;
      for (let k = Math.max(lo, 0); k < notes.length && notes[k].t <= ts + (WIN.catch / 1000) * r; k++) {
        const n = notes[k];
        if (n.state || n.t < segStart - 1e-3 || !sameKind(n.piece, piece) || !scored(n.piece)) continue;
        if (!best || Math.abs(n.t - ts) < Math.abs(best.t - ts)) best = n;
      }
      if (!best) { count("extra"); pops.push({ lane, text: "extra", c: "#9aabc2", at: performance.now() }); return; }
      const off = ((ts - best.t) / r) * 1000;           // ms real time; < 0 early, > 0 late
      const a = Math.abs(off);
      const kind = a <= WIN.perfect ? "perfect" : a <= WIN.good ? "good" : "ok";
      best.state = kind; best.off = off;
      count(kind, off);
      const label = kind === "perfect" ? "Perfect" : `${off < 0 ? "Early" : "Late"} ${Math.round(a)}`;
      pops.push({ lane: best.piece === "kick" ? "kick" : lane, text: label, c: kind === "perfect" ? "#7ee2a8" : kind === "good" ? "#cfe3ff" : "#ffd28a", at: performance.now() });
    }
    function sweepMisses(t) {                            // notes that slid past the line unplayed
      const r = rate(), limit = t - (WIN.catch / 1000) * r;
      for (let k = Math.max(lastLE(times, segStart - 1e-3), 0); k < notes.length && notes[k].t < limit; k++) {
        const n = notes[k];
        if (n.state || n.t < segStart - 1e-3 || !scored(n.piece)) continue;
        n.state = "miss"; count("miss", 0);
      }
    }
    // jumps (loop wrap, bar click): what's ahead is fresh again; a jump back closes a pass
    let lastT = audio.currentTime, lastPerf = performance.now();
    function watchJumps(t, now) {
      const expected = lastT + ((now - lastPerf) / 1000) * (audio.paused ? 0 : rate());
      if (Math.abs(t - expected) > 0.25) {
        if (t < lastT) closePass();
        segStart = t;
        notes.forEach((n) => { if (n.t >= t - 1e-3) { n.state = null; n.off = 0; } });
      }
      lastT = t; lastPerf = now;
    }
    audio.addEventListener("play", () => { segStart = Math.min(segStart, audio.currentTime); if (segStart > audio.currentTime) segStart = audio.currentTime; }, sig);

    // ---------- Web MIDI ----------
    let learnFor = null, calib = null, access = null;
    let monLine = "Last pad: --", ccLast = "";
    const mon = $(".mp-midi-mon");
    function onMessage(e) {
      const [st0, note, vel] = e.data;
      // MIDI monitor: shows what each pad sends, so any kit's map can be read off directly
      if ((st0 & 0xf0) === 0xb0) {
        ccLast = note === 4 ? `hi-hat pedal (CC4) ${vel}` : `CC${note} ${vel}`;
        if (mon) mon.textContent = monLine + (ccLast ? `  |  ${ccLast}` : "");
        return;
      }
      if ((st0 & 0xf0) !== 0x90 || vel === 0) return;  // note-on only
      root.dataset.lastMidi = String(note);
      monLine = `Last pad: note ${note}${map[note] ? " (" + map[note].replace("_", " ") + ")" : ""}, velocity ${vel}`;
      if (mon) mon.textContent = monLine + (ccLast ? `  |  ${ccLast}` : "");
      if (learnFor) {
        map[note] = learnFor;
        const m = { ...(load().map || {}) }; m[note] = learnFor; save({ map: m });
        status.textContent = `Pad ${note} now plays ${learnFor.replace("_", " ")}`;
        learnFor = null; return;
      }
      if (calib) { calib.hits.push(e.timeStamp || performance.now()); return; }
      const piece = map[note];
      if (!piece) { status.textContent = `Pad ${note} isn't mapped: use "learn" on a lane`; return; }
      judge(piece, vel / 127, e.timeStamp || performance.now());
    }
    function attach() {
      const names = [];
      access.inputs.forEach((inp) => { inp.onmidimessage = onMessage; names.push(inp.name); });
      status.textContent = names.length ? `Connected: ${names.join(", ")}` : "No MIDI device found: plug in the kit, then connect again";
      root.dataset.midi = names.length ? "connected" : "none";
    }
    sig.signal.addEventListener("abort", () => { if (access) access.inputs.forEach((inp) => { if (inp.onmidimessage === onMessage) inp.onmidimessage = null; }); });
    $(".mp-midi-connect").addEventListener("click", async () => {
      if (!navigator.requestMIDIAccess) { status.textContent = "This browser has no Web MIDI: use Chrome or Edge"; return; }
      try {
        access = await navigator.requestMIDIAccess();
        attach();
        access.onstatechange = () => attach();
      } catch (err) { status.textContent = "MIDI access was blocked: allow it in the address bar and try again"; }
    }, sig);
    panel.querySelectorAll(".mp-learn").forEach((b) => b.addEventListener("click", (ev) => {
      ev.preventDefault();
      learnFor = b.dataset.p;
      status.textContent = `Hit the pad you want for ${b.parentElement.textContent.replace("learn", "").trim()}...`;
    }, sig));

    // calibration: 8 clicks; your hits vs the clicks give the system's latency
    $(".mp-calibrate").addEventListener("click", () => {
      if (!access) { status.textContent = "Connect the MIDI kit first"; return; }
      const ctx = api.audioCtx(), beat = 0.6, t0 = ctx.currentTime + 0.6;
      const outLat = (ctx.outputLatency || ctx.baseLatency || 0) * 1000;
      const clicksPerf = [];
      for (let k = 0; k < 8; k++) {
        api.click(t0 + k * beat, k % 4 === 0);
        clicksPerf.push(performance.now() + (t0 + k * beat - ctx.currentTime) * 1000 + outLat);
      }
      calib = { hits: [] };
      root.__mpCalibClicks = clicksPerf;                 // tests
      status.textContent = "Hit any pad on every click...";
      setTimeout(() => {
        const offs = calib.hits.map((h) => {
          let best = clicksPerf[0];
          clicksPerf.forEach((c) => { if (Math.abs(h - c) < Math.abs(h - best)) best = c; });
          return h - best;
        }).filter((d) => Math.abs(d) < 250).sort((a, b) => a - b);
        calib = null;
        if (offs.length < 4) { status.textContent = "Not enough hits heard; try again"; return; }
        offsetMs = Math.round(offs[Math.floor(offs.length / 2)]);
        showOffset(); save({ offset: offsetMs });
        status.textContent = `Calibrated: your hits register ${Math.abs(offsetMs)} ms ${offsetMs >= 0 ? "late" : "early"}; compensated`;
      }, (0.6 + 8 * beat + 0.5) * 1000);
    }, sig);

    // ---------- drawing ----------
    let W = 0, H = 0, dpr = 1;
    function resize() {
      dpr = window.devicePixelRatio || 1;
      W = canvas.clientWidth; H = canvas.clientHeight;
      canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    }
    const ro = new ResizeObserver(resize); ro.observe(canvas);
    sig.signal.addEventListener("abort", () => ro.disconnect());
    function rr(x, y, w, h, r) { g.beginPath(); g.roundRect(x, y, w, h, r); }
    function draw(t, now) {
      if (!W || !H) resize();
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, W, H);
      const r = rate(), hitY = H * 0.86, look = LOOK_S * r;            // song seconds visible
      const y = (tn) => hitY - ((tn - t) / look) * hitY;
      const lw = W / Math.max(lanes.length, 1);
      // lanes
      lanes.forEach((p, k) => {
        g.fillStyle = k % 2 ? "rgba(255,255,255,.035)" : "rgba(255,255,255,.015)";
        g.fillRect(k * lw, 0, lw, H);
        g.globalAlpha = scored(p) ? 1 : 0.35;
        g.fillStyle = color[p]; g.fillRect(k * lw + lw * 0.5 - 1, 0, 2, hitY);
        g.globalAlpha = 1;
      });
      // beats and bars
      const b0 = Math.max(lastLE(D.beats, t - look * 0.2), 0);
      for (let k = b0; k < D.beats.length && D.beats[k] < t + look; k++) {
        const yy = y(D.beats[k]);
        const isBar = lastLE(D.bars, D.beats[k] + 1e-3) >= 0 && Math.abs(D.bars[lastLE(D.bars, D.beats[k] + 1e-3)] - D.beats[k]) < 1e-3;
        g.fillStyle = isBar ? "rgba(243,245,248,.55)" : "rgba(243,245,248,.16)";
        g.fillRect(0, yy - (isBar ? 1 : 0.5), W, isBar ? 2 : 1);
        if (isBar) { g.fillStyle = "rgba(243,245,248,.6)"; g.font = "600 12px system-ui"; g.fillText(String(lastLE(D.bars, D.beats[k] + 1e-3) + 1), 6, yy - 5); }
      }
      // hit line + pads
      g.fillStyle = "rgba(243,245,248,.9)"; g.fillRect(0, hitY - 1.5, W, 3);
      lanes.forEach((p, k) => {
        const age = now - (padFlash[p] || -1e9), on = Math.max(0, 1 - age / 180);
        g.beginPath(); g.arc(k * lw + lw / 2, hitY, Math.min(lw * 0.32, 26), 0, 7);
        g.lineWidth = 3; g.strokeStyle = color[p]; g.stroke();
        if (on > 0) { g.globalAlpha = on; g.fillStyle = color[p]; g.fill(); g.globalAlpha = 1; }
      });
      if (used.has("kick")) {
        const age = now - (padFlash.kick || -1e9), on = Math.max(0, 1 - age / 180);
        if (on > 0) { g.globalAlpha = on * 0.8; g.fillStyle = color.kick; g.fillRect(0, hitY + 8, W, 8); g.globalAlpha = 1; }
      }
      // notes
      const k0 = Math.max(lastLE(times, t - look * 0.2), 0);
      for (let k = k0; k < notes.length && notes[k].t < t + look; k++) {
        const n = notes[k], yy = y(n.t);
        if (n.state && n.state !== "miss") continue;                     // played: gone
        const fade = scored(n.piece) ? (n.state === "miss" ? 0.3 : 1) : 0.3;
        g.globalAlpha = fade;
        if (n.piece === "kick") {
          g.fillStyle = color.kick; rr(4, yy - 5, W - 8, 10, 5); g.fill();
        } else {
          const li = lanes.indexOf(n.piece); if (li < 0) continue;
          const cx = li * lw + lw / 2, w = Math.min(lw * 0.7, 70);
          g.fillStyle = color[n.piece];
          if (CYM.has(n.piece) || n.piece === "hihat") {                  // cymbals: flat discs
            g.beginPath(); g.ellipse(cx, yy, w / 2, 9, 0, 0, 7); g.fill();
            g.lineWidth = 2; g.strokeStyle = "rgba(255,255,255,.7)"; g.stroke();
          } else { rr(cx - w / 2, yy - 9, w, 18, 6); g.fill(); g.lineWidth = 2; g.strokeStyle = "rgba(16,22,31,.6)"; g.stroke(); }
        }
        g.globalAlpha = 1;
      }
      // judgement pops
      for (let k = pops.length - 1; k >= 0; k--) {
        const p = pops[k], age = now - p.at;
        if (age > 700) { pops.splice(k, 1); continue; }
        const li = p.lane === "kick" ? (lanes.length - 1) / 2 : lanes.indexOf(p.lane);
        g.globalAlpha = 1 - age / 700; g.fillStyle = p.c; g.font = "700 15px system-ui"; g.textAlign = "center";
        g.fillText(p.text, li * lw + lw / 2, hitY - 34 - age / 25);
        g.textAlign = "start"; g.globalAlpha = 1;
      }
    }

    // ---------- loop ----------
    let visible = false;
    function frame() {
      if (!api.isAlive()) return;
      const now = performance.now(), t = audio.currentTime;
      watchJumps(t, now);
      if (!audio.paused) sweepMisses(t);
      if (visible) {
        draw(t, now);
        const bi = Math.max(lastLE(D.bars, t + 0.04), 0);
        const txt = `Bar ${bi + 1} of ${D.bars.length}`;
        if (where.textContent !== txt) where.textContent = txt;
      }
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
    paintScore();

    $(".mp-hw-play").addEventListener("click", () => api.toggle(), sig);
    const where = $(".mp-hw-where");
    return {
      show(on) {
        visible = on; panel.hidden = !on;
        if (on) { resize(); requestAnimationFrame(() => panel.scrollIntoView({ block: "end", behavior: "smooth" })); }
      },
      stats: () => ({ ...st, offs: st.offs.slice(), acc: accuracy(st), mean: mean(st.offs), passes: passes.slice() }),
      notes, lanes, reset: resetScore,
    };
  };
})();
