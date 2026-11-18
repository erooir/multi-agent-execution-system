import type { CSSProperties, ReactNode } from "react";

/* Flat illustration icons for the expert / skill / connector center.
   Each asset category has its own shape and its own colour (see wb-shell.css).
   Everything is inline SVG: no image files, no network, works offline. */
export type HubIconKind = "agent" | "flow" | "tool" | "skill" | "mcp";

/* Colour is assigned per item (stable hash of the item id), not per
   category: two agents, two tools etc. must never look identical.
   The shape tells the category apart; a continuous hue tells items apart. */
export const HUE_COUNT = 360;
const KIND_HUE: Record<HubIconKind, number> = {
  agent: 162,
  flow: 216,
  tool: 28,
  skill: 266,
  mcp: 190,
};
export function kindHue(kind: HubIconKind): number {
  return KIND_HUE[kind];
}
export function iconHue(id: string): number {
  let h = 2166136261;
  for (let i = 0; i < id.length; i += 1) {
    h ^= id.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) % HUE_COUNT;
}

const ART: Record<HubIconKind, ReactNode> = {
  agent: (
    <>
      <circle cx="32" cy="32" r="25" fill="#fff" opacity=".2" />
      <path d="M32 13v6M24 13h16" stroke="#fff" strokeWidth="3" strokeLinecap="round" fill="none" />
      <rect x="16" y="20" width="32" height="26" rx="9" fill="#fff" />
      <circle cx="26" cy="32" r="3.4" fill="var(--hub-ink)" />
      <circle cx="38" cy="32" r="3.4" fill="var(--hub-ink)" />
      <path d="M27 40q5 4 10 0" stroke="var(--hub-ink)" strokeWidth="2.4" fill="none" strokeLinecap="round" />
      <path d="M12 30H8M56 30h-4" stroke="#fff" strokeWidth="3" strokeLinecap="round" />
    </>
  ),
  flow: (
    <>
      <path d="M18 20h28M32 20v14M18 44h28" stroke="#fff" strokeWidth="3.4" strokeLinecap="round" fill="none" />
      <circle cx="16" cy="20" r="8" fill="#fff" />
      <circle cx="48" cy="20" r="8" fill="#fff" />
      <circle cx="32" cy="44" r="9" fill="#fff" />
      <circle cx="16" cy="20" r="3" fill="var(--hub-ink)" />
      <circle cx="48" cy="20" r="3" fill="var(--hub-ink)" />
      <circle cx="32" cy="44" r="3.4" fill="var(--hub-ink)" />
    </>
  ),
  tool: (
    <>
      <circle cx="32" cy="32" r="24" fill="#fff" opacity=".18" />
      <path
        d="M42 14a12 12 0 0 0-14 16L16 42a5 5 0 0 0 7 7l12-12a12 12 0 0 0 15-15l-8 8-6-1-1-6z"
        fill="#fff"
        stroke="var(--hub-ink)"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
    </>
  ),
  skill: (
    <>
      <path d="M14 20 32 11l18 9v24L32 53 14 44z" fill="#fff" />
      <path d="M14 20 32 29l18-9M32 29v24" stroke="var(--hub-ink)" strokeWidth="2.2" fill="none" strokeLinejoin="round" />
      <path d="m23 15 18 9v6l-18-9z" fill="var(--hub-ink)" opacity=".22" />
    </>
  ),
  mcp: (
    <>
      <path d="M24 12v10M40 12v10" stroke="#fff" strokeWidth="4" strokeLinecap="round" />
      <rect x="18" y="22" width="28" height="16" rx="5" fill="#fff" />
      <path d="M32 38v12" stroke="#fff" strokeWidth="4" strokeLinecap="round" />
      <circle cx="24" cy="30" r="2.4" fill="var(--hub-ink)" />
      <circle cx="40" cy="30" r="2.4" fill="var(--hub-ink)" />
      <circle cx="32" cy="53" r="4" fill="#fff" opacity=".85" />
    </>
  ),
};

type IconStyle = CSSProperties & { "--hub-hue": string };

export default function HubIcon({
  kind,
  hue,
  size = 48,
  className = "",
}: {
  kind: HubIconKind;
  hue?: number;
  size?: number;
  className?: string;
}) {
  const h = (((hue ?? KIND_HUE[kind]) % HUE_COUNT) + HUE_COUNT) % HUE_COUNT;
  const style: IconStyle = {
    width: size,
    height: size,
    "--hub-hue": `${h}deg`,
  };
  return (
    <span
      className={`hub-icon hub-icon-${kind} ${className}`.trim()}
      style={style}
      data-hue={h}
      aria-hidden="true"
    >
      <svg viewBox="0 0 64 64" focusable="false">
        {ART[kind]}
      </svg>
    </span>
  );
}
