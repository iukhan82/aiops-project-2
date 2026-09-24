import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ageSeconds, ageText } from "../lib/format";
import { Button } from "./Button";
import { useNow } from "./Clock";
import { StatusChip } from "./badges";

export type FeedState = "connected" | "reconnecting" | "paused" | "offline";

export interface FeedInfo {
  state: FeedState;
  /** ISO time of the newest value the screen holds. */
  newestAt: string | null;
  /** When the connection was last good, shown while reconnecting. */
  lastConnectedAt: string | null;
  paused: boolean;
  onTogglePause?: () => void;
}

interface FeedApi {
  info: FeedInfo | null;
  publish: (info: FeedInfo | null) => void;
}

const FeedContext = createContext<FeedApi>({ info: null, publish: () => undefined });

/** A live screen publishes its feed state here; the header shows it once, page-level, never per value. */
export function FeedProvider({ children }: { children: ReactNode }) {
  const [info, setInfo] = useState<FeedInfo | null>(null);
  const publish = useCallback((next: FeedInfo | null) => setInfo(next), []);
  const value = useMemo(() => ({ info, publish }), [info, publish]);
  return <FeedContext.Provider value={value}>{children}</FeedContext.Provider>;
}

export function useFeedPublisher(): (info: FeedInfo | null) => void {
  return useContext(FeedContext).publish;
}

export function LiveFeedStatus() {
  const { info } = useContext(FeedContext);
  const now = useNow();
  if (!info) return null;
  const age = ageSeconds(info.newestAt, now);
  const quantised = age === null ? null : Math.floor(age / 5) * 5;
  return (
    <div className="feed-status" data-feed-state={info.state}>
      <span role="status">
        <StatusChip domain="feed" value={info.state} />
      </span>
      <span className="feed-age">
        {info.state === "reconnecting" || info.state === "offline"
          ? `values may be older than shown; last connected ${info.lastConnectedAt ? ageText(ageSeconds(info.lastConnectedAt, now)) : "never"}`
          : `newest value ${quantised === null ? "not available" : ageText(quantised)}`}
      </span>
      {info.onTogglePause ? (
        <Button variant="ghost" onClick={info.onTogglePause}>
          {info.paused ? "Resume updates" : "Pause updates"}
        </Button>
      ) : null}
    </div>
  );
}

/**
 * Feed state for a screen that polls rather than streams. A screen is "connected" while its requests succeed, "reconnecting" after
 * one or two failures (what is on screen may be older than it looks) and "offline" after three.
 */
export function usePolledFeed(resources: readonly { failures: number; fetchedAt: number | null }[], newestAt: string | null, paused: boolean, onTogglePause: () => void): void {
  const publish = useFeedPublisher();
  const worst = Math.max(0, ...resources.map((r) => r.failures));
  const lastGood = resources.reduce<number | null>((latest, r) => (r.fetchedAt !== null && (latest === null || r.fetchedAt > latest) ? r.fetchedAt : latest), null);
  const state: FeedState = paused ? "paused" : worst === 0 ? "connected" : worst < 3 ? "reconnecting" : "offline";
  const lastConnectedAt = lastGood === null ? null : new Date(lastGood).toISOString();
  useEffect(() => {
    publish({ state, newestAt, lastConnectedAt, paused, onTogglePause });
    return () => publish(null);
  }, [publish, state, newestAt, lastConnectedAt, paused, onTogglePause]);
}
