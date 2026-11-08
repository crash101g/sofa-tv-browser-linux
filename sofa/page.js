// Sofa TV Browser for Linux: the script that runs in every frame, in its own script world ("sofa"), so pages
// cannot see or change it. It carries the remote's pointer into the page (hover, click, drag, scroll at a point,
// across frames), finds videos for Watch, and tells Sofa when a text field takes the keyboard.
// SPDX-License-Identifier: GPL-3.0-only
(function () {
  "use strict";
  if (window.__sofa) return;
  const TOKEN = "__TOKEN__";
  const isTop = window === window.top;
  const post = (msg) => { try { window.webkit.messageHandlers.sofa.postMessage(msg); } catch (e) {} };
  let hovered = null;
  let lastInput = 0;
  let downEl = null;

  const editable = (el) => {
    if (!el) return false;
    if (el.isContentEditable) return true;
    if (el.tagName === "TEXTAREA") return !el.readOnly && !el.disabled;
    if (el.tagName === "INPUT") {
      const t = (el.type || "text").toLowerCase();
      return !el.readOnly && !el.disabled &&
        ["text", "search", "email", "url", "password", "tel", "number", "date", "datetime-local", "month",
         "time", "week"].includes(t);
    }
    return false;
  };

  // The element at (x, y) in this frame, or the frame element there with the point in its own coordinates.
  function at(x, y) {
    let el = document.elementFromPoint(x, y);
    while (el && el.shadowRoot) {
      const inner = el.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === el) break;
      el = inner;
    }
    if (el && (el.tagName === "IFRAME" || el.tagName === "FRAME") && el.contentWindow) {
      const r = el.getBoundingClientRect();
      return { frame: el, x: x - r.left - el.clientLeft, y: y - r.top - el.clientTop };
    }
    return { el };
  }

  function relay(frame, msg) {
    try { frame.contentWindow.postMessage(Object.assign({ sofa: TOKEN }, msg), "*"); } catch (e) {}
  }

  function mouse(el, type, x, y, buttons) {
    const init = { bubbles: true, cancelable: true, composed: true, view: window, clientX: x, clientY: y,
                   screenX: x, screenY: y, button: 0, buttons: buttons || 0 };
    const isPointer = type.startsWith("pointer");
    const ev = isPointer
      ? new PointerEvent(type, Object.assign({ pointerId: 1, pointerType: "mouse", isPrimary: true }, init))
      : new MouseEvent(type, init);
    el.dispatchEvent(ev);
  }

  function hover(x, y, buttons) {
    const t = at(x, y);
    if (t.frame) { relay(t.frame, { op: "hover", x: t.x, y: t.y, buttons }); return; }
    const el = t.el;
    if (!el) return;
    if (el !== hovered) {
      if (hovered && hovered.isConnected) {
        mouse(hovered, "pointerout", x, y, buttons); mouse(hovered, "mouseout", x, y, buttons);
        mouse(hovered, "pointerleave", x, y, buttons); mouse(hovered, "mouseleave", x, y, buttons);
      }
      mouse(el, "pointerover", x, y, buttons); mouse(el, "mouseover", x, y, buttons);
      mouse(el, "pointerenter", x, y, buttons); mouse(el, "mouseenter", x, y, buttons);
      hovered = el;
    }
    mouse(el, "pointermove", x, y, buttons); mouse(el, "mousemove", x, y, buttons);
  }

  function focusable(el) {
    for (let e = el; e && e !== document.body; e = e.parentElement) {
      if (e.tabIndex >= 0 || editable(e)) return e;
    }
    return null;
  }

  function press(x, y, phase) {
    const t = at(x, y);
    if (t.frame) { relay(t.frame, { op: phase, x: t.x, y: t.y }); return; }
    const el = t.el;
    if (!el) return;
    lastInput = Date.now();
    if (phase === "down" || phase === "click") {
      downEl = el;
      mouse(el, "pointerdown", x, y, 1);
      mouse(el, "mousedown", x, y, 1);
      const f = focusable(el);
      if (f) { try { f.focus({ preventScroll: true }); } catch (e) {} }
      else if (document.activeElement && editable(document.activeElement)) document.activeElement.blur();
    }
    if (phase === "up" || phase === "click") {
      const target = phase === "click" ? el : (downEl && downEl.isConnected ? el : el);
      mouse(target, "pointerup", x, y, 0);
      mouse(target, "mouseup", x, y, 0);
      if (phase === "click" || target === downEl || (downEl && downEl.contains(target))) {
        const sel = target.closest && target.closest("select");
        if (sel && !sel.disabled) { pick(sel); return; }
        mouse(target, "click", x, y, 0);
        const lbl = target.closest && target.closest("label");
        if (lbl && lbl.control && editable(lbl.control)) lbl.control.focus();
      }
      downEl = null;
      const a = document.activeElement;
      if (editable(a)) post({ type: "focus", editable: true });
    }
  }

  // A <select> cannot be opened by a script: Sofa shows its options itself.
  let picking = null;
  function pick(sel) {
    picking = sel;
    const options = Array.from(sel.options).map((o, i) => ({ i, label: o.label || o.text, disabled: o.disabled }));
    post({ type: "select", options, selected: sel.selectedIndex, token: TOKEN });
  }

  function scrollable(el, dx, dy) {
    for (let e = el; e && e !== document.documentElement; e = e.parentElement) {
      if (e === document.body) break;
      const s = getComputedStyle(e);
      const oy = s.overflowY, ox = s.overflowX;
      if (dy && /(auto|scroll|overlay)/.test(oy) && e.scrollHeight > e.clientHeight + 1) {
        if ((dy > 0 && e.scrollTop + e.clientHeight < e.scrollHeight - 1) || (dy < 0 && e.scrollTop > 0)) return e;
      }
      if (dx && /(auto|scroll|overlay)/.test(ox) && e.scrollWidth > e.clientWidth + 1) {
        if ((dx > 0 && e.scrollLeft + e.clientWidth < e.scrollWidth - 1) || (dx < 0 && e.scrollLeft > 0)) return e;
      }
    }
    return document.scrollingElement || document.documentElement;
  }

  function scroll(x, y, dx, dy) {
    const t = at(x, y);
    if (t.frame) { relay(t.frame, { op: "scroll", x: t.x, y: t.y, dx, dy }); return; }
    const target = scrollable(t.el, dx, dy);
    target.scrollBy({ left: dx, top: dy, behavior: "instant" });
  }

  function hit(x, y) {
    const t = at(x, y);
    if (t.frame) { relay(t.frame, { op: "hit", x: t.x, y: t.y }); return; }
    const el = t.el;
    if (!el) { post({ type: "hit" }); return; }
    const a = el.closest && el.closest("a[href]");
    const img = el.closest && el.closest("img");
    const vid = el.closest && el.closest("video");
    post({
      type: "hit",
      link: a ? a.href : null,
      text: a ? (a.innerText || a.title || "").trim().slice(0, 200) : null,
      image: img ? (img.currentSrc || img.src) : null,
      video: vid ? (vid.currentSrc || vid.src || null) : null,
      frameUrl: location.href,
    });
  }

  // Watch: every frame reports its largest video.
  function videos() {
    return Array.from(document.querySelectorAll("video")).filter((v) => {
      const r = v.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && getComputedStyle(v).visibility !== "hidden";
    });
  }

  function largest() {
    let best = null, area = 0;
    for (const v of videos()) {
      const r = v.getBoundingClientRect();
      if (r.width * r.height > area) { area = r.width * r.height; best = v; }
    }
    return best;
  }

  function describe(v) {
    const r = v.getBoundingClientRect();
    const src = v.currentSrc || v.src || (v.querySelector("source") || {}).src || "";
    const tracks = Array.from(v.querySelectorAll("track[src]")).map((t) => ({
      src: t.src, lang: t.srclang || "", label: t.label || "", kind: t.kind || "subtitles" }));
    return {
      type: "video", top: isTop, frameUrl: location.href, title: document.title,
      width: r.width, height: r.height, src, tracks,
      duration: isFinite(v.duration) ? v.duration : (v.duration === Infinity ? -1 : 0),
      position: v.currentTime || 0, muted: v.muted || v.volume === 0, playing: !v.paused, loop: v.loop,
      mse: src.startsWith("blob:") || src.startsWith("mediasource:"),
    };
  }

  function locate() {
    const v = largest();
    if (v) post(describe(v));
    for (const f of document.querySelectorAll("iframe, frame")) relay(f, { op: "locate" });
  }

  function allFrames(op) {
    for (const f of document.querySelectorAll("iframe, frame")) relay(f, { op });
  }

  function pauseAll() {
    for (const v of document.querySelectorAll("video, audio")) { try { v.pause(); } catch (e) {} }
    allFrames("pause");
  }

  function resume() {
    const v = largest();
    if (v) { try { v.play(); } catch (e) {} }
    allFrames("resume");
  }

  // The fallback when no player can open the stream: the video fills the window, in its frame and every frame above.
  const FS_STYLE = "position:fixed!important;inset:0!important;width:100vw!important;height:100vh!important;" +
    "max-width:none!important;max-height:none!important;z-index:2147483647!important;background:#000!important;" +
    "margin:0!important;border:0!important;transform:none!important;object-fit:contain!important";
  let expanded = [];
  function expand(el) {
    expanded.push([el, el.getAttribute("style")]);
    el.setAttribute("style", (el.getAttribute("style") || "") + ";" + FS_STYLE);
  }
  function fill() {
    const v = largest();
    if (!v) return false;
    try { if (v.requestFullscreen) { v.requestFullscreen().catch(() => {}); } } catch (e) {}
    expand(v);
    v.controls = true;
    try { v.play(); } catch (e) {}
    if (!isTop) window.parent.postMessage({ sofa: TOKEN, op: "expandme" }, "*");
    return true;
  }
  function unfill() {
    for (const [el, style] of expanded.reverse()) {
      if (style === null) el.removeAttribute("style"); else el.setAttribute("style", style);
    }
    expanded = [];
    allFrames("unfill");
  }

  window.addEventListener("message", (e) => {
    const d = e.data;
    if (!d || d.sofa !== TOKEN) return;
    switch (d.op) {
      case "hover": hover(d.x, d.y, d.buttons); break;
      case "down": case "up": case "click": press(d.x, d.y, d.op); break;
      case "dbl": dbl(d.x, d.y); break;
      case "enter": enter(); break;
      case "focusAt": focusAt(d.x, d.y); break;
      case "media": media(d.cmd, false); break;
      case "scroll": scroll(d.x, d.y, d.dx, d.dy); break;
      case "hit": hit(d.x, d.y); break;
      case "locate": locate(); break;
      case "pause": pauseAll(); break;
      case "resume": resume(); break;
      case "fill": fill() || allFrames("fill"); break;
      case "unfill": unfill(); break;
      case "expandme":
        for (const f of document.querySelectorAll("iframe, frame")) {
          if (f.contentWindow === e.source) {
            expand(f);
            if (!isTop) window.parent.postMessage({ sofa: TOKEN, op: "expandme" }, "*");
          }
        }
        break;
      case "blur": if (document.activeElement) document.activeElement.blur(); allFrames("blur"); break;
    }
  }, true);

  // The keyboard belongs to a text field while it has focus, whether the pointer or a real mouse put it there.
  document.addEventListener("focusin", (e) => { if (editable(e.target)) post({ type: "focus", editable: true }); }, true);
  document.addEventListener("focusout", (e) => { if (editable(e.target)) post({ type: "focus", editable: false }); }, true);
  for (const t of ["pointerdown", "keydown"]) document.addEventListener(t, (e) => { if (e.isTrusted) lastInput = Date.now(); }, true);

  // "Open videos in Sofa Player": a video the user starts, with sound, a minute or longer, about the player's size.
  document.addEventListener("play", (e) => {
    const v = e.target;
    if (!(v instanceof HTMLVideoElement) || Date.now() - lastInput > 3000) return;
    const check = () => {
      const r = v.getBoundingClientRect();
      const big = r.width * r.height >= 0.2 * window.innerWidth * window.innerHeight || r.width >= 480;
      if (!v.muted && v.volume > 0 && !v.loop && big && (v.duration >= 60 || v.duration === Infinity)) {
        post(Object.assign(describe(v), { type: "started" }));
      }
    };
    if (isNaN(v.duration)) v.addEventListener("loadedmetadata", check, { once: true }); else check();
  }, true);

  function dbl(x, y) {
    const t = at(x, y);
    if (t.frame) { relay(t.frame, { op: "dbl", x: t.x, y: t.y }); return; }
    press(x, y, "click");
    press(x, y, "click");
    if (t.el) mouse(t.el, "dblclick", x, y, 0);
  }

  // ---- typing from the phone -------------------------------------------------------------------------------
  // Makes sure something takes the text; Sofa then inserts it into whichever frame has focus.
  function deepActive() {
    let el = document.activeElement;
    while (el && el.shadowRoot && el.shadowRoot.activeElement) el = el.shadowRoot.activeElement;
    return el;
  }

  function focusAt(x, y) {
    const t = at(x, y);
    if (t.frame) { try { t.frame.focus(); } catch (e) {} relay(t.frame, { op: "focusAt", x: t.x, y: t.y }); return; }
    const f = t.el && focusable(t.el);
    if (f && editable(f)) { try { f.focus({ preventScroll: true }); } catch (e) {} }
  }

  function prepareType(x, y) {
    const a = deepActive();
    if (editable(a)) return "ok";
    if (a && (a.tagName === "IFRAME" || a.tagName === "FRAME")) return "iframe";
    if (x === null || x === undefined) return "nofield";
    const t = at(x, y);
    if (t.frame) { focusAt(x, y); return "iframe"; }
    const f = t.el && focusable(t.el);
    if (f && editable(f)) { try { f.focus({ preventScroll: true }); } catch (e) {} return "ok"; }
    return "nofield";
  }

  // Enter from the phone: in the frame that has the focus, as a press of Enter, and the form is sent if the page
  // doesn't take the key itself (scripts can't make a real key press).
  function enter() {
    for (const f of document.querySelectorAll("iframe, frame")) relay(f, { op: "enter" });
    if (!document.hasFocus()) return;
    const el = deepActive();
    if (!editable(el)) return;
    const init = { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true, composed: true };
    const down = new KeyboardEvent("keydown", init);
    const taken = !el.dispatchEvent(down);
    el.dispatchEvent(new KeyboardEvent("keypress", init));
    el.dispatchEvent(new KeyboardEvent("keyup", init));
    if (taken) return;
    if (el.tagName === "INPUT") {
      if (el.form) { try { el.form.requestSubmit(); } catch (e) { el.form.submit(); } }
    } else {
      document.execCommand("insertText", false, "\n");
    }
  }

  // ---- the page's own video, from the phone's play/pause --------------------------------------------------
  function media(cmd, relayToFrames) {
    const vids = Array.from(document.querySelectorAll("video, audio"));
    const v = vids.find((m) => !m.paused) || largest();
    if (v) {
      try {
        if (cmd === "playpause") { if (v.paused) v.play(); else v.pause(); }
        else if (cmd === "pause") v.pause();
      } catch (e) {}
    }
    if (relayToFrames !== false) for (const f of document.querySelectorAll("iframe, frame")) relay(f, { op: "media", cmd });
  }

  // ---- clean up: overlays, cookie walls, sticky ad bars, scroll locks (from TVCouchBrowser) ----------------
  const ANNOY = /(^|[-_\s])(ad|ads|advert|adsbox|banner|sponsor|promo|cookie|consent|gdpr|cmp|newsletter|subscribe|popup|pop-up|modal|overlay|interstitial|paywall|lightbox|backdrop)([-_\s]|$)/i;
  const nameOf = (el) => (el.id || "") + " " + (typeof el.className === "string" ? el.className : "");

  function unlockScroll() {
    let changed = false;
    for (const el of [document.documentElement, document.body]) {
      if (!el) continue;
      const st = getComputedStyle(el);
      if (st.overflow === "hidden" || st.overflowY === "hidden") {
        el.style.setProperty("overflow", "auto", "important");
        el.style.setProperty("overflow-y", "auto", "important");
        changed = true;
      }
      if (el === document.body && st.position === "fixed") {
        // Sites lock scrolling by fixing the body at -scrollY; put it back where it was.
        const y = -parseInt(el.style.top || "0", 10) || 0;
        el.style.setProperty("position", "static", "important");
        el.style.setProperty("top", "auto", "important");
        if (y) window.scrollTo(0, y);
        changed = true;
      }
    }
    // Paywalls and cookie walls blur the article behind them.
    for (const el of document.querySelectorAll("body, body > *, main, article")) {
      const f = getComputedStyle(el).filter;
      if (f && f.includes("blur")) { el.style.setProperty("filter", "none", "important"); changed = true; }
    }
    return changed;
  }

  function cleanup() {
    let n = 0;
    const vw = innerWidth, vh = innerHeight;
    const links = document.querySelectorAll("a[href]").length;
    const fs = document.fullscreenElement;
    for (const el of document.querySelectorAll("body *")) {
      if (!el.isConnected) continue;
      const st = getComputedStyle(el);
      if (st.position !== "fixed" && st.position !== "sticky") continue;
      if (fs && (el.contains(fs) || fs.contains(el))) continue;
      if (expanded.some(([x]) => x === el || el.contains(x))) continue; // Watch's filled video
      const r = el.getBoundingClientRect();
      const w = Math.max(0, Math.min(r.right, vw) - Math.max(r.left, 0));
      const h = Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));
      if (!w || !h) continue;
      const cover = (w * h) / (vw * vh);
      // Never take the page's own layout: something holding most of its links is the site itself.
      const own = el.querySelectorAll("a[href]").length;
      if (links > 20 && own > links * 0.5) continue;
      const big = cover >= 0.3 && st.position === "fixed";
      const annoying = ANNOY.test(nameOf(el)) && own <= 8;
      const adFrame = cover < 0.3 && own <= 3 && el.querySelector("iframe") && (r.top <= 0 || r.bottom >= vh - 1);
      if (big || annoying || adFrame) { el.remove(); n++; }
    }
    if (unlockScroll()) n++;
    return n;
  }

  // ---- remove element, and a selector to remember it by (from TVCouchBrowser) -----------------------------
  const stableName = (x) => /^[A-Za-z_][\w-]{1,40}$/.test(x) && !/\d{4,}/.test(x) && !/^(css|sc|jsx|emotion)-/.test(x);

  function selectorFor(el) {
    const tag = el.tagName.toLowerCase();
    let sel = "";
    if (el.id && stableName(el.id)) sel = "#" + CSS.escape(el.id);
    else if (tag === "iframe" && el.src) {
      try {
        const u = new URL(el.src, location.href);
        if (u.hostname && u.hostname !== location.hostname) sel = 'iframe[src*="' + u.hostname.replace(/"/g, "") + '"]';
      } catch (e) {}
    }
    if (!sel) {
      const cls = Array.from(el.classList).filter(stableName).slice(0, 3);
      if (!cls.length) return "";
      sel = tag + cls.map((c) => "." + CSS.escape(c)).join("");
      const p = el.parentElement;
      if (p && p.id && stableName(p.id)) sel = "#" + CSS.escape(p.id) + " > " + sel;
    }
    // Only remember selectors that pick out this element and few others.
    try {
      const count = document.querySelectorAll(sel).length;
      return count >= 1 && count <= 3 ? sel : "";
    } catch (e) { return ""; }
  }

  const areaOf = (e) => { const r = e.getBoundingClientRect(); return r.width * r.height; };

  // Returns "" (nothing there), "-" (removed, not remembered) or the selector to remember.
  function zapAt(x, y) {
    let el = document.elementFromPoint(x, y);
    if (!el || el === document.body || el === document.documentElement) return "";
    // Climb out of wrappers that are barely bigger than what was pointed at.
    while (el.parentElement && el.parentElement !== document.body && el.parentElement !== document.documentElement &&
      areaOf(el.parentElement) <= areaOf(el) * 1.25) el = el.parentElement;
    const sel = selectorFor(el);
    el.remove();
    unlockScroll();
    return sel || "-";
  }

  window.__sofa = {
    hover, scroll, hit, locate, pauseAll, resume, fill, unfill, dbl, prepareType, enter, media, cleanup, zapAt,
    click: (x, y) => press(x, y, "click"),
    down: (x, y) => press(x, y, "down"),
    up: (x, y) => press(x, y, "up"),
    blur: () => { if (document.activeElement) document.activeElement.blur(); allFrames("blur"); },
    choose: (i) => {
      if (!picking) return;
      picking.selectedIndex = i;
      picking.dispatchEvent(new Event("input", { bubbles: true }));
      picking.dispatchEvent(new Event("change", { bubbles: true }));
      picking = null;
    },
    pageScroll: (dy) => window.scrollBy({ top: dy * window.innerHeight * 0.85, behavior: "smooth" }),
  };
  // A <select> answer goes to every frame; only the one that asked has one open.
  window.__sofa.chooseAll = (i) => {
    window.__sofa.choose(i);
    for (const f of document.querySelectorAll("iframe, frame")) relay(f, { op: "choose", i });
  };
  window.addEventListener("message", (e) => {
    const d = e.data;
    if (d && d.sofa === TOKEN && d.op === "choose") window.__sofa.chooseAll(d.i);
  }, true);
})();
