import type { ShapeName } from "../lib/status";

/** The drawing of one status shape in a 12x12 box, for placing inside another SVG (the map) or wrapping in `Shape`. */
export function ShapeBody({ name }: { name: ShapeName }) {
  const stroke = { fill: "none", stroke: "currentColor", strokeLinecap: "round", strokeLinejoin: "round" } as const;
  let body;
  switch (name) {
    case "circle":
      body = <circle cx="6" cy="6" r="5" fill="currentColor" />;
      break;
    case "ring":
      body = <circle cx="6" cy="6" r="4.2" {...stroke} strokeWidth="1.8" />;
      break;
    case "triangle":
      body = <path d="M6 1 L11.2 10.6 H0.8 Z" fill="currentColor" />;
      break;
    case "square":
      body = <rect x="1.5" y="1.5" width="9" height="9" fill="currentColor" />;
      break;
    case "diamond":
      body = <path d="M6 0.6 L11.4 6 L6 11.4 L0.6 6 Z" fill="currentColor" />;
      break;
    case "cross":
      body = <path d="M2 2 L10 10 M10 2 L2 10" {...stroke} strokeWidth="2.2" />;
      break;
    case "dash":
      body = <path d="M1.5 6 H10.5" {...stroke} strokeWidth="2.4" />;
      break;
    case "half":
      body = (
        <>
          <circle cx="6" cy="6" r="4.4" {...stroke} strokeWidth="1.6" />
          <path d="M6 1.6 A4.4 4.4 0 0 1 6 10.4 Z" fill="currentColor" />
        </>
      );
      break;
    case "pause":
      body = (
        <>
          <rect x="2.2" y="1.8" width="2.8" height="8.4" fill="currentColor" />
          <rect x="7" y="1.8" width="2.8" height="8.4" fill="currentColor" />
        </>
      );
      break;
    case "check":
      body = <path d="M1.8 6.4 L4.9 9.5 L10.4 2.6" {...stroke} strokeWidth="2.2" />;
      break;
    case "arrow-left":
      body = <path d="M10.2 6 H2.4 M5.6 2.8 L2.4 6 L5.6 9.2" {...stroke} strokeWidth="2" />;
      break;
    case "hexagon":
      body = <path d="M6 0.6 L10.9 3.3 V8.7 L6 11.4 L1.1 8.7 V3.3 Z" fill="currentColor" />;
      break;
  }
  return <>{body}</>;
}

/** The status shapes (docs/ux/DESIGN_SYSTEM.md). Decorative: the meaning is always also written as text. */
export function Shape({ name, size = 12 }: { name: ShapeName; size?: number }) {
  return (
    <svg className="shape" width={size} height={size} viewBox="0 0 12 12" aria-hidden="true" focusable="false">
      <ShapeBody name={name} />
    </svg>
  );
}
