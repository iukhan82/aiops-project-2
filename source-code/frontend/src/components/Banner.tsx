import type { ReactNode } from "react";
import { Shape } from "./Shape";

const SHAPE = { info: "diamond", warn: "triangle", danger: "square" } as const;

/** One page-level message. `role=alert` only for the failure of something the user just did. */
export function Banner({ tone = "info", children, action, alert = false }: { tone?: "info" | "warn" | "danger"; children: ReactNode; action?: ReactNode; alert?: boolean }) {
  return (
    <div className={`banner tone-${tone === "warn" ? "warn" : tone === "danger" ? "danger" : "info"}`} role={alert ? "alert" : "status"}>
      <Shape name={SHAPE[tone]} />
      <div className="banner-text">{children}</div>
      {action ? <div className="banner-action">{action}</div> : null}
    </div>
  );
}
