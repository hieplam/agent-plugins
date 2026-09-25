import React, { useMemo } from "react";
import {
  AbsoluteFill,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
  CalculateMetadataFunction,
} from "remotion";

/**
 * ImageLoop — a generic, config-driven engine that animates ONE supplied still
 * image into a short SEAMLESS loop. Every motion is a pure function of the loop
 * phase t = frame / durationInFrames (0..1), driven by sin(2π·k·t + φ) with an
 * INTEGER k, so frame 0 and frame N are identical. That is what makes the loop
 * seamless with zero crossfade — the whole trick of this skill.
 *
 * All effects are supplied via props (pass --props=./props.json to the CLI).
 * Positions are normalized 0..1 against the frame, so they hold at any render
 * size as long as the image is object-fit: cover at the same aspect.
 *
 * Effect families (all optional — an empty array renders nothing):
 *   - breath    : whole-frame breathing (one slow scale pulse)
 *   - halos     : bright pulsing glows for weapons / lights (core + wide bloom)
 *   - auras     : big soft background glows (glowing city, sky, spirit light)
 *   - particles : drifting motes / pyreflies / embers / snow
 *   - jiggle    : OPT-IN localized soft-body wobble (see references — easy to
 *                 make look bad on a flat painting; keep amp tiny)
 *   - vignette  : breathing edge darkening
 */

const TAU = Math.PI * 2;

export interface Halo {
  cx: number; cy: number; w: number; h: number; rot: number;
  color: string; base: number; amp: number; k: number; phase: number;
}
export interface Aura {
  cx: number; cy: number; w: number; h: number; blur: number;
  color: string; base: number; amp: number; k: number; phase: number;
}
export interface Jiggle {
  cx: number; cy: number; rx: number; ry: number; originY: number;
  amp: number; ty: number; k: number; phase: number;
}
export interface ImageLoopProps {
  image: string;
  width?: number; height?: number; fps?: number; durationInFrames?: number;
  bg?: string;
  breath?: { amp: number; k: number };
  vignette?: { base: number; amp: number; k: number };
  halos?: Halo[];
  auras?: Aura[];
  jiggle?: Jiggle[];
  particles?: { count: number; palette?: string[] };
  etherealGrade?: boolean;
}

export const calculateImageLoopMetadata: CalculateMetadataFunction<ImageLoopProps> = ({ props }) => ({
  width: props.width ?? 1920,
  height: props.height ?? 1080,
  fps: props.fps ?? 30,
  durationInFrames: props.durationInFrames ?? 210,
});

// deterministic PRNG — identical particle field every render / frame
function mulberry32(seed: number) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export const ImageLoop: React.FC<ImageLoopProps> = (props) => {
  const {
    image,
    bg = "#0a0a12",
    breath = { amp: 0.006, k: 1 },
    vignette = { base: 0.3, amp: 0.06, k: 1 },
    halos = [],
    auras = [],
    jiggle = [],
    particles = { count: 0 },
    etherealGrade = false,
  } = props;

  const frame = useCurrentFrame();
  const { width, height, durationInFrames } = useVideoConfig();
  const t = frame / durationInFrames;
  const img = staticFile(image);

  const palette = particles.palette ?? ["255,255,255", "150,215,255", "255,224,150"];
  const pts = useMemo(() => {
    const rnd = mulberry32(1337);
    return new Array(particles.count).fill(0).map(() => ({
      x: rnd(),
      size: 2 + rnd() * 6,
      loops: 1 + Math.floor(rnd() * 2),
      swayAmp: 0.008 + rnd() * 0.022,
      swayK: 1 + Math.floor(rnd() * 3),
      phase: rnd(),
      color: palette[Math.floor(rnd() * palette.length)],
      twK: 1 + Math.floor(rnd() * 3),
      bright: 0.7 + rnd() * 0.4,
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [particles.count]);

  const breathS = 1 + breath.amp * Math.sin(TAU * breath.k * t);
  const vig = vignette.base + vignette.amp * Math.sin(TAU * vignette.k * t);

  return (
    <AbsoluteFill style={{ backgroundColor: bg, overflow: "hidden" }}>
      {/* breathing base image + jiggle portholes scale together, stay aligned */}
      <AbsoluteFill style={{ transform: `scale(${breathS})`, transformOrigin: "50% 55%" }}>
        {/* IMPORTANT: use explicit width/height, NOT `inset:0` — the shorthand
            fails to fill in Remotion's Chromium and collapses the image. */}
        <div style={{ position: "absolute", top: 0, left: 0, width: "100%", height: "100%", backgroundImage: `url(${img})`, backgroundSize: "cover", backgroundPosition: "center" }} />

        {jiggle.map((j, i) => {
          const cx = j.cx * width, cy = j.cy * height, rx = j.rx * width, ry = j.ry * height;
          const mask = `radial-gradient(ellipse ${rx}px ${ry}px at ${cx}px ${cy}px, #000 38%, rgba(0,0,0,0.5) 62%, transparent 100%)`;
          const b = Math.sin(TAU * j.k * t + j.phase);
          const sy = 1 + j.amp * b;          // stretch down / squash up
          const sx = 1 - 0.6 * j.amp * b;    // volume conservation → jelly wobble, not melt
          const ty = j.ty * b;
          return (
            <div key={`j${i}`} style={{ position: "absolute", top: 0, left: 0, width: "100%", height: "100%", maskImage: mask, WebkitMaskImage: mask }}>
              <div style={{ position: "absolute", top: 0, left: 0, width: "100%", height: "100%", backgroundImage: `url(${img})`, backgroundSize: "cover", backgroundPosition: "center", transform: `translateY(${ty}px) scaleX(${sx}) scaleY(${sy})`, transformOrigin: `${cx}px ${j.originY * height}px` }} />
            </div>
          );
        })}
      </AbsoluteFill>

      {/* background auras — big soft glows (glowing city / sky / spirit light) */}
      {auras.map((a, i) => {
        const cx = a.cx * width, cy = a.cy * height, w = a.w * width, h = a.h * height;
        const op = a.base + a.amp * Math.sin(TAU * a.k * t + a.phase);
        return (
          <div key={`a${i}`} style={{ position: "absolute", left: cx - w / 2, top: cy - h / 2, width: w, height: h, background: `radial-gradient(ellipse 50% 50% at 50% 50%, rgba(${a.color},${op.toFixed(3)}) 0%, rgba(${a.color},${(op * 0.45).toFixed(3)}) 45%, transparent 72%)`, mixBlendMode: "screen", filter: `blur(${a.blur}px)` }} />
        );
      })}

      {/* halos — bright core + wide bloom, pulsing (weapons, lights, gems) */}
      {halos.map((g, i) => {
        const cx = g.cx * width, cy = g.cy * height, w = g.w * width, h = g.h * height;
        const op = g.base + g.amp * Math.sin(TAU * g.k * t + g.phase);
        const grad = `radial-gradient(ellipse 50% 50% at 50% 50%, rgba(${g.color},${op.toFixed(3)}) 0%, rgba(${g.color},${(op * 0.7).toFixed(3)}) 22%, rgba(${g.color},${(op * 0.32).toFixed(3)}) 48%, transparent 74%)`;
        const boxBase = { position: "absolute" as const, left: cx - w / 2, top: cy - h / 2, width: w, height: h, transform: `rotate(${g.rot}deg)`, background: grad, mixBlendMode: "screen" as const };
        return (
          <React.Fragment key={`g${i}`}>
            <div style={{ ...boxBase, filter: "blur(14px)" }} />
            <div style={{ ...boxBase, filter: "blur(4px)" }} />
          </React.Fragment>
        );
      })}

      {/* particles — floating motes, seamless drift */}
      {pts.map((p, i) => {
        const prog = (p.phase + t * p.loops) % 1;
        const y = 1.05 - prog * 1.15;
        const x = p.x + p.swayAmp * Math.sin(TAU * p.swayK * (t + p.phase));
        const env = Math.sin(Math.PI * prog);           // fade in/out → invisible wrap
        const tw = 0.55 + 0.45 * Math.sin(TAU * p.twK * (t + p.phase));
        const opacity = env * tw * p.bright;
        return (
          <div key={`p${i}`} style={{ position: "absolute", left: x * width, top: y * height, width: p.size, height: p.size, borderRadius: "50%", background: `rgba(${p.color},${opacity.toFixed(3)})`, boxShadow: `0 0 ${p.size * 4}px rgba(${p.color},${(opacity * 0.9).toFixed(3)})`, mixBlendMode: "screen" }} />
        );
      })}

      {etherealGrade && (
        <AbsoluteFill style={{ background: "radial-gradient(ellipse 62% 52% at 55% 22%, rgba(130,188,255,0.14) 0%, transparent 55%),linear-gradient(180deg, rgba(66,120,210,0.14) 0%, transparent 35%, transparent 68%, rgba(20,30,70,0.18) 100%)", mixBlendMode: "screen", pointerEvents: "none" }} />
      )}

      <AbsoluteFill style={{ background: `radial-gradient(ellipse 78% 78% at 50% 48%, transparent 52%, rgba(6,8,20,${vig.toFixed(3)}) 100%)`, pointerEvents: "none" }} />
    </AbsoluteFill>
  );
};
