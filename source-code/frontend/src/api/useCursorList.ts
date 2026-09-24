import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Page } from "./client";

type Query = Record<string, string | number | boolean | undefined | null>;

export interface CursorList<T> {
  items: T[];
  hasMore: boolean;
  loading: boolean;
  loadingMore: boolean;
  error: unknown;
  failures: number;
  fetchedAt: number | null;
  loadMore: () => void;
  reload: () => void;
}

const MAX_PAGES_REFRESHED = 10;

/**
 * A cursor-paginated list ("Load more"). A refresh re-reads every page loaded so far, so what is on screen is consistent and never a
 * mix of old and new pages. On failure the rows already loaded stay, and `error` says why they may be out of date.
 */
export function useCursorList<T>(path: string | null, query: Query, refreshMs?: number, pageSize = 50): CursorList<T> {
  const [items, setItems] = useState<T[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [failures, setFailures] = useState(0);
  const [fetchedAt, setFetchedAt] = useState<number | null>(null);
  const [tick, setTick] = useState(0);
  const loaded = useRef(1);
  const queryKey = JSON.stringify({ path, query, pageSize });
  const lastKey = useRef<string | null>(null);

  useEffect(() => {
    if (path === null) return;
    const controller = new AbortController();
    if (lastKey.current !== queryKey) {
      lastKey.current = queryKey;
      loaded.current = 1;
      setItems([]);
      setCursor(null);
      setLoading(true);
      setFailures(0);
    }
    (async () => {
      try {
        const collected: T[] = [];
        let next: string | null = null;
        for (let page = 0; page < Math.min(loaded.current, MAX_PAGES_REFRESHED); page += 1) {
          const result: Page<T> = await api<Page<T>>(path, { query: { ...query, limit: pageSize, cursor: next }, signal: controller.signal });
          collected.push(...result.items);
          next = result.next_cursor;
          if (!next) break;
        }
        setItems(collected);
        setCursor(next);
        setError(null);
        setFailures(0);
        setFetchedAt(Date.now());
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(err);
        setFailures((n) => n + 1);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryKey, tick]);

  useEffect(() => {
    if (!refreshMs || path === null) return;
    const id = window.setInterval(() => setTick((n) => n + 1), refreshMs);
    return () => window.clearInterval(id);
  }, [refreshMs, path]);

  const loadMore = useCallback(() => {
    if (path === null || !cursor || loadingMore) return;
    setLoadingMore(true);
    api<Page<T>>(path, { query: { ...query, limit: pageSize, cursor } })
      .then((result) => {
        loaded.current += 1;
        setItems((current) => [...current, ...result.items]);
        setCursor(result.next_cursor);
        setError(null);
        setFetchedAt(Date.now());
      })
      .catch((err) => setError(err))
      .finally(() => setLoadingMore(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, cursor, loadingMore, queryKey]);

  const reload = useCallback(() => setTick((n) => n + 1), []);
  return { items, hasMore: cursor !== null, loading, loadingMore, error, failures, fetchedAt, loadMore, reload };
}
