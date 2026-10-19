// Vanilla port of React Bits' <TextPressure /> (https://reactbits.dev),
// itself ported from https://codepen.io/JuanFuentes/full/rgXKGQ.
// Each letter's variable-font axes (weight / width / italic / opacity) react to
// how close the cursor is. Needs a variable font with wght + wdth + ital axes.
//
//   const tp = TextPressure(container, { text: "Dinner", textColor: "#d6b25e" });
//   tp.setText("Lunch");   tp.destroy();

(function () {
  const dist = (a, b) => Math.hypot(b.x - a.x, b.y - a.y);
  const getAttr = (d, maxDist, minVal, maxVal) => {
    const val = maxVal - Math.abs((maxVal * d) / maxDist);
    return Math.max(minVal, val + minVal);
  };
  const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

  const loadedFonts = new Set();

  function TextPressure(container, opts = {}) {
    const o = {
      text: "Compressa",
      fontFamily: "Roboto Flex",
      fontUrl: "https://fonts.googleapis.com/css2?family=Roboto+Flex:opsz,wdth,wght@8..144,25..151,100..1000&display=swap",
      width: true, weight: true, italic: true, alpha: false,
      flex: true, stroke: false, scale: false,
      textColor: "#FFFFFF", strokeColor: "#FF0000",
      className: "", minFontSize: 24,
      ...opts,
    };

    if (o.fontUrl && !loadedFonts.has(o.fontUrl)) {
      const link = document.createElement("link");
      link.rel = "stylesheet"; link.href = o.fontUrl;
      document.head.appendChild(link);
      loadedFonts.add(o.fontUrl);
    }

    container.style.position = "relative";
    const title = document.createElement("h1");
    title.className = ["text-pressure-title", o.className, o.flex && "tp-flex", o.stroke && "tp-stroke"].filter(Boolean).join(" ");
    title.setAttribute("aria-label", o.text);
    Object.assign(title.style, {
      fontFamily: `"${o.fontFamily}", sans-serif`, textTransform: "uppercase", margin: 0, textAlign: "center",
      userSelect: "none", whiteSpace: "nowrap", fontWeight: 100, width: "100%", color: o.textColor,
      transformOrigin: "center top",
    });
    title.style.setProperty("--tp-stroke", o.strokeColor);
    container.appendChild(title);

    let spans = [];
    function setText(text) {
      o.text = text;
      title.setAttribute("aria-label", text);
      title.textContent = "";
      spans = text.split("").map(ch => {
        const s = document.createElement("span");
        s.textContent = ch; s.dataset.char = ch; s.setAttribute("aria-hidden", "true");
        s.style.display = "inline-block";
        title.appendChild(s);
        return s;
      });
      setSize();
    }

    function setSize() {
      const { width: cw, height: ch } = container.getBoundingClientRect();
      // Same sizing rule as the original, but never taller than the container.
      let fs = Math.max(cw / (Math.max(o.text.length, 1) / 2), o.minFontSize);
      if (!o.scale && ch) fs = Math.max(Math.min(fs, ch * 0.92), o.minFontSize);
      title.style.fontSize = fs + "px";
      title.style.lineHeight = 1;
      title.style.transform = "scale(1, 1)";
      requestAnimationFrame(() => {
        const r = title.getBoundingClientRect();
        if (o.scale && r.height > 0) {
          const y = ch / r.height;
          title.style.transform = `scale(1, ${y})`;
          title.style.lineHeight = y;
        }
      });
    }

    const cursor = { x: 0, y: 0 }, mouse = { x: 0, y: 0 };
    const r0 = container.getBoundingClientRect();
    mouse.x = cursor.x = r0.left + r0.width / 2;
    mouse.y = cursor.y = r0.top + r0.height / 2;
    const onMove = e => { cursor.x = e.clientX; cursor.y = e.clientY; };
    const onTouch = e => { const t = e.touches[0]; cursor.x = t.clientX; cursor.y = t.clientY; };
    const onResize = debounce(setSize, 100);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("touchmove", onTouch, { passive: true });
    window.addEventListener("resize", onResize);

    // Only animate while on screen, and not at all for reduced-motion users.
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    let visible = true, raf = 0;
    const io = new IntersectionObserver(([e]) => { visible = e.isIntersecting; if (visible && !raf && !reduce) raf = requestAnimationFrame(tick); });
    io.observe(container);

    function tick() {
      raf = 0;
      if (!visible) return;
      mouse.x += (cursor.x - mouse.x) / 15;
      mouse.y += (cursor.y - mouse.y) / 15;
      const maxDist = title.getBoundingClientRect().width / 2;
      for (const span of spans) {
        const r = span.getBoundingClientRect();
        const d = dist(mouse, { x: r.x + r.width / 2, y: r.y + r.height / 2 });
        const wdth = o.width ? Math.floor(getAttr(d, maxDist, 5, 200)) : 100;
        const wght = o.weight ? Math.floor(getAttr(d, maxDist, 100, 900)) : 400;
        const ital = o.italic ? getAttr(d, maxDist, 0, 1).toFixed(2) : 0;
        const fvs = `'wght' ${wght}, 'wdth' ${wdth}, 'ital' ${ital}`;
        if (span.style.fontVariationSettings !== fvs) span.style.fontVariationSettings = fvs;
        if (o.alpha) span.style.opacity = getAttr(d, maxDist, 0, 1).toFixed(2);
      }
      raf = requestAnimationFrame(tick);
    }
    if (reduce) spans.forEach(s => (s.style.fontVariationSettings = "'wght' 700, 'wdth' 100"));

    setText(o.text);
    if (document.fonts) document.fonts.ready.then(setSize);

    return {
      setText,
      destroy() {
        cancelAnimationFrame(raf); io.disconnect();
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("touchmove", onTouch);
        window.removeEventListener("resize", onResize);
        title.remove();
      },
    };
  }

  const css = document.createElement("style");
  css.textContent = `
    .text-pressure-title.tp-flex { display: flex; justify-content: space-between; }
    .text-pressure-title.tp-stroke span { position: relative; }
    .text-pressure-title.tp-stroke span::after {
      content: attr(data-char); position: absolute; left: 0; top: 0; color: transparent; z-index: -1;
      -webkit-text-stroke-width: 3px; -webkit-text-stroke-color: var(--tp-stroke);
    }`;
  document.head.appendChild(css);

  window.TextPressure = TextPressure;
})();
