import React from "react";
import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { C, display, mono } from "./theme";

/**
 * Motion helpers. Everything is frame-derived rather than CSS-animated, so the
 * render is deterministic: frame 41 looks identical on every machine and on
 * every re-render, which is the whole reason for doing this in Remotion
 * instead of screen-recording a web page.
 */

export const useFly = (delay = 0) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = spring({ frame: frame - delay, fps, config: { damping: 14, mass: 0.7 } });
  return {
    opacity: interpolate(t, [0, 0.35], [0, 1], { extrapolateRight: "clamp" }),
    transform: `perspective(1600px) translate3d(0px, ${interpolate(
      t, [0, 1], [70, 0]
    )}px, ${interpolate(t, [0, 1], [-520, 0])}px) rotateX(${interpolate(
      t, [0, 1], [28, 0]
    )}deg)`,
  };
};

export const useSlam = (delay = 0) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = spring({ frame: frame - delay, fps, config: { damping: 9, mass: 0.5, stiffness: 120 } });
  return {
    opacity: interpolate(t, [0, 0.25], [0, 1], { extrapolateRight: "clamp" }),
    transform: `scale(${interpolate(t, [0, 1], [1.55, 1])}) rotate(${interpolate(
      t, [0, 1], [-3.5, 0]
    )}deg)`,
  };
};

export const useSpinIn = (delay = 0) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = spring({ frame: frame - delay, fps, config: { damping: 16, mass: 0.9 } });
  return {
    opacity: interpolate(t, [0, 0.3], [0, 1], { extrapolateRight: "clamp" }),
    transform: `perspective(1400px) rotateY(${interpolate(
      t, [0, 1], [-82, 0]
    )}deg) translateZ(${interpolate(t, [0, 1], [-220, 0])}px)`,
  };
};

/** The 180-degree reveal used for pending -> confirmed. */
export const useFlip = (delay: number, hold: number) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = spring({
    frame: frame - delay - hold,
    fps,
    config: { damping: 18, mass: 1.1 },
  });
  return interpolate(t, [0, 1], [0, 180]);
};

export const Stage: React.FC<{
  children: React.ReactNode;
  bg?: string;
  fg?: string;
}> = ({ children, bg = C.concrete, fg = C.ink }) => (
  <div
    style={{
      position: "absolute",
      inset: 0,
      background: bg,
      color: fg,
      fontFamily: mono,
      padding: "96px 96px 118px",
      display: "flex",
      flexDirection: "column",
      justifyContent: "space-between",
      overflow: "hidden",
    }}
  >
    <div
      style={{
        position: "absolute",
        inset: 0,
        backgroundImage:
          "repeating-linear-gradient(0deg, rgba(10,10,10,.07) 0 1px, transparent 1px 96px), repeating-linear-gradient(90deg, rgba(10,10,10,.07) 0 1px, transparent 1px 96px)",
      }}
    />
    {children}
  </div>
);

export const Eyebrow: React.FC<{ children: React.ReactNode; color?: string }> = ({
  children,
  color = C.ink,
}) => {
  const fly = useFly(0);
  return (
    <div
      style={{
        ...fly,
        fontFamily: mono,
        fontWeight: 700,
        fontSize: 26,
        letterSpacing: "0.38em",
        textTransform: "uppercase",
        color,
      }}
    >
      {children}
    </div>
  );
};

export const Headline: React.FC<{
  children: React.ReactNode;
  size?: number;
  delay?: number;
  color?: string;
  width?: number;
}> = ({ children, size = 108, delay = 4, color, width }) => {
  const slam = useSlam(delay);
  return (
    <h2
      style={{
        ...slam,
        margin: 0,
        fontFamily: display,
        fontSize: size,
        lineHeight: 0.93,
        letterSpacing: "-0.03em",
        textTransform: "uppercase",
        color,
        maxWidth: width,
      }}
    >
      {children}
    </h2>
  );
};

export const Rule: React.FC<{ delay?: number }> = ({ delay = 8 }) => {
  const frame = useCurrentFrame();
  const w = interpolate(frame - delay, [0, 18], [0, 100], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return <div style={{ height: 12, background: C.ink, width: `${w}%` }} />;
};

export const Slab: React.FC<{
  children: React.ReactNode;
  tone?: "slab" | "paper" | "blue" | "acid" | "siren";
  delay?: number;
  motion?: "fly" | "spin";
  flex?: number;
  row?: boolean;
}> = ({ children, tone = "slab", delay = 0, motion = "fly", flex = 1, row = false }) => {
  const fly = useFly(delay);
  const spin = useSpinIn(delay);
  const m = motion === "spin" ? spin : fly;
  const bg = { slab: C.slab, paper: C.paper, blue: C.blue, acid: C.acid, siren: C.siren }[tone];
  const fg = tone === "blue" || tone === "siren" ? C.paper : C.ink;
  return (
    <div
      style={{
        ...m,
        flex,
        minWidth: 0,
        background: bg,
        color: fg,
        border: `9px solid ${C.ink}`,
        boxShadow: `17px 17px 0 ${C.ink}`,
        padding: 34,
        display: "flex",
        flexDirection: row ? "row" : "column",
        alignItems: row ? "center" : "stretch",
        gap: 14,
      }}
    >
      {children}
    </div>
  );
};

export const SlabTitle: React.FC<{ children: React.ReactNode; size?: number }> = ({
  children,
  size = 42,
}) => (
  <h3
    style={{
      margin: 0,
      fontFamily: display,
      fontSize: size,
      lineHeight: 1,
      textTransform: "uppercase",
    }}
  >
    {children}
  </h3>
);

export const Num: React.FC<{ children: React.ReactNode; size?: number }> = ({
  children,
  size = 92,
}) => (
  <div
    style={{
      fontFamily: display,
      fontSize: size,
      lineHeight: 0.9,
      fontVariantNumeric: "tabular-nums",
    }}
  >
    {children}
  </div>
);

export const Cap: React.FC<{ children: React.ReactNode; size?: number }> = ({
  children,
  size = 27,
}) => <div style={{ fontSize: size, lineHeight: 1.32 }}>{children}</div>;

export const Row: React.FC<{ children: React.ReactNode; gap?: number }> = ({
  children,
  gap = 26,
}) => <div style={{ display: "flex", gap, alignItems: "stretch" }}>{children}</div>;

export const Tick: React.FC<{ children: React.ReactNode; tone?: string; fg?: string }> = ({
  children,
  tone = C.acid,
  fg = C.ink,
}) => (
  <span
    style={{
      fontWeight: 700,
      fontSize: 25,
      letterSpacing: "0.2em",
      textTransform: "uppercase",
      border: `6px solid ${C.ink}`,
      padding: "9px 18px",
      background: tone,
      color: fg,
      whiteSpace: "nowrap",
    }}
  >
    {children}
  </span>
);

export const Stamp: React.FC<{ children: React.ReactNode; delay?: number; tone?: string }> = ({
  children,
  delay = 20,
  tone = C.siren,
}) => {
  const slam = useSlam(delay);
  return (
    <div
      style={{
        position: "absolute",
        top: 92,
        right: -18,
        transform: `${slam.transform} rotate(9deg)`,
        opacity: slam.opacity,
        background: tone,
        color: C.paper,
        border: `9px solid ${C.ink}`,
        boxShadow: `14px 14px 0 ${C.ink}`,
        fontFamily: display,
        fontSize: 38,
        textTransform: "uppercase",
        padding: "14px 30px",
      }}
    >
      {children}
    </div>
  );
};

export const Marquee: React.FC<{ text: string }> = ({ text }) => {
  const frame = useCurrentFrame();
  const shift = (frame * 3.2) % 2400;
  const run = `${text}  ·  `.repeat(6);
  return (
    <div
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        bottom: 0,
        borderTop: `9px solid ${C.ink}`,
        background: C.ink,
        color: C.acid,
        fontWeight: 700,
        fontSize: 25,
        letterSpacing: "0.3em",
        textTransform: "uppercase",
        padding: "14px 0",
        overflow: "hidden",
        whiteSpace: "nowrap",
      }}
    >
      <div style={{ transform: `translateX(${-shift}px)` }}>{run}</div>
    </div>
  );
};

/** A labelled bar that grows from zero. Used for every comparison in the reel. */
export const Bar: React.FC<{
  label: string;
  value: string;
  pct: number;
  tone?: string;
  delay?: number;
  labelWidth?: number;
}> = ({ label, value, pct, tone = C.ink, delay = 0, labelWidth = 560 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = spring({ frame: frame - delay, fps, config: { damping: 18, mass: 0.8 } });
  const fly = useFly(delay);
  return (
    <div style={{ ...fly, display: "flex", alignItems: "center", gap: 22 }}>
      <span style={{ fontWeight: 700, fontSize: 28, width: labelWidth }}>{label}</span>
      <span
        style={{
          height: 46,
          border: `7px solid ${C.ink}`,
          background: C.paper,
          flex: 1,
          overflow: "hidden",
        }}
      >
        <span
          style={{
            display: "block",
            height: "100%",
            width: `${interpolate(t, [0, 1], [0, pct])}%`,
            background: tone,
          }}
        />
      </span>
      <span
        style={{
          fontFamily: display,
          fontSize: 38,
          width: 150,
          textAlign: "right",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {value}
      </span>
    </div>
  );
};
