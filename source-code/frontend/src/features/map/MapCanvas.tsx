import { useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { ShapeBody } from "../../components/Shape";
import { Button } from "../../components/Button";
import { statusEntry } from "../../lib/status";
import { accessibleName, visibleOnMap, type Layers, type MapItem, type MapModel } from "./model";

interface Props {
  model: MapModel;
  layers: Layers;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  now: number;
  /** Paused or replaying: elements are drawn dimmed so the picture is never mistaken for live. */
  notLive?: boolean;
  large?: boolean;
  /** A small inset for a detail screen: no pan and zoom buttons, shorter. */
  compact?: boolean;
  /** Segment ids of a route to lay over the network, in the info colour with a solid underlay. */
  routeEdges?: readonly string[];
}

interface View {
  x: number;
  y: number;
  w: number;
  h: number;
}

const ZOOM_STEP = 1.25;
const TONE_COLOR = { ok: "var(--color-accent)", warn: "var(--color-warning)", danger: "var(--color-danger-text)", info: "var(--color-info)", neutral: "var(--color-neutral)" } as const;

const FLOW_STYLE = {
  flowing: { stroke: "var(--color-accent)", width: 4, dash: undefined },
  slow: { stroke: "var(--color-warning)", width: 5, dash: "10 6" },
  congested: { stroke: "var(--color-danger-text)", width: 6, dash: "2 5" },
  unknown: { stroke: "var(--color-neutral)", width: 2.5, dash: "1 7" },
} as const;

function arrowPoints(a: { x: number; y: number }, b: { x: number; y: number }): string {
  const angle = Math.atan2(b.y - a.y, b.x - a.x);
  const cx = a.x + (b.x - a.x) * 0.62;
  const cy = a.y + (b.y - a.y) * 0.62;
  const tip = { x: cx + Math.cos(angle) * 7, y: cy + Math.sin(angle) * 7 };
  const left = { x: cx + Math.cos(angle + 2.5) * 7, y: cy + Math.sin(angle + 2.5) * 7 };
  const right = { x: cx + Math.cos(angle - 2.5) * 7, y: cy + Math.sin(angle - 2.5) * 7 };
  return `${tip.x},${tip.y} ${left.x},${left.y} ${right.x},${right.y}`;
}

function markerLook(item: MapItem): { shape: Parameters<typeof ShapeBody>[0]["name"]; color: string; size: number } {
  if (item.kind === "incident") {
    const entry = statusEntry("severity", item.severity);
    return { shape: entry.shape, color: TONE_COLOR[entry.tone], size: 24 };
  }
  if (item.kind === "call") {
    const entry = statusEntry("call", item.status?.value);
    return { shape: entry.shape, color: TONE_COLOR[entry.tone], size: 22 };
  }
  if (item.kind === "unit") return { shape: "diamond", color: TONE_COLOR.info, size: 18 };
  const entry = statusEntry("freshness", item.freshness ?? "unknown");
  return { shape: entry.shape, color: TONE_COLOR[entry.tone], size: 13 };
}

/** The schematic. Every drawn element is a real, focusable button with a spoken description; the same facts are in the list view. */
export function MapCanvas({ model, layers, selectedId, onSelect, now, notLive = false, large = false, compact = false, routeEdges = [] }: Props) {
  const { projection, items } = model;
  const home: View = useMemo(() => ({ x: 0, y: 0, w: projection.width, h: projection.height }), [projection.width, projection.height]);
  const [view, setView] = useState<View>(home);
  const drag = useRef<{ x: number; y: number; view: View } | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  const zoom = (factor: number) =>
    setView((v) => {
      const w = Math.min(home.w, Math.max(home.w / 6, v.w / factor));
      const h = (w / home.w) * home.h;
      return { x: Math.min(Math.max(0, v.x + (v.w - w) / 2), home.w - w), y: Math.min(Math.max(0, v.y + (v.h - h) / 2), home.h - h), w, h };
    });
  const pan = (dx: number, dy: number) =>
    setView((v) => ({ ...v, x: Math.min(Math.max(0, v.x + dx * v.w), home.w - v.w), y: Math.min(Math.max(0, v.y + dy * v.h), home.h - v.h) }));

  const onKeyDown = (event: KeyboardEvent<SVGSVGElement>) => {
    if (event.target !== event.currentTarget) {
      if (event.key === "Escape") onSelect(null);
      return;
    }
    if (event.key === "ArrowLeft") pan(-0.1, 0);
    else if (event.key === "ArrowRight") pan(0.1, 0);
    else if (event.key === "ArrowUp") pan(0, -0.1);
    else if (event.key === "ArrowDown") pan(0, 0.1);
    else if (event.key === "+" || event.key === "=") zoom(ZOOM_STEP);
    else if (event.key === "-" || event.key === "_") zoom(1 / ZOOM_STEP);
    else if (event.key === "0") setView(home);
    else if (event.key === "Escape") onSelect(null);
    else return;
    event.preventDefault();
  };

  const onPointerDown = (event: PointerEvent<SVGRectElement>) => {
    drag.current = { x: event.clientX, y: event.clientY, view };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const onPointerMove = (event: PointerEvent<SVGRectElement>) => {
    const d = drag.current;
    const box = svgRef.current?.getBoundingClientRect();
    if (!d || !box) return;
    const dx = ((event.clientX - d.x) / box.width) * d.view.w;
    const dy = ((event.clientY - d.y) / box.height) * d.view.h;
    setView({ ...d.view, x: Math.min(Math.max(0, d.view.x - dx), home.w - d.view.w), y: Math.min(Math.max(0, d.view.y - dy), home.h - d.view.h) });
  };
  const endDrag = () => {
    drag.current = null;
  };

  const shown = items.filter((item) => visibleOnMap(item, layers) && (item.line || item.point));
  const order: Record<MapItem["kind"], number> = { segment: 0, intersection: 1, device: 2, incident: 3, call: 4, unit: 5 };
  const drawn = [...shown].sort((a, b) => order[a.kind] - order[b.kind]);
  const labelSize = large ? 22 : 14;

  const activate = (event: KeyboardEvent<SVGGElement>, id: string) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      event.stopPropagation();
      onSelect(id);
    }
  };

  return (
    <div className={`map-canvas${notLive ? " not-live" : ""}${large ? " large" : ""}${compact ? " compact" : ""}`}>
      <div className="map-tools" role="group" aria-label="Map view" hidden={compact}>
        <Button variant="secondary" onClick={() => zoom(ZOOM_STEP)} aria-label="Zoom in">
          +
        </Button>
        <Button variant="secondary" onClick={() => zoom(1 / ZOOM_STEP)} aria-label="Zoom out">
          -
        </Button>
        <Button variant="secondary" onClick={() => setView(home)}>
          Reset view
        </Button>
      </div>
      <svg
        ref={svgRef}
        className="map-svg"
        viewBox={`${view.x} ${view.y} ${view.w} ${view.h}`}
        role="group"
        aria-labelledby="map-title map-desc"
        tabIndex={0}
        onKeyDown={onKeyDown}
        data-testid="map-canvas"
      >
        <title id="map-title">Schematic map of the district</title>
        <desc id="map-desc">
          Junctions are circles joined by directed road segments. Arrow keys pan, plus and minus zoom, Tab steps through the elements, Enter selects one. Every element is also in the list view.
        </desc>
        <rect x={0} y={0} width={projection.width} height={projection.height} className="map-bg" onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={endDrag} onPointerCancel={endDrag} onClick={() => onSelect(null)} />
        {drawn.map((item) => {
          const selected = item.id === selectedId;
          const stale = item.freshness === "stale" || item.freshness === "unknown";
          const label = accessibleName(item);
          if (item.kind === "segment" && item.line) {
            const style = item.closed ? { stroke: "var(--color-destructive)", width: 7, dash: undefined } : FLOW_STYLE[item.flow ?? "unknown"];
            const { a, b, mid } = item.line;
            return (
              <g key={item.id} className={`map-el segment${selected ? " selected" : ""}`} role="button" tabIndex={0} aria-label={label} aria-pressed={selected} data-item-id={item.id} onClick={() => onSelect(item.id)} onKeyDown={(e) => activate(e, item.id)} opacity={stale ? 0.5 : 1}>
                {routeEdges.includes(item.id.slice("segment:".length)) ? <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="var(--color-info)" strokeWidth={style.width + 12} strokeLinecap="round" opacity={0.55} data-on-route="true" /> : null}
                {selected ? <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="var(--color-ring)" strokeWidth={style.width + 8} strokeLinecap="round" opacity={0.85} /> : null}
                <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={style.stroke} strokeWidth={style.width} strokeDasharray={style.dash} strokeLinecap="round" />
                <polygon points={arrowPoints(a, b)} fill={style.stroke} />
                {item.closed ? (
                  <g stroke="var(--color-foreground)" strokeWidth={2.5} strokeLinecap="round">
                    <line x1={mid.x - 6} y1={mid.y - 6} x2={mid.x + 6} y2={mid.y + 6} />
                    <line x1={mid.x + 6} y1={mid.y - 6} x2={mid.x - 6} y2={mid.y + 6} />
                  </g>
                ) : null}
                <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="transparent" strokeWidth={18} />
              </g>
            );
          }
          if (!item.point) return null;
          const { x, y } = item.point;
          if (item.kind === "intersection") {
            return (
              <g key={item.id} className={`map-el node${selected ? " selected" : ""}`} role="button" tabIndex={0} aria-label={label} aria-pressed={selected} data-item-id={item.id} onClick={() => onSelect(item.id)} onKeyDown={(e) => activate(e, item.id)}>
                <circle cx={x} cy={y} r={selected ? 13 : 10} fill="var(--color-card)" stroke={selected ? "var(--color-ring)" : "var(--color-border-strong)"} strokeWidth={selected ? 4 : 3} />
                <text x={x + 14} y={y - 12} fontSize={labelSize} fill="var(--color-foreground)" className="map-label">
                  {item.name.replace(/^int-/, "")}
                </text>
              </g>
            );
          }
          const look = markerLook(item);
          const scale = look.size / 12;
          return (
            <g key={item.id} className={`map-el marker ${item.kind}${selected ? " selected" : ""}`} role="button" tabIndex={0} aria-label={label} aria-pressed={selected} data-item-id={item.id} onClick={() => onSelect(item.id)} onKeyDown={(e) => activate(e, item.id)} opacity={item.kind === "device" && stale ? 0.7 : 1} style={{ color: look.color }}>
              {selected ? <circle cx={x} cy={y} r={look.size * 0.85} fill="none" stroke="var(--color-ring)" strokeWidth={3} strokeDasharray="4 3" /> : null}
              <g transform={`translate(${x - look.size / 2} ${y - look.size / 2}) scale(${scale})`}>
                <ShapeBody name={look.shape} />
              </g>
              {item.kind === "incident" || selected ? (
                <text x={x + look.size / 2 + 4} y={y + 5} fontSize={labelSize} fill="var(--color-foreground)" className="map-label">
                  {item.kind === "incident" ? item.name.split(" at ")[0] : item.name}
                </text>
              ) : null}
              <circle cx={x} cy={y} r={Math.max(look.size, 18) / 2 + 4} fill="transparent" />
            </g>
          );
        })}
      </svg>
      <span className="sr-only" data-now={now} />
    </div>
  );
}
