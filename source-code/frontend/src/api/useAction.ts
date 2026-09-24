import { useCallback, useRef, useState } from "react";
import { ApiError } from "./client";

export type Outcome<T> = { ok: true; value: T } | { ok: false; error: ApiError };

/**
 * Runs one write at a time. While it is in flight `busy` is true and a second call is ignored (it comes back as "already running"),
 * so a double click or a held Enter cannot submit twice. A refusal comes back as `error` with the server's own code and message;
 * there is no optimistic update.
 */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const inFlight = useRef(false);

  const run = useCallback(async <T,>(work: () => Promise<T>): Promise<Outcome<T>> => {
    if (inFlight.current) return { ok: false, error: new ApiError(0, "busy", "Another action is still running") };
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      return { ok: true, value: await work() };
    } catch (caught) {
      const failure = caught instanceof ApiError ? caught : new ApiError(0, "error", caught instanceof Error ? caught.message : "The action failed");
      setError(failure);
      return { ok: false, error: failure };
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }, []);

  const clear = useCallback(() => setError(null), []);
  return { busy, error, run, clear };
}
