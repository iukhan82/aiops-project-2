import { useEffect, useRef, useState } from "react";
import type { LiveEvent } from "../../api/types";
import { freshToken } from "../../auth/keycloak";
import type { FeedState } from "../../components/Feed";

export interface LiveFeedInfo {
  state: FeedState;
  /** Source time of the newest observation received. */
  newestAt: string | null;
  lastConnectedAt: string | null;
}

interface Options {
  /** False while replaying history: there is nothing live to show. */
  enabled: boolean;
  paused: boolean;
  onEvents: (events: LiveEvent[]) => void;
  /** How far back the first connection asks for, so the screen opens with something. */
  lookbackSeconds?: number;
}

const MAX_BACKOFF_MS = 10_000;
const OFFLINE_AFTER_ATTEMPTS = 5;
const FLUSH_MS = 250;

/**
 * The live observation feed (`/api/v1/live`). The browser cannot set headers on a WebSocket, so the short-lived token rides in the
 * query string. A dropped connection reconnects with growing delay and asks for everything after the last `received_at` it saw, so
 * a reconnect fills the gap instead of skipping it. Pausing closes the socket; resuming catches up the same way.
 */
export function useLiveFeed({ enabled, paused, onEvents, lookbackSeconds = 120 }: Options): LiveFeedInfo {
  const [state, setState] = useState<FeedState>(paused ? "paused" : "reconnecting");
  const [newestAt, setNewestAt] = useState<string | null>(null);
  const [lastConnectedAt, setLastConnectedAt] = useState<string | null>(null);
  const lastReceived = useRef<string | null>(null);
  const handler = useRef(onEvents);
  handler.current = onEvents;

  useEffect(() => {
    if (!enabled) return;
    if (paused) {
      setState("paused");
      return;
    }
    let closedByUs = false;
    let socket: WebSocket | null = null;
    let retry: number | undefined;
    let flush: number | undefined;
    let attempt = 0;
    let batch: LiveEvent[] = [];
    setState("reconnecting");

    const deliver = () => {
      flush = undefined;
      if (batch.length === 0) return;
      const events = batch;
      batch = [];
      handler.current(events);
      const newest = events.reduce((latest, e) => (e.observation_time > latest ? e.observation_time : latest), "");
      setNewestAt((current) => (current && current > newest ? current : newest));
    };

    const scheduleRetry = () => {
      if (closedByUs) return;
      setState(attempt >= OFFLINE_AFTER_ATTEMPTS ? "offline" : "reconnecting");
      retry = window.setTimeout(connect, Math.min(1000 * 2 ** attempt, MAX_BACKOFF_MS));
      attempt += 1;
    };

    async function connect() {
      let token: string;
      try {
        token = await freshToken();
      } catch {
        setState("offline");
        return;
      }
      if (closedByUs) return;
      const since = lastReceived.current ?? new Date(Date.now() - lookbackSeconds * 1000).toISOString();
      const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(`${scheme}//${window.location.host}/api/v1/live?since=${encodeURIComponent(since)}&access_token=${encodeURIComponent(token)}`);
      socket.onopen = () => {
        attempt = 0;
        setState("connected");
        setLastConnectedAt(new Date().toISOString());
      };
      socket.onmessage = (message) => {
        try {
          const event = JSON.parse(String(message.data)) as LiveEvent;
          lastReceived.current = event.received_at;
          batch.push(event);
          flush ??= window.setTimeout(deliver, FLUSH_MS);
        } catch {
          /* a malformed frame is dropped, never shown as data */
        }
      };
      socket.onclose = scheduleRetry;
      socket.onerror = () => socket?.close();
    }

    void connect();
    return () => {
      closedByUs = true;
      window.clearTimeout(retry);
      window.clearTimeout(flush);
      socket?.close();
    };
  }, [enabled, paused, lookbackSeconds]);

  return { state: enabled ? state : "offline", newestAt, lastConnectedAt };
}
