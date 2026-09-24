import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./client";

export interface ApiResource<T> {
  data: T | null;
  error: unknown;
  loading: boolean;
  /** When the data on screen was fetched. On an error the last data stays visible and `error` is set (stale). */
  fetchedAt: number | null;
  /** Consecutive failed fetches; 0 after any success. */
  failures: number;
  reload: () => void;
}

type Query = Record<string, string | number | boolean | undefined | null>;

/** GET a JSON resource, optionally polling. The last good data is kept when a refresh fails, and marked via `error`. */
export function useApi<T>(path: string | null, query?: Query, refreshMs?: number): ApiResource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(path !== null);
  const [fetchedAt, setFetchedAt] = useState<number | null>(null);
  const [tick, setTick] = useState(0);
  const [failures, setFailures] = useState(0);
  const queryKey = JSON.stringify(query ?? {});
  const isFirst = useRef(true);
  const lastKey = useRef<string | null>(null);

  useEffect(() => {
    if (path === null) return;
    const key = `${path}?${queryKey}`;
    if (lastKey.current !== key) {
      lastKey.current = key;
      isFirst.current = true;
      setData(null);
      setError(null);
      setFailures(0);
      setLoading(true);
    }
    const controller = new AbortController();
    api<T>(path, { query: JSON.parse(queryKey) as Query, signal: controller.signal })
      .then((result) => {
        setData(result);
        setError(null);
        setFailures(0);
        setFetchedAt(Date.now());
      })
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(err);
        setFailures((n) => n + 1);
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          isFirst.current = false;
          setLoading(false);
        }
      });
    return () => controller.abort();
  }, [path, queryKey, tick]);

  useEffect(() => {
    if (!refreshMs || path === null) return;
    const id = window.setInterval(() => setTick((n) => n + 1), refreshMs);
    return () => window.clearInterval(id);
  }, [refreshMs, path]);

  const reload = useCallback(() => setTick((n) => n + 1), []);
  return { data, error, loading, fetchedAt, failures, reload };
}
