import type { RouteAlternative } from "../api/types";
import { number, words } from "../lib/format";
import { Button } from "./Button";
import { TruthBadge } from "./badges";
import { Shape } from "./Shape";

/**
 * One route alternative: ETA with its uncertainty and the truth label "predicted", distance, and the constraints applied. Which one is
 * selected is written in words and outlined, and choosing another is an explicit button, never a side effect of looking.
 */
export function RouteCard({ route, index, canSelect, busy, onSelect }: { route: RouteAlternative; index: number; canSelect: boolean; busy?: boolean; onSelect?: () => void }) {
  const constraints = route.constraints_applied ?? [];
  return (
    <li className={`route-card${route.selected ? " selected" : ""}`} data-route-id={route.route_id} data-selected={route.selected}>
      <h4>
        Route {index + 1}
        {route.selected ? (
          <span className="chip tone-ok">
            <Shape name="check" />
            <span>Selected</span>
          </span>
        ) : (
          <span className="muted"> alternative</span>
        )}
      </h4>
      <dl className="kv kv-dense">
        <div className="kv-row">
          <dt>Estimated time</dt>
          <dd>
            <span className="kv-value">
              {number(route.eta_seconds, 0, "s")} plus or minus {number(route.eta_uncertainty_seconds, 0, "s")}
            </span>
            <span className="kv-extra">
              <TruthBadge label="predicted" />
            </span>
          </dd>
        </div>
        <div className="kv-row">
          <dt>Distance</dt>
          <dd className="kv-value">{number(route.distance_m, 0, "m")}</dd>
        </div>
        <div className="kv-row">
          <dt>Constraints</dt>
          <dd>{constraints.length === 0 ? "None applied" : constraints.map((c) => words(c.replace(/:/g, " "))).join(", ")}</dd>
        </div>
      </dl>
      {canSelect && !route.selected && onSelect ? (
        <Button variant="secondary" busy={busy} onClick={onSelect}>
          Select route {index + 1}
        </Button>
      ) : null}
    </li>
  );
}
