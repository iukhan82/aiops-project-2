import { useId, useMemo, useState, type KeyboardEvent } from "react";
import { number } from "../lib/format";
import type { ShapeName } from "../lib/status";
import { Button } from "./Button";
import { DataTable, type Column } from "./DataTable";
import { ShapeBody } from "./Shape";

export interface ChartPoint {
  t: number;
  v: number | null;
}

export interface ChartSeries {
  id: string;
  label: string;
  points: ChartPoint[];
  /** Series are told apart by dash pattern and marker shape as well as colour. */
  dash?: string;
  marker: ShapeName;
  color: string;
}

export interface ChartBand {
  label: string;
  points: { t: number; lo: number; hi: number }[];
}

interface Props {
  title: string;
  unit: string;
  digits?: number;
  series: readonly ChartSeries[];
  band?: ChartBand | null;
  /** A vertical rule marking the present, so history and forecast are never confused. */
  nowMs?: number;
  /** Consecutive points further apart than this are drawn as a gap, never joined. */
  gapMs?: number;
  /** Why there is nothing to draw, or nothing forecast. */
  emptyText?: string;
  paused?: boolean;
  onTogglePause?: () => void;
  note?: string;
}

const W = 820;
const H = 330;
const M = { left: 70, right: 18, top: 16, bottom: 42 };

const pad2 = (n: number) => String(n).padStart(2, "0");
const hhmm = (t: number) => `${pad2(new Date(t).getUTCHours())}:${pad2(new Date(t).getUTCMinutes())}`;

function niceStep(range: number, ticks: number): number {
  const raw = range / ticks;
  const exponent = Math.floor(Math.log10(raw));
  const base = raw / 10 ** exponent;
  const nice = base < 1.5 ? 1 : base < 3.5 ? 2 : base < 7.5 ? 5 : 10;
  return nice * 10 ** exponent;
}

/**
 * A labelled time-series chart with a real table one toggle away. Missing data is drawn as a gap, an uncertainty band carries its
 * label, and the keyboard moves a crosshair through the points with the value written out under the chart.
 */
export function LineChart({ title, unit, digits = 1, series, band, nowMs, gapMs, emptyText, paused, onTogglePause, note }: Props) {
  const id = useId();
  const [asTable, setAsTable] = useState(false);
  const [cursor, setCursor] = useState<number | null>(null);

  const times = useMemo(() => {
    const set = new Set<number>();
    for (const s of series) for (const p of s.points) set.add(p.t);
    for (const p of band?.points ?? []) set.add(p.t);
    return [...set].sort((a, b) => a - b);
  }, [series, band]);

  const geometry = useMemo(() => {
    const values = series.flatMap((s) => s.points.map((p) => p.v).filter((v): v is number => v !== null));
    for (const p of band?.points ?? []) values.push(p.lo, p.hi);
    if (times.length === 0 || values.length === 0) return null;
    const tMin = times[0]!;
    const tMax = times[times.length - 1]!;
    const dataMin = Math.min(...values);
    const dataMax = Math.max(...values);
    const yMin = dataMin >= 0 ? 0 : dataMin;
    const step = niceStep(Math.max(dataMax - yMin, 1e-9), 5);
    const yMax = Math.ceil((dataMax * 1.05) / step) * step || step;
    const x = (t: number) => M.left + (tMax === tMin ? (W - M.left - M.right) / 2 : ((t - tMin) / (tMax - tMin)) * (W - M.left - M.right));
    const y = (v: number) => M.top + (1 - (v - yMin) / (yMax - yMin || 1)) * (H - M.top - M.bottom);
    const yTicks: number[] = [];
    for (let v = yMin; v <= yMax + step / 2; v += step) yTicks.push(v);
    const xTicks: number[] = [];
    const count = Math.min(6, times.length);
    for (let i = 0; i < count; i += 1) xTicks.push(times[Math.round((i * (times.length - 1)) / Math.max(count - 1, 1))]!);
    return { tMin, tMax, x, y, yTicks, xTicks: [...new Set(xTicks)] };
  }, [series, band, times]);

  const rows = useMemo(
    () =>
      times.map((t) => ({
        t,
        values: Object.fromEntries(series.map((s) => [s.id, s.points.find((p) => p.t === t)?.v ?? null])) as Record<string, number | null>,
        band: band?.points.find((p) => p.t === t) ?? null,
      })),
    [times, series, band],
  );

  const text = (row: (typeof rows)[number]): string => {
    const parts = series.map((s) => `${s.label} ${row.values[s.id] === null || row.values[s.id] === undefined ? "no value" : number(row.values[s.id], digits, unit)}`);
    if (row.band) parts.push(`${band?.label}: ${number(row.band.lo, digits)} to ${number(row.band.hi, digits, unit)}`);
    return `${hhmm(row.t)} UTC: ${parts.join("; ")}`;
  };

  const latest = rows.length - 1;
  const current = cursor === null ? latest : Math.min(cursor, latest);
  const summary =
    rows.length === 0
      ? (emptyText ?? "No data to draw.")
      : `${title}. ${rows.length} points from ${hhmm(times[0]!)} to ${hhmm(times[times.length - 1]!)} UTC. Latest: ${text(rows[latest]!)}.`;

  const onKeyDown = (event: KeyboardEvent) => {
    if (rows.length === 0) return;
    if (event.key === "ArrowLeft") setCursor(Math.max(0, current - 1));
    else if (event.key === "ArrowRight") setCursor(Math.min(latest, current + 1));
    else if (event.key === "Home") setCursor(0);
    else if (event.key === "End") setCursor(latest);
    else return;
    event.preventDefault();
  };

  const columns: Column<(typeof rows)[number]>[] = [
    { key: "time", header: "Time (UTC)", render: (r) => `${hhmm(r.t)}`, sortValue: (r) => r.t },
    ...series.map<Column<(typeof rows)[number]>>((s) => ({
      key: s.id,
      header: `${s.label} (${unit})`,
      numeric: true,
      render: (r) => (r.values[s.id] === null || r.values[s.id] === undefined ? "no value" : number(r.values[s.id], digits)),
    })),
    ...(band ? [{ key: "band", header: band.label, numeric: true, render: (r: (typeof rows)[number]) => (r.band ? `${number(r.band.lo, digits)} to ${number(r.band.hi, digits)}` : "not forecast") } as Column<(typeof rows)[number]>] : []),
  ];

  const segments = (points: ChartPoint[]): ChartPoint[][] => {
    const out: ChartPoint[][] = [];
    let run: ChartPoint[] = [];
    let previous: ChartPoint | null = null;
    for (const p of points) {
      if (p.v === null || (previous && gapMs && p.t - previous.t > gapMs)) {
        if (run.length) out.push(run);
        run = [];
      }
      if (p.v !== null) run.push(p);
      previous = p.v === null ? null : p;
    }
    if (run.length) out.push(run);
    return out;
  };

  return (
    <figure className="chart" data-testid="line-chart">
      <figcaption className="chart-head">
        <span className="chart-title">{title}</span>
        <span className="chart-actions">
          {onTogglePause ? (
            <Button variant="secondary" onClick={onTogglePause} aria-pressed={Boolean(paused)}>
              {paused ? "Resume updates" : "Pause updates"}
            </Button>
          ) : null}
          <Button variant="secondary" onClick={() => setAsTable((v) => !v)} aria-pressed={asTable}>
            {asTable ? "Show as chart" : "Show as table"}
          </Button>
        </span>
      </figcaption>
      {paused ? <p className="chart-paused">Paused. The chart is not being updated.</p> : null}
      {rows.length === 0 || !geometry ? (
        <div role="status" className="state">
          <p>{emptyText ?? "No data to draw."}</p>
        </div>
      ) : asTable ? (
        <DataTable caption={`${title}: the same values as the chart`} columns={columns} rows={[...rows].reverse()} rowKey={(r) => String(r.t)} />
      ) : (
        <>
          <svg
            className="chart-svg"
            viewBox={`0 0 ${W} ${H}`}
            role="img"
            aria-label={summary}
            tabIndex={0}
            onKeyDown={onKeyDown}
            onFocus={() => setCursor((c) => c ?? latest)}
            onBlur={() => setCursor(null)}
            aria-describedby={`${id}-selected`}
          >
            {geometry.yTicks.map((v) => (
              <g key={v}>
                <line x1={M.left} x2={W - M.right} y1={geometry.y(v)} y2={geometry.y(v)} className="grid-line" />
                <text x={M.left - 8} y={geometry.y(v) + 4} textAnchor="end" className="axis-text">
                  {number(v, v % 1 === 0 ? 0 : 1)}
                </text>
              </g>
            ))}
            {geometry.xTicks.map((t) => (
              <text key={t} x={geometry.x(t)} y={H - M.bottom + 18} textAnchor="middle" className="axis-text">
                {hhmm(t)}
              </text>
            ))}
            <text x={M.left} y={H - 6} className="axis-text">
              Time (UTC)
            </text>
            <text x={14} y={M.top + 10} className="axis-text" transform={`rotate(-90 14 ${M.top + 10})`} textAnchor="end">
              {unit}
            </text>
            {band && band.points.length > 1 ? (
              <g>
                <path
                  d={`M ${band.points.map((p) => `${geometry.x(p.t)} ${geometry.y(p.hi)}`).join(" L ")} L ${[...band.points].reverse().map((p) => `${geometry.x(p.t)} ${geometry.y(p.lo)}`).join(" L ")} Z`}
                  className="band"
                />
              </g>
            ) : null}
            {nowMs !== undefined && nowMs >= geometry.tMin && nowMs <= geometry.tMax ? (
              <g>
                <line x1={geometry.x(nowMs)} x2={geometry.x(nowMs)} y1={M.top} y2={H - M.bottom} className="now-line" />
                <text x={geometry.x(nowMs) + 6} y={M.top + 12} className="axis-text">
                  now
                </text>
              </g>
            ) : null}
            {series.map((s) => (
              <g key={s.id} style={{ color: s.color }}>
                {segments(s.points).map((run, index) => (
                  <polyline key={index} points={run.map((p) => `${geometry.x(p.t)},${geometry.y(p.v as number)}`).join(" ")} fill="none" stroke={s.color} strokeWidth={2.5} strokeDasharray={s.dash} strokeLinejoin="round" />
                ))}
                {s.points.length <= 80
                  ? s.points.map((p) =>
                      p.v === null ? null : (
                        <g key={p.t} transform={`translate(${geometry.x(p.t) - 5} ${geometry.y(p.v) - 5}) scale(${10 / 12})`}>
                          <ShapeBody name={s.marker} />
                        </g>
                      ),
                    )
                  : null}
              </g>
            ))}
            {cursor !== null && rows[current] ? (
              <line x1={geometry.x(rows[current]!.t)} x2={geometry.x(rows[current]!.t)} y1={M.top} y2={H - M.bottom} className="crosshair" />
            ) : null}
          </svg>
          <p id={`${id}-selected`} className="chart-selected" aria-live="polite" data-testid="chart-selected">
            {rows[current] ? text(rows[current]!) : ""}
          </p>
          <ul className="chart-legend" aria-label="Chart legend">
            {series.map((s) => (
              <li key={s.id}>
                <svg width="46" height="14" viewBox="0 0 46 14" aria-hidden="true" focusable="false" style={{ color: s.color }}>
                  <line x1="2" y1="7" x2="44" y2="7" stroke={s.color} strokeWidth={2.5} strokeDasharray={s.dash} />
                  <g transform="translate(18 2) scale(0.83)">
                    <ShapeBody name={s.marker} />
                  </g>
                </svg>
                {s.label}
              </li>
            ))}
            {band ? (
              <li>
                <svg width="46" height="14" viewBox="0 0 46 14" aria-hidden="true" focusable="false">
                  <rect x="2" y="2" width="42" height="10" className="band" />
                </svg>
                {band.label}
              </li>
            ) : null}
          </ul>
        </>
      )}
      {note ? <p className="muted chart-note">{note}</p> : null}
    </figure>
  );
}
