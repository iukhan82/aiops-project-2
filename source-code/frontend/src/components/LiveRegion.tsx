import { useEffect, useRef, useState } from "react";
import { Button } from "./Button";

const ANNOUNCE_EVERY_MS = 5000;

/**
 * Recent updates, announced politely and at most once every five seconds, with a Pause control. A live update never moves focus.
 * `entries` are the newest few lines (already formatted); `total` counts everything received, so the announcement can say how many.
 */
export function LiveRegion({ label, entries, total, paused, onTogglePause }: { label: string; entries: readonly string[]; total: number; paused: boolean; onTogglePause: () => void }) {
  const [announcement, setAnnouncement] = useState("");
  const announcedTotal = useRef(0);
  const latest = useRef({ total, paused });
  latest.current = { total, paused };

  useEffect(() => {
    const id = window.setInterval(() => {
      const { total: current, paused: isPaused } = latest.current;
      const fresh = current - announcedTotal.current;
      if (fresh > 0 && !isPaused) {
        setAnnouncement(`${fresh} new ${fresh === 1 ? "update" : "updates"}`);
        announcedTotal.current = current;
      }
    }, ANNOUNCE_EVERY_MS);
    return () => window.clearInterval(id);
  }, []);

  return (
    <section className="live-region" aria-label={label}>
      <div className="panel-head">
        <h3>{label}</h3>
        <Button variant="secondary" onClick={onTogglePause} aria-pressed={paused}>
          {paused ? "Resume updates" : "Pause updates"}
        </Button>
      </div>
      <p className="sr-only" aria-live="polite" aria-atomic="false">
        {announcement}
      </p>
      {entries.length === 0 ? (
        <p className="muted">{paused ? "Updates are paused." : "Nothing new yet."}</p>
      ) : (
        <ol className="log">
          {entries.map((line, index) => (
            <li key={`${index}-${line}`}>{line}</li>
          ))}
        </ol>
      )}
    </section>
  );
}
