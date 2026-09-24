import type { ComponentType } from "react";
import { AuditPage } from "./AuditPage";
import { CorridorAnalyticsPage } from "./CorridorAnalyticsPage";
import { CommandDetailPage } from "./CommandDetailPage";
import { CommandsPage } from "./CommandsPage";
import { DemoPage } from "./DemoPage";
import { DeviceHealthPage } from "./DeviceHealthPage";
import { DispatchDetailPage } from "./DispatchDetailPage";
import { DispatchPage } from "./DispatchPage";
import { FieldPage } from "./FieldPage";
import { HandoverPage } from "./HandoverPage";
import { IncidentDetailPage } from "./IncidentDetailPage";
import { IncidentsPage } from "./IncidentsPage";
import { IntersectionAnalyticsPage } from "./IntersectionAnalyticsPage";
import { MapPage } from "./MapPage";
import { OperationsPage } from "./OperationsPage";
import { OutcomesPage } from "./OutcomesPage";
import { RecommendationsPage } from "./RecommendationsPage";

export const PAGES: Record<string, ComponentType> = {
  map: MapPage,
  incidents: IncidentsPage,
  dispatch: DispatchPage,
  "dispatch-detail": DispatchDetailPage,
  field: FieldPage,
  "actions-recommendations": RecommendationsPage,
  "actions-commands": CommandsPage,
  "actions-command-detail": CommandDetailPage,
  "actions-outcomes": OutcomesPage,
  "incident-detail": IncidentDetailPage,
  "analytics-corridors": CorridorAnalyticsPage,
  "analytics-intersections": IntersectionAnalyticsPage,
  "analytics-devices": DeviceHealthPage,
  audit: AuditPage,
  operations: OperationsPage,
  handover: HandoverPage,
  demo: DemoPage,
};
