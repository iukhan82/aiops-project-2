import { ShapeBody } from "../../components/Shape";
import type { ShapeName } from "../../lib/status";
import { FLOW_THRESHOLDS } from "./geometry";
import type { Layers } from "./model";

function LineSample({ dash, width, color }: { dash?: string; width: number; color: string }) {
  return (
    <svg width="44" height="14" viewBox="0 0 44 14" aria-hidden="true" focusable="false">
      <line x1="3" y1="7" x2="41" y2="7" stroke={color} strokeWidth={width} strokeDasharray={dash} strokeLinecap="round" />
    </svg>
  );
}

function Glyph({ shape, color }: { shape: ShapeName; color: string }) {
  return (
    <svg width="18" height="18" viewBox="0 0 12 12" aria-hidden="true" focusable="false" style={{ color }}>
      <ShapeBody name={shape} />
    </svg>
  );
}

/** Line style and symbol meanings as text, so no meaning depends on colour. */
export function MapLegend({ layers }: { layers: Layers }) {
  const slow = Math.round(FLOW_THRESHOLDS.slowBelow * 100);
  const congested = Math.round(FLOW_THRESHOLDS.congestedBelow * 100);
  return (
    <section aria-label="Map legend" className="legend">
      <h3>Legend</h3>
      <ul>
        {layers.traffic ? (
          <>
            <li>
              <LineSample width={4} color="var(--color-accent)" /> Solid: flowing, speed at least {slow}% of free flow
            </li>
            <li>
              <LineSample width={5} dash="10 6" color="var(--color-warning)" /> Dashed: slow, {congested}% to {slow}% of free flow
            </li>
            <li>
              <LineSample width={6} dash="2 5" color="var(--color-danger-text)" /> Dotted, thick: congested, under {congested}% of free flow
            </li>
            <li>
              <LineSample width={7} color="var(--color-destructive)" /> Thick with a cross: closed by a blocking incident
            </li>
            <li>
              <LineSample width={2.5} dash="1 7" color="var(--color-neutral)" /> Fine dots: no speed reading yet
            </li>
            <li>Faded line or marker: the reading is stale</li>
          </>
        ) : null}
        {layers.devices ? (
          <>
            <li>
              <Glyph shape="circle" color="var(--color-accent)" /> Circle: device reporting
            </li>
            <li>
              <Glyph shape="triangle" color="var(--color-warning)" /> Triangle: device stale
            </li>
            <li>
              <Glyph shape="ring" color="var(--color-neutral)" /> Ring: device never reported
            </li>
          </>
        ) : null}
        {layers.incidents ? (
          <li>
            <Glyph shape="square" color="var(--color-danger-text)" /> Square, triangle, diamond, ring: incident severity critical, high, medium, low
          </li>
        ) : null}
        {layers.emergency ? (
          <>
            <li>
              <Glyph shape="diamond" color="var(--color-info)" /> Diamond with a name: emergency unit
            </li>
            <li>
              <Glyph shape="half" color="var(--color-info)" /> Shape by status: emergency call
            </li>
          </>
        ) : null}
      </ul>
    </section>
  );
}
