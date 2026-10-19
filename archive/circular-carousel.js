// Vanilla port of React Bits' <CircularCarousel /> (https://reactbits.dev).
// Same presets, intro animations, drag/flick physics, snapping, parallax,
// curved (tiled) cards, captions and keyboard support as the React original.
//
//   const cc = CircularCarousel(container, {
//     items: [{ src, alt, title, subtitle }], preset: "cylinder", intro: "rise",
//     cardWidth: 220, aspectRatio: 1, captions: true, fadeColor: "#fff",
//     onChange: i => {}, onItemClick: (item, i) => {},
//   });
//   cc.setItems(items); cc.focusIndex(2); cc.stepBy(1); cc.active; cc.destroy();

(function () {
  "use strict";

  const PRESETS = {
    cylinder: { axis: "y", tilt: -5, perspective: 2500, curve: 1, spread: 1, inward: false, billboard: false, backfaces: true, window: 0 },
    orbit: { axis: "y", tilt: -16, perspective: 1500, curve: 0, spread: 1.45, inward: false, billboard: true, backfaces: false, window: 0 },
    wheel: { axis: "x", tilt: 0, perspective: 1800, curve: 0, spread: 1, inward: false, billboard: false, backfaces: true, window: 1.7 },
    panorama: { axis: "y", tilt: 0, perspective: 0, curve: 1, spread: 1, inward: true, billboard: false, backfaces: false, window: 0 },
  };
  const INTRO_LENGTH = { assemble: 1500, rise: 1400, spin: 1800, none: 0 };
  const TILES = 8, OVERLAP = 2.5, DRAG_THRESHOLD = 5, SPRING = 118, SETTLE_SPEED = 9, CAPTION_SPACE = 76;
  const TO_RAD = Math.PI / 180;

  const clamp = (v, min, max) => Math.min(max, Math.max(min, v));
  const wrap = d => ((((d + 180) % 360) + 360) % 360) - 180;
  const easeOut = t => 1 - Math.pow(1 - t, 4);
  const easeOutQuint = t => 1 - Math.pow(1 - t, 5);
  const rotateX = (p, deg) => { const r = deg * TO_RAD, c = Math.cos(r), s = Math.sin(r); return [p[0], p[1] * c - p[2] * s, p[1] * s + p[2] * c]; };
  const rotateY = (p, deg) => { const r = deg * TO_RAD, c = Math.cos(r), s = Math.sin(r); return [p[0] * c + p[2] * s, p[1], -p[0] * s + p[2] * c]; };
  const el = (tag, cls) => { const e = document.createElement(tag); if (cls) e.className = cls; return e; };
  const px = obj => Object.fromEntries(Object.entries(obj).map(([k, v]) => [k, typeof v === "number" ? v + "px" : v]));

  function CircularCarousel(root, options = {}) {
    const o = Object.assign({
      items: [], preset: "cylinder", intro: "rise", cardWidth: 220, aspectRatio: 1, gap: 25,
      curve: undefined, tilt: undefined, perspective: undefined,
      autoplay: "drift", speed: 14, interval: 3, direction: "left", draggable: true, momentum: 0.6,
      snap: true, pauseOnHover: true, focusOnClick: true, parallax: 0.3, stretch: 0.5, depthFade: 0.55,
      fadeColor: "#000000", innerShade: 0.6, cornerRadius: 12, captions: false,
      onChange: null, onItemClick: null, className: "",
    }, options);

    // ---------- DOM ----------
    root.classList.add("circular-carousel");
    o.className.split(" ").filter(Boolean).forEach(c => root.classList.add(c));
    root.setAttribute("role", "region");
    root.setAttribute("aria-roledescription", "carousel");
    root.setAttribute("aria-label", "Image carousel");
    root.tabIndex = 0;
    const view = el("div", "circular-carousel__view");
    const stage = el("div", "circular-carousel__stage");
    const camera = el("div", "circular-carousel__camera");
    const ring = el("div", "circular-carousel__ring");
    camera.appendChild(ring); stage.appendChild(camera); view.appendChild(stage); root.appendChild(view);
    const caption = el("div", "circular-carousel__caption");
    caption.setAttribute("aria-hidden", "true");
    const live = el("div", "circular-carousel__live");
    live.setAttribute("aria-live", "polite"); live.setAttribute("aria-atomic", "true");
    root.appendChild(live);

    // ---------- state ----------
    const reducedQuery = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
    const state = {
      angle: 0, velocity: 0, target: null, dir: 0, press: null, drag: false, hover: false,
      pointer: { inside: false, x: 0, y: 0 }, yaw: 0, pitch: 0, intro: null, introDone: false,
      holdUntil: 0, stepAt: 0, suppressClick: false, wheelTimer: 0, fit: 1, shift: 0, drop: 0, last: 0,
    };
    let list = [], cards = [], s = null, lastDirSign = null;
    let active = 0, ready = false, raf = 0, visible = true, destroyed = false, loadToken = 0;
    let captionTitle = null, digitsEl = null, totalEl = null;

    // ---------- geometry / settings ----------
    function compute() {
      const count = list.length;
      const shape = PRESETS[o.preset] ? o.preset : "cylinder";
      const layout = PRESETS[shape];
      const axis = layout.axis;
      const tilt = o.tilt ?? layout.tilt;
      const curve = layout.billboard ? 0 : clamp(o.curve ?? layout.curve, 0, 1);
      const reduced = !!reducedQuery?.matches;
      const cardW = Math.max(40, o.cardWidth);
      const cardH = cardW / clamp(o.aspectRatio, 0.2, 5);
      const along = axis === "x" ? cardH : cardW;
      const step = 360 / Math.max(count, 1);
      const n = Math.max(count, 3);
      const pitch = (along + o.gap) * layout.spread;
      const chord = pitch / (2 * Math.sin(Math.PI / n));
      const arc = (n * pitch) / (2 * Math.PI);
      const radius = Math.max(chord + (arc - chord) * curve, along * 0.6);

      const total = curve > 0.001 ? TILES : 1;
      const length = along / total;
      const bend = curve > 0.001 ? radius / curve : 0;
      const tiles = Array.from({ length: total }, (_, index) => {
        const start = index * length - (index > 0 ? OVERLAP / 2 : 0);
        const end = (index + 1) * length + (index < total - 1 ? OVERLAP / 2 : 0);
        const center = (start + end) / 2 - along / 2;
        const alpha = bend ? center / bend : 0;
        const shift = bend ? bend * Math.sin(alpha) : center;
        const sink = bend ? bend * (1 - Math.cos(alpha)) : 0;
        const depth = layout.inward ? sink : -sink;
        const turn = ((layout.inward ? -alpha : alpha) * 180) / Math.PI;
        const move = axis === "x"
          ? `translate3d(0px, ${shift}px, ${depth}px) rotateX(${-turn}deg)`
          : `translate3d(${shift}px, 0px, ${depth}px) rotateY(${turn}deg)`;
        return { index, total, start, end, size: end - start, move };
      });

      s = {
        count, step, radius, layout, axis, shape, tilt, cardW, cardH, along, tiles,
        perspective: layout.inward ? radius : (o.perspective ?? layout.perspective),
        intro: reduced ? "none" : (o.intro in INTRO_LENGTH ? o.intro : "rise"),
        autoplay: reduced ? "off" : o.autoplay,
        speed: o.speed, interval: Math.max(0.5, o.interval), draggable: o.draggable,
        momentum: clamp(o.momentum, 0, 1), snap: o.snap, pauseOnHover: o.pauseOnHover,
        parallax: reduced ? 0 : clamp(o.parallax, 0, 1), stretch: reduced ? 0 : clamp(o.stretch, 0, 1),
        depthFade: clamp(o.depthFade, 0, 1), captions: o.captions, reduced,
      };
      const dirSign = (o.direction === "right" ? 1 : -1) * (layout.inward ? -1 : 1);
      if (dirSign !== lastDirSign) { state.dir = dirSign; lastDirSign = dirSign; }

      root.dataset.axis = axis; root.dataset.shape = shape;
      if (o.draggable) root.dataset.draggable = ""; else delete root.dataset.draggable;
      root.style.setProperty("--cc-fade", o.fadeColor);
      root.style.setProperty("--cc-radius", `${Math.max(0, o.cornerRadius)}px`);
      root.style.setProperty("--cc-inner", (1 - clamp(o.innerShade, 0, 1)).toFixed(3));
    }

    function renderTile(item, tile, back) {
      const strip = back ? tile.total - 1 - tile.index : tile.index;
      const first = strip === 0, last = strip === tile.total - 1;
      const r = "var(--cc-radius)";
      const frameRadius = s.axis === "x"
        ? `${first ? r : 0} ${first ? r : 0} ${last ? r : 0} ${last ? r : 0}`
        : `${first ? r : 0} ${last ? r : 0} ${last ? r : 0} ${first ? r : 0}`;
      const offset = back ? s.along - tile.end : tile.start;
      const size = tile.size;
      const box = s.axis === "x"
        ? { left: -s.cardW / 2, top: -size / 2, width: s.cardW, height: size }
        : { left: -size / 2, top: -s.cardH / 2, width: size, height: s.cardH };
      const photoStyle = s.axis === "x"
        ? { left: 0, top: -offset, width: s.cardW, height: s.cardH }
        : { left: -offset, top: 0, width: s.cardW, height: s.cardH };
      const flip = s.axis === "x" ? " rotateX(180deg)" : " rotateY(180deg)";

      const tileEl = el("div", "circular-carousel__tile");
      tileEl.setAttribute("aria-hidden", "true");
      Object.assign(tileEl.style, px(box), { transform: tile.move + (back ? flip : "") });
      const frame = el("div", "circular-carousel__frame");
      frame.style.height = (s.axis === "x" ? size : s.cardH) + "px";
      frame.style.borderRadius = frameRadius;
      const img = el("img", "circular-carousel__photo");
      img.src = item.src; img.alt = ""; img.draggable = false; img.decoding = "async";
      Object.assign(img.style, px(photoStyle));
      frame.appendChild(img);
      if (back) frame.appendChild(el("div", "circular-carousel__inner"));
      frame.appendChild(el("div", "circular-carousel__shade"));
      tileEl.appendChild(frame);
      return tileEl;
    }

    function buildCards() {
      ring.innerHTML = "";
      cards = list.map((item, index) => {
        const card = el("div", "circular-carousel__card");
        card.dataset.ccIndex = index;
        card.setAttribute("role", "group");
        card.setAttribute("aria-roledescription", "slide");
        card.setAttribute("aria-label", `${item.title || item.alt || `Image ${index + 1}`}, ${index + 1} of ${list.length}`);
        for (const tile of s.tiles) card.appendChild(renderTile(item, tile, false));
        if (s.layout.backfaces) for (const tile of s.tiles) card.appendChild(renderTile(item, tile, true));
        ring.appendChild(card);
        return card;
      });
    }

    // ---------- fit the ring into the container ----------
    function measure() {
      if (!s) return;
      const rect = root.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const room = s.captions ? CAPTION_SPACE : 0;
      const width = rect.width * 0.94;
      const height = (rect.height - room) * 0.92;
      const P = s.perspective;
      let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
      if (s.layout.inward) {
        minX = -width / 2; maxX = width / 2; minY = -s.cardH / 2; maxY = s.cardH / 2;
      } else {
        const corners = [[-s.cardW / 2, -s.cardH / 2], [s.cardW / 2, -s.cardH / 2], [-s.cardW / 2, s.cardH / 2], [s.cardW / 2, s.cardH / 2]];
        const limit = s.layout.window ? s.layout.window * s.step : 180;
        for (let a = -limit; a <= limit; a += limit / 24) {
          for (const [cx, cy] of corners) {
            let p;
            if (s.axis === "x") { p = rotateX([cx, cy, s.radius], -a); p = [p[0], p[1], p[2] - s.radius]; p = rotateY(p, s.tilt); }
            else if (s.layout.billboard) { const c = rotateY([0, 0, s.radius], a); p = [c[0] + cx, cy, c[2] - s.radius]; p = rotateX(p, s.tilt); }
            else { p = rotateY([cx, cy, s.radius], a); p = [p[0], p[1], p[2] - s.radius]; p = rotateX(p, s.tilt); }
            if (p[2] >= P * 0.95) continue;
            const k = P / (P - p[2]);
            minX = Math.min(minX, p[0] * k); maxX = Math.max(maxX, p[0] * k);
            minY = Math.min(minY, p[1] * k); maxY = Math.max(maxY, p[1] * k);
          }
        }
      }
      const spanX = Math.max(maxX - minX, 1), spanY = Math.max(maxY - minY, 1);
      const fit = Math.min(1, width / spanX, height / spanY);
      state.fit = fit;
      state.shift = -((minY + maxY) / 2) * fit - room / 2;
      state.drop = s.axis === "x" ? (rect.width / fit) * 0.55 + s.cardW : (rect.height / fit) * 0.55 + s.cardH;
      stage.style.perspective = `${P}px`;
      stage.style.transform = `translate3d(0, ${state.shift}px, 0) scale(${fit})`;
    }

    // ---------- animation ----------
    const nearest = angle => Math.round(angle / s.step) * s.step;

    function introCard(elapsed, landing) {
      if (!state.intro) return { radius: 1, lift: 0 };
      const type = state.intro.type;
      const reach = Math.abs(wrap(landing + state.angle));
      if (type === "assemble") {
        const p = easeOut(clamp((elapsed - (reach / 180) * 420) / 1080, 0, 1));
        return { radius: 1 + 0.6 * (1 - p), lift: 0 };
      }
      if (type === "rise") {
        const p = easeOutQuint(clamp((elapsed - (reach / 180) * 480) / 900, 0, 1));
        return { radius: 1, lift: (1 - p) * state.drop };
      }
      if (type === "spin") {
        const p = easeOut(clamp(elapsed / INTRO_LENGTH.spin, 0, 1));
        return { radius: 1 + 0.28 * (1 - p), lift: 0 };
      }
      return { radius: 1, lift: 0 };
    }

    function advance(dt, now) {
      if (!state.introDone && ready) {
        if (!state.intro) {
          if (s.intro === "none") state.introDone = true;
          else state.intro = { type: s.intro, start: now };
        }
        if (state.intro && now - state.intro.start >= INTRO_LENGTH[state.intro.type]) { state.intro = null; state.introDone = true; }
      }
      const paused = (s.pauseOnHover && state.hover) || state.drag || now < state.holdUntil;
      const cruise = s.autoplay === "drift" && !paused && !state.intro ? s.speed * state.dir : 0;
      let busy = Boolean(state.intro) || state.drag;

      if (state.drag || state.intro) {
        state.velocity = state.drag ? state.velocity : 0;
      } else if (state.target !== null) {
        let remaining = dt;
        const damping = 2 * Math.sqrt(SPRING);
        while (remaining > 0) {
          const h = Math.min(remaining, 1 / 240);
          const accel = SPRING * (state.target - state.angle) - damping * state.velocity;
          state.velocity += accel * h;
          state.angle += state.velocity * h;
          remaining -= h;
        }
        if (Math.abs(state.target - state.angle) < 0.004 && Math.abs(state.velocity) < 0.03) {
          state.angle = state.target; state.velocity = 0; state.target = null;
        }
        busy = true;
      } else {
        const tau = 0.18 + s.momentum * 1.5;
        state.velocity += (cruise - state.velocity) * (1 - Math.exp(-dt / tau));
        state.angle += state.velocity * dt;
        if (cruise === 0 && s.snap && Math.abs(state.velocity) < SETTLE_SPEED) state.target = nearest(state.angle);
        busy = busy || cruise !== 0 || Math.abs(state.velocity) > 0.01 || state.target !== null;
      }

      if (s.autoplay === "step" && !paused && !state.intro && state.introDone) {
        if (!state.stepAt) state.stepAt = now + s.interval * 1000;
        if (now >= state.stepAt) {
          state.target = (state.target ?? nearest(state.angle)) + s.step * state.dir;
          state.stepAt = now + s.interval * 1000;
        }
        busy = true;
      } else state.stepAt = 0;

      if (now < state.holdUntil) busy = true;

      const ease = 1 - Math.exp(-dt / 0.35);
      const aimYaw = state.pointer.inside ? state.pointer.x * s.parallax * 9 : 0;
      const aimPitch = state.pointer.inside ? -state.pointer.y * s.parallax * 6 : 0;
      state.yaw += (aimYaw - state.yaw) * ease;
      state.pitch += (aimPitch - state.pitch) * ease;
      if (Math.abs(aimYaw - state.yaw) > 0.01 || Math.abs(aimPitch - state.pitch) > 0.01) busy = true;
      return busy;
    }

    function render(now) {
      const elapsed = state.intro ? now - state.intro.start : 0;
      const swell = 1 + s.stretch * 0.12 * Math.min(1, Math.abs(state.velocity) / 420);
      let spinOffset = 0;
      if (state.intro?.type === "spin") spinOffset = -300 * state.dir * (1 - easeOut(clamp(elapsed / INTRO_LENGTH.spin, 0, 1)));
      else if (state.intro?.type === "assemble") spinOffset = -32 * state.dir * (1 - easeOut(clamp(elapsed / INTRO_LENGTH.assemble, 0, 1)));
      const angle = state.angle + spinOffset;
      const R = s.radius * swell;

      if (s.axis === "x") {
        camera.style.transform = `translate3d(0, 0, ${-R}px) rotateY(${s.tilt + state.yaw}deg) rotateX(${state.pitch}deg)`;
        ring.style.transform = `rotateX(${-angle}deg)`;
      } else if (s.layout.inward) {
        camera.style.transform = `translate3d(0, 0, ${s.perspective - 1}px) rotateX(${s.tilt + state.pitch}deg) rotateY(${state.yaw}deg)`;
        ring.style.transform = `rotateY(${angle}deg)`;
      } else {
        camera.style.transform = `translate3d(0, 0, ${-R}px) rotateX(${s.tilt + state.pitch}deg) rotateY(${state.yaw}deg)`;
        ring.style.transform = `rotateY(${angle}deg)`;
      }

      for (let index = 0; index < s.count; index++) {
        const card = cards[index];
        if (!card) continue;
        const base = index * s.step;
        const mod = introCard(elapsed, base);
        const r = R * mod.radius;
        let transform;
        if (s.axis === "x") transform = `rotateX(${-base}deg) translateZ(${r}px)`;
        else if (s.layout.inward) transform = `rotateY(${base}deg) translateZ(${-r}px)`;
        else {
          transform = `rotateY(${base}deg) translateZ(${r}px)`;
          if (s.layout.billboard) transform += ` rotateY(${-(base + angle)}deg)`;
        }
        if (mod.lift) transform += s.axis === "x" ? ` translateX(${mod.lift}px)` : ` translateY(${mod.lift}px)`;
        card.style.transform = transform;
        const world = wrap(base + angle);
        const facing = Math.cos(world * TO_RAD);
        if (s.layout.inward) card.style.visibility = Math.abs(world) > 86 ? "hidden" : "";
        card.style.setProperty("--cc-depth", (s.depthFade * Math.pow((1 - facing) / 2, 1.25)).toFixed(3));
      }

      if (s.count) {
        const index = ((Math.round(-state.angle / s.step) % s.count) + s.count) % s.count || 0;
        if (index !== active) { active = index; updateCaption(); updateLive(); o.onChange?.(index); }
      }
    }

    function frame(now) {
      raf = 0;
      if (!s) return;
      const dt = state.last ? Math.min((now - state.last) / 1000, 0.05) : 1 / 60;
      state.last = now;
      const busy = advance(dt, now);
      render(now);
      if (busy && visible && !document.hidden) raf = requestAnimationFrame(frame);
      else state.last = 0;
    }
    function wake() { if (!raf && visible && !document.hidden && !destroyed) raf = requestAnimationFrame(frame); }
    function sleep() { cancelAnimationFrame(raf); raf = 0; state.last = 0; }

    // ---------- captions / a11y ----------
    function buildCaption() {
      caption.innerHTML = ""; captionTitle = null;
      if (!o.captions || !list.length) { caption.remove(); return; }
      const count = el("span", "circular-carousel__count");
      digitsEl = el("span", "circular-carousel__digits");
      const slash = el("span", "circular-carousel__slash"); slash.textContent = "/";
      totalEl = el("span");
      count.append(digitsEl, slash, totalEl);
      caption.appendChild(count);
      root.insertBefore(caption, live);
      updateCaption();
    }
    function updateCaption() {
      if (!o.captions || !list.length) return;
      const current = list[active] || list[0];
      const title = el("span", "circular-carousel__title");
      title.appendChild(document.createTextNode(current.title || current.alt || ""));
      if (current.subtitle) { const sub = el("span", "circular-carousel__subtitle"); sub.textContent = current.subtitle; title.appendChild(sub); }
      if (captionTitle) captionTitle.replaceWith(title); else caption.prepend(title);
      captionTitle = title;
      const str = String(active + 1).padStart(2, "0");
      if (digitsEl.childElementCount !== str.length) {
        digitsEl.innerHTML = "";
        for (let i = 0; i < str.length; i++) {
          const d = el("span", "circular-carousel__digit"), reel = el("span", "circular-carousel__reel");
          for (const n of "0123456789") { const x = el("span"); x.textContent = n; reel.appendChild(x); }
          d.appendChild(reel); digitsEl.appendChild(d);
        }
      }
      [...digitsEl.children].forEach((d, i) => { d.firstChild.style.transform = `translateY(${-Number(str[i]) * 10}%)`; });
      totalEl.textContent = String(list.length).padStart(2, "0");
    }
    function updateLive() {
      const current = list[active] || list[0];
      const label = current ? current.title || current.alt || `Image ${active + 1}` : "";
      live.textContent = `${label}, ${active + 1} of ${list.length}`;
    }

    // ---------- images ----------
    function load() {
      const token = ++loadToken;
      ready = false; delete root.dataset.ready;
      const sources = list.map(i => i.src).slice(0, 12);
      const one = src => new Promise(resolve => {
        const image = new Image();
        image.decoding = "async";
        image.onload = () => (image.decode ? image.decode().then(resolve, resolve) : resolve());
        image.onerror = resolve;
        image.src = src;
      });
      Promise.race([Promise.all(sources.map(one)), new Promise(r => setTimeout(r, 2400))]).then(() => {
        if (token !== loadToken || destroyed) return;
        state.introDone = false; state.intro = null;
        ready = true; root.dataset.ready = "";
        wake();
      });
    }

    // ---------- public actions ----------
    function focusIndex(index) {
      let target = -index * s.step;
      target += 360 * Math.round((state.angle - target) / 360);
      state.target = target;
      state.holdUntil = performance.now() + 2800;
      wake();
    }
    function stepBy(delta) {
      const base = state.target ?? Math.round(state.angle / s.step) * s.step;
      state.target = base - delta * s.step * (s.layout.inward ? -1 : 1);
      state.holdUntil = performance.now() + 2800;
      wake();
    }

    // ---------- input ----------
    const updatePointer = e => {
      const rect = root.getBoundingClientRect();
      state.pointer.x = clamp(((e.clientX - rect.left) / rect.width) * 2 - 1, -1, 1);
      state.pointer.y = clamp(((e.clientY - rect.top) / rect.height) * 2 - 1, -1, 1);
    };
    const onPointerDown = e => {
      state.suppressClick = false;
      if (!o.draggable || e.button !== 0) return;
      state.press = { id: e.pointerId, x: e.clientX, y: e.clientY, angle: state.angle, moved: false, origin: 0, samples: [{ time: performance.now(), angle: state.angle }] };
    };
    const onPointerMove = e => {
      if (e.pointerType === "mouse") { state.pointer.inside = true; updatePointer(e); }
      const press = state.press;
      if (!press || press.id !== e.pointerId) { wake(); return; }
      const delta = s.axis === "x" ? e.clientY - press.y : e.clientX - press.x;
      const cross = s.axis === "x" ? e.clientX - press.x : e.clientY - press.y;
      if (!press.moved) {
        if (Math.abs(delta) < DRAG_THRESHOLD) return;
        if (Math.abs(cross) > Math.abs(delta) * 1.2 && e.pointerType !== "mouse") { state.press = null; return; }
        press.moved = true; press.origin = delta;
        state.drag = true; state.target = null; state.velocity = 0;
        root.dataset.dragging = "";
        try { root.setPointerCapture(e.pointerId); } catch {}
      }
      const perPixel = 180 / (Math.PI * s.radius * state.fit);
      state.angle = press.angle + (delta - press.origin) * perPixel * (s.layout.inward ? -1 : 1);
      const now = performance.now();
      press.samples.push({ time: now, angle: state.angle });
      while (press.samples.length > 2 && now - press.samples[0].time > 110) press.samples.shift();
      wake();
    };
    const onPointerUp = e => {
      const press = state.press;
      if (!press || press.id !== e.pointerId) return;
      state.press = null;
      if (!press.moved) return;
      state.drag = false; delete root.dataset.dragging;
      state.suppressClick = true;
      const first = press.samples[0], last = press.samples[press.samples.length - 1];
      const span = (last.time - first.time) / 1000;
      const velocity = span > 0.008 ? clamp((last.angle - first.angle) / span, -1400, 1400) : 0;
      state.velocity = velocity;
      if (Math.abs(velocity) > 60) state.dir = Math.sign(velocity);
      const coasting = s.autoplay === "drift" && !(s.pauseOnHover && state.hover && e.pointerType === "mouse");
      if (s.snap && !coasting) {
        const tau = 0.18 + s.momentum * 1.5;
        state.target = Math.round((state.angle + velocity * tau * 0.55) / s.step) * s.step;
      }
      wake();
    };
    const onPointerEnter = e => { if (e.pointerType !== "mouse") return; state.hover = true; wake(); };
    const onPointerLeave = e => { if (e.pointerType === "mouse") { state.hover = false; state.pointer.inside = false; } wake(); };
    const onClick = e => {
      if (state.suppressClick) { state.suppressClick = false; return; }
      const card = e.target.closest?.("[data-cc-index]");
      if (!card) return;
      const index = Number(card.dataset.ccIndex);
      if (o.focusOnClick) focusIndex(index);
      o.onItemClick?.(list[index], index);
    };
    const onKeyDown = e => {
      const forward = s.axis === "x" ? "ArrowDown" : "ArrowRight";
      const backward = s.axis === "x" ? "ArrowUp" : "ArrowLeft";
      if (e.key === forward) stepBy(1);
      else if (e.key === backward) stepBy(-1);
      else if (e.key === "Home") focusIndex(0);
      else if (e.key === "End") focusIndex(list.length - 1);
      else if (e.key === "Enter" || e.key === " ") o.onItemClick?.(list[active], active);
      else return;
      e.preventDefault();
    };
    const onWheel = e => {
      if (!o.draggable) return;
      const delta = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : 0;
      if (!delta) return;
      e.preventDefault();
      const perPixel = 180 / (Math.PI * s.radius * state.fit);
      const sign = s.layout.inward ? -1 : 1;
      state.target = null;
      state.angle -= delta * perPixel * sign;
      state.velocity = -delta * perPixel * sign * 30;
      state.holdUntil = performance.now() + 1600;
      clearTimeout(state.wheelTimer);
      state.wheelTimer = setTimeout(() => { if (s.snap) state.target = nearest(state.angle + state.velocity * 0.12); wake(); }, 140);
      wake();
    };
    const onVisibility = () => { if (document.hidden) sleep(); else wake(); };
    const onReduced = () => { compute(); wake(); };

    root.addEventListener("pointerdown", onPointerDown);
    root.addEventListener("pointermove", onPointerMove);
    root.addEventListener("pointerup", onPointerUp);
    root.addEventListener("pointercancel", onPointerUp);
    root.addEventListener("pointerenter", onPointerEnter);
    root.addEventListener("pointerleave", onPointerLeave);
    root.addEventListener("click", onClick);
    root.addEventListener("keydown", onKeyDown);
    root.addEventListener("wheel", onWheel, { passive: false });
    document.addEventListener("visibilitychange", onVisibility);
    reducedQuery?.addEventListener?.("change", onReduced);
    const ro = new ResizeObserver(() => { measure(); wake(); });
    ro.observe(root);
    const io = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; if (visible) wake(); else sleep(); });
    io.observe(root);

    // ---------- API ----------
    function setItems(items) {
      list = Array.isArray(items) ? items.slice() : [];
      active = clamp(active, 0, Math.max(list.length - 1, 0));
      compute(); buildCards(); buildCaption(); updateLive(); measure();
      render(performance.now());
      load(); wake();
    }
    function setOptions(patch) {
      Object.assign(o, patch);
      setItems(list);
    }
    function destroy() {
      destroyed = true; sleep();
      clearTimeout(state.wheelTimer);
      ro.disconnect(); io.disconnect();
      root.removeEventListener("pointerdown", onPointerDown);
      root.removeEventListener("pointermove", onPointerMove);
      root.removeEventListener("pointerup", onPointerUp);
      root.removeEventListener("pointercancel", onPointerUp);
      root.removeEventListener("pointerenter", onPointerEnter);
      root.removeEventListener("pointerleave", onPointerLeave);
      root.removeEventListener("click", onClick);
      root.removeEventListener("keydown", onKeyDown);
      root.removeEventListener("wheel", onWheel);
      document.removeEventListener("visibilitychange", onVisibility);
      reducedQuery?.removeEventListener?.("change", onReduced);
      root.innerHTML = "";
      root.classList.remove("circular-carousel");
    }

    setItems(o.items);
    return { setItems, setOptions, focusIndex, stepBy, destroy, get active() { return active; }, get items() { return list; } };
  }

  window.CircularCarousel = CircularCarousel;
})();
