import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import { Button } from "./Button";

/** Shows nothing for the first 300 ms, so a fast response never flashes a skeleton (docs/ux/DESIGN_SYSTEM.md). */
function useAfter(ms: number): boolean {
  const [elapsed, setElapsed] = useState(false);
  useEffect(() => {
    const id = window.setTimeout(() => setElapsed(true), ms);
    return () => window.clearTimeout(id);
  }, [ms]);
  return elapsed;
}

export function LoadingState({ what = "data" }: { what?: string }) {
  const show = useAfter(300);
  return (
    <div role="status" className="state state-loading" aria-busy="true">
      {show ? (
        <>
          <div className="skeleton" aria-hidden="true" />
          <div className="skeleton short" aria-hidden="true" />
          <p>Loading {what}...</p>
        </>
      ) : (
        <span className="sr-only">Loading {what}</span>
      )}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div role="status" className="state state-empty">
      <h2>{title}</h2>
      {children ? <p>{children}</p> : null}
    </div>
  );
}

export function ErrorState({ what, error, onRetry }: { what: string; error: unknown; onRetry?: () => void }) {
  const api = error instanceof ApiError ? error : null;
  return (
    <div role="alert" className="state state-error">
      <h2>{what} could not be loaded</h2>
      <p>{api ? api.message : "An unexpected error occurred."}</p>
      {api ? (
        <p className="muted">
          Code <code>{api.code}</code>
          {api.status ? `, HTTP ${api.status}` : ""}. {api.retryable ? "This may be temporary." : "Retrying will not change this."}
        </p>
      ) : null}
      {onRetry ? <Button onClick={onRetry}>Retry</Button> : null}
    </div>
  );
}

export function ForbiddenState({ capability, screen }: { capability: string | null; screen?: string }) {
  return (
    <div role="status" className="state state-forbidden">
      <h2>Not permitted</h2>
      <p>
        {screen ? `The ${screen} screen` : "This screen"} needs the <code>{capability ?? "unknown"}</code> capability, which your role does not hold.
      </p>
      <p>
        <Link to="/">Go to your home screen</Link>
      </p>
    </div>
  );
}
