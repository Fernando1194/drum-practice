/* "Fix transcription" panel for drums: per-family sensitivity + add/remove hits on the grid.
   Changes stay in the page until Save; Save sends them to Python, which recomputes the hits
   (no model run) and re-renders the player. Used by player.js: window.MPDrumEdit({...}). */
(function () {
  if (window.MPDrumEdit) return;

  window.MPDrumEdit = function (o) {
    const { root, D, sig, onSave, onToggle } = o;
    const K = D.drums, F = K && K.fix;
    const panel = root.querySelector(".mp-fix"), btn = root.querySelector(".mp-show-fix");
    const sheet = root.querySelector(".mp-sheet");
    if (!F || !panel || !btn || !sheet) return null;
    const $ = (s) => panel.querySelector(s);
    const status = $(".mp-fix-status"), saveB = $(".mp-fix-save"), discardB = $(".mp-fix-discard");
    const resetB = $(".mp-fix-reset");
    const cym = new Set(F.cymbals);

    // ---------- sensitivity (same mapping as drum_edit.threshold_for) ----------
    function thrFor(f, s) {
      const d = F.defaults[f];
      return s <= 50 ? F.max - (F.max - d) * s / 50 : d - (d - F.min) * (s - 50) / 50;
    }
    const sliders = [...panel.querySelectorAll(".mp-sens")];
    const startVal = {};
    sliders.forEach((el) => { startVal[el.dataset.f] = el.value; });
    function countAt(fi, thr) {
      let n = 0;
      for (const c of F.cands) if (c[1] === fi && c[2] >= thr - 1e-9) n++;
      return n;
    }
    function showCount(el) {
      const f = el.dataset.f, fi = F.families.indexOf(f);
      const gone = (F.dropped && F.dropped[f]) || 0;   // removed by the "three hands" rule
      const now = Math.max(0, countAt(fi, thrFor(f, +el.value)) - gone);
      const dflt = Math.max(0, countAt(fi, F.defaults[f]) - gone);
      const out = panel.querySelector(`.mp-sens-n[data-f="${f}"]`);
      const diff = now - dflt;
      out.textContent = now + " hits" + (diff ? ` (${diff > 0 ? "+" : ""}${diff} vs AI)` : "");
    }
    const slidersChanged = () => sliders.some((el) => el.value !== startVal[el.dataset.f]);
    sliders.forEach((el) => {
      showCount(el);
      el.addEventListener("input", () => { showCount(el); update(); }, sig);
    });

    // ---------- editing cells ----------
    const barEls = [...root.querySelectorAll(".mp-bar")];
    const beats = D.beats, bars = D.bars;
    const diffs = beats.slice(1).map((b, j) => b - beats[j]).sort((x, y) => x - y);
    const ibi = diffs.length ? (diffs.length % 2 ? diffs[diffs.length >> 1]
      : (diffs[diffs.length / 2 - 1] + diffs[diffs.length / 2]) / 2) : 0.5;   // median, like Python
    const grid = beats.concat([beats[beats.length - 1] + ibi]);
    const barBeat = bars.map((b) => {
      let best = 0;
      for (let j = 1; j < beats.length; j++) if (Math.abs(beats[j] - b) < Math.abs(beats[best] - b)) best = j;
      return best;
    });
    function slotTime(bar, slot) {      // inverse of tab.quantize: the time a grid cell stands for
      const q = barBeat[bar] * 4 + slot, j = Math.min(Math.floor(q / 4), grid.length - 2);
      return +(grid[j] + (q / 4 - j) * (grid[j + 1] - grid[j])).toFixed(3);
    }
    const pending = new Map();   // cell -> {op, items, cls, txt}
    function cellInfo(cell) {
      const row = cell.parentElement, bar = cell.closest(".mp-bar");
      return { piece: row.dataset.p, bar: +bar.dataset.i, slot: [...row.children].indexOf(cell) };
    }
    function toggleCell(cell) {
      const p = pending.get(cell);
      if (p) {                                     // undo this change
        cell.className = p.cls; cell.textContent = p.txt; pending.delete(cell); update(); return;
      }
      const { piece, bar, slot } = cellInfo(cell);
      if (!piece) return;
      const rec = { cls: cell.className, txt: cell.textContent };
      if (cell.dataset.h) {
        rec.op = "remove";
        rec.items = cell.dataset.h.split(",").map((k) => [K.hits[+k][0], piece]);
        cell.classList.add("mp-del");
      } else {
        rec.op = "add";
        rec.items = [[slotTime(bar, slot), piece]];
        if (cym.has(piece)) { cell.className = "mp-x mp-add"; cell.textContent = "x"; }
        else { cell.className = "mp-o mp-add"; cell.textContent = "●"; }
      }
      pending.set(cell, rec); update();
    }
    sheet.addEventListener("click", (e) => {
      if (!open) return;
      const cell = e.target.closest(".mp-row > i");
      if (!cell || !sheet.contains(cell)) return;
      e.stopPropagation(); e.preventDefault();    // no jump to the bar while editing
      toggleCell(cell);
    }, { capture: true, signal: sig.signal });

    function discard() {
      pending.forEach((p, cell) => { cell.className = p.cls; cell.textContent = p.txt; });
      pending.clear();
      sliders.forEach((el) => { el.value = startVal[el.dataset.f]; showCount(el); });
      update();
    }

    // ---------- status / save ----------
    function update() {
      const n = pending.size, s = slidersChanged();
      const parts = [];
      if (n) parts.push(n + (n === 1 ? " cell changed" : " cells changed"));
      if (s) parts.push("sensitivity changed");
      status.textContent = parts.length ? parts.join(", ") + ", not saved yet."
        : (F.edits ? `${F.edits} hand edit${F.edits === 1 ? "" : "s"} saved for this song.` : "No changes.");
      root.classList.toggle("has-unsaved", !!(n || s));
      saveB.disabled = discardB.disabled = !(n || s);
    }
    function thresholds() {
      const t = { ...F.thr };
      sliders.forEach((el) => { t[el.dataset.f] = +thrFor(el.dataset.f, +el.value).toFixed(4); });
      return t;
    }
    function send(cmd) {
      const box = document.querySelector("#mp-cmd textarea, #mp-cmd input");
      const go = document.querySelector("#mp-cmd-go");
      if (!box || !go) { status.textContent = "Couldn't save: reload the page and try again."; return false; }
      const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(box), "value").set;
      setter.call(box, JSON.stringify(cmd));
      box.dispatchEvent(new Event("input", { bubbles: true }));
      setTimeout(() => go.click(), 60);
      saveB.disabled = discardB.disabled = resetB.disabled = true;
      status.textContent = "Saving...";
      return true;
    }
    saveB.addEventListener("click", () => {
      const add = [], remove = [];
      pending.forEach((p) => (p.op === "add" ? add : remove).push(...p.items));
      onSave();
      send({ key: root.dataset.key, thresholds: F.scored ? thresholds() : null, add, remove });
    }, sig);
    discardB.addEventListener("click", discard, sig);
    let resetArmed = null;
    resetB.addEventListener("click", () => {      // two clicks instead of a confirm() dialog
      if (!resetArmed) {
        resetB.textContent = "Click again to reset";
        resetArmed = setTimeout(() => { resetArmed = null; resetB.textContent = "Back to the AI's version"; }, 3000);
        return;
      }
      clearTimeout(resetArmed); resetArmed = null;
      onSave();
      send({ key: root.dataset.key, reset: true });
    }, sig);

    // ---------- open / close ----------
    let open = false;
    function setOpen(v) {
      open = v;
      panel.hidden = !v;
      sheet.classList.toggle("mp-editing", v);
      btn.setAttribute("aria-pressed", String(v));
      if (onToggle) onToggle(v);
    }
    btn.addEventListener("click", () => setOpen(!open), sig);
    update();
    return {
      setOpen, isOpen: () => open,
      pendingCount: () => pending.size,       // tests
      slotTime,                                 // tests
    };
  };
})();
