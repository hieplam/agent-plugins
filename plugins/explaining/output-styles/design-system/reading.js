// Reading — the runtime half of the design system. It themes mermaid from the colour
// tokens in reading.css, then sizes every rendered diagram to the screen.
//
// render-illustration.ts inlines this file into a <script type="module"> right after
// its own `import mermaid from '<pinned CDN url>'` and calls renderDiagrams(mermaid).
// It therefore carries no import or export of its own: the renderer owns the mermaid
// version, which must stay the major that validate-mermaid.ts parses with.

// A diagram grows to fill the page width, but at most to twice its natural size: mermaid
// lays text out at 16px, so labels top out near 32px on a small chart.
const MAX_SCALE = 2;
// The share of the screen height a diagram may fill, leaving room for the title above
// and the caption below it on a 1440px-tall screen.
const HEIGHT_SHARE = 0.78;

/** Pure. The factor to scale a diagram of `natural` size by, to sit in `room`.
 * Fit the room's height, but never past `maxScale`, and never below `textScale` — the
 * factor that sets the diagram's labels at body-text size, so a diagram taller than the
 * screen scrolls down rather than shrinking its text. Then shrink to the room's width,
 * but never below `minScale`: a diagram wider than that scrolls sideways in its frame
 * instead of shrinking its labels past reading. */
function fitScale(natural, room, maxScale, textScale, minScale) {
  const byWidth = room.width / natural.width;
  const byHeight = room.height / natural.height;
  return Math.max(minScale, Math.min(byWidth, Math.max(textScale, Math.min(byHeight, maxScale))));
}

function token(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Pure. True when a `#rrggbb` ground is dark. Deciding from the resolved --paper token
 * rather than the media query keeps mermaid in step with a data-theme override too. */
function isDarkGround(hex) {
  const n = parseInt(hex.replace('#', ''), 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b < 128;
}

/** mermaid's `base` theme is the only one that takes custom colours. Every value comes
 * from a reading.css token, so light and dark follow whatever the stylesheet resolved. */
function themeVariables() {
  const paper = token('--paper');
  const paperDeep = token('--paper-deep');
  const ink = token('--ink');
  const inkSoft = token('--ink-soft');
  const rule = token('--rule');
  const accent = token('--accent');
  const wash = token('--accent-wash');
  return {
    darkMode: isDarkGround(paper),
    background: paper,
    fontFamily: token('--font-body'),
    fontSize: '16px',
    textColor: ink,
    lineColor: inkSoft,
    primaryColor: paperDeep,
    primaryTextColor: ink,
    primaryBorderColor: accent,
    secondaryColor: wash,
    secondaryTextColor: ink,
    secondaryBorderColor: accent,
    tertiaryColor: paper,
    tertiaryTextColor: ink,
    tertiaryBorderColor: rule,
    mainBkg: paperDeep,
    nodeBorder: accent,
    clusterBkg: paper,
    clusterBorder: rule,
    edgeLabelBackground: paper,
    titleColor: ink,
    actorBkg: paperDeep,
    actorBorder: accent,
    actorTextColor: ink,
    actorLineColor: rule,
    signalColor: ink,
    signalTextColor: ink,
    labelBoxBkgColor: wash,
    labelBoxBorderColor: accent,
    labelTextColor: ink,
    loopTextColor: ink,
    noteBkgColor: wash,
    noteBorderColor: accent,
    noteTextColor: ink,
    activationBkgColor: wash,
    activationBorderColor: accent,
    sequenceNumberColor: paper,
  };
}

// Mermaid lays out label text at this size; scaling by body size / this sets labels at
// body size.
const MERMAID_TEXT_PX = 16;
// The smallest a label may be scaled to, however wide the diagram: 14px.
const MIN_SCALE = 14 / MERMAID_TEXT_PX;

function fitDiagram(svg) {
  const box = svg.viewBox.baseVal;
  if (!box || !box.width || !box.height) return;
  const room = { width: svg.parentElement.clientWidth, height: window.innerHeight * HEIGHT_SHARE };
  const textScale = parseFloat(getComputedStyle(document.body).fontSize) / MERMAID_TEXT_PX;
  const scale = fitScale({ width: box.width, height: box.height }, room, MAX_SCALE, textScale, MIN_SCALE);
  svg.removeAttribute('width');
  svg.removeAttribute('height');
  svg.style.width = `${Math.floor(box.width * scale)}px`;
  svg.style.height = `${Math.floor(box.height * scale)}px`;
}

/** Wait for the book face, render every `.mermaid` block, then fit each to the screen.
 * Mermaid measures label text while it lays out, so rendering before Caslon is ready would
 * size every box for the fallback face. The fonts are bundled in the page, so this wait
 * is short; a face that still fails to load costs at most two seconds, then the fallback
 * face is used. Loading the whole --font-body stack with the diagrams' own text pulls in
 * every file that text needs, the Vietnamese companion included. */
async function renderDiagrams(mermaid) {
  // Before any await: mermaid renders every `.mermaid` block itself on window load,
  // unthemed, unless told not to. Waiting for fonts first would race that load event.
  mermaid.initialize({ startOnLoad: false });
  const text = [...document.querySelectorAll('.mermaid')].map((el) => el.textContent).join(' ') || 'a';
  const fontsLoaded = document.fonts.load(`16px ${token('--font-body')}`, text);
  await Promise.race([fontsLoaded, new Promise((done) => setTimeout(done, 2000))]);

  mermaid.initialize({ startOnLoad: false, theme: 'base', themeVariables: themeVariables() });
  await mermaid.run({ querySelector: '.mermaid' });

  const fitAll = () => document.querySelectorAll('.mermaid svg').forEach(fitDiagram);
  fitAll();
  let pending = 0;
  window.addEventListener('resize', () => {
    cancelAnimationFrame(pending);
    pending = requestAnimationFrame(fitAll);
  });
}
