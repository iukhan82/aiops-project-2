# Design tokens, components and interaction handoff

Task P08.03. This is what a developer needs to build the operator UI without
guessing: the tokens, the components with their states, keyboard behaviour and
ARIA, how charts and tables pair, how every backend state is shown, and which
API and role each screen depends on. It extends
`design-system/intelligent-traffic-and-emergency-response-platform/MASTER.md`; where
this document departs from that file it says so and shows the measurement.

Everything mechanical is single-sourced and machine-checked:

| Source | Generates or is checked by |
|---|---|
| `source-code/frontend/src/design/tokens.json` | `tokens.css` (generated), the contrast table below |
| `source-code/frontend/src/design/status.json` | the status vocabulary below; checked against every backend enumeration |
| `source-code/frontend/src/config/inventory.json` | the screen, API and role tables (P08.01) |
| this file's component sections | checked for the seven required fields and for real screen and token references |

`python source-code/frontend/tools/design.py --build` regenerates
`tokens.css` and the tables below; `python source-code/frontend/tools/design.py`
verifies everything and writes evidence.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Framework | React 19 with TypeScript, built by Vite | `source-code/frontend/README.md` already names React; no ADR fixes anything else; component state and the live feed fit it; Vite is fast to test in a real browser |
| Styling | plain CSS using the generated custom properties, no CSS framework | one source of tokens, no utility-class drift, nothing to purge; the design system is small |
| Map | a hand-drawn SVG schematic, not MapLibre | the network is 12 intersections and 26 segments with no basemap tiles available offline; SVG elements are natively focusable, labelled and testable, which a WebGL canvas is not; MapLibre stays the option if the network grows past what a schematic can hold. This departs from the earlier plan text in `frontend/README.md` and is recorded here |
| Icons | inline SVG shapes from the status vocabulary, no emoji, no icon font | shapes are semantic (status), so they are defined once and tested |
| Fonts | IBM Plex Sans (headings), Inter (body), IBM Plex Mono (data), self-hosted with system fallbacks | the control room must not depend on the public internet (MASTER.md) |
| Auth | `keycloak-js`, Authorization Code with PKCE (P08.04) | the UI never handles a password |
| Theme | one dark control-room theme | MASTER.md fixes it; a light theme is out of scope and adds no requirement |

## Principles (from the design system, made testable)

1. Every live value shows its source time and a stale or unknown state.
2. Status is a shape plus a word, never colour alone.
3. Real-time charts have pause and resume, a current-value summary, keyboard
   access and a table alternative.
4. Forecasts show actual against predicted, the uncertainty and the model version.
5. A high-impact command shows target, reason, expected effect, constraints,
   expiry, approval state, execution progress, acknowledgement and outcome.
6. Repeated submission is disabled while an action executes; there is no silent
   success.
7. Safety text and action names wrap; they are never truncated with an ellipsis.
8. Motion is never used for urgency: critical events use persistent structure and
   readable labels, sound only when configurable, and an acknowledgement state.
9. Complex control is desktop-first; the field view is status and route only.

## Interaction handoff

**Focus.** The focus indicator is the two-tone ring (`--color-ring` outside,
`--color-ring-inner` inside, `--size-focus-width` each), never removed and never
below 3:1 on any surface. On a route change focus moves to the page's `h1`; on
opening a dialog it moves to the safe control (Cancel); on closing it returns to
the opener; deleting or removing an item moves focus to the next sensible item.
A live update never moves focus.

**Keyboard.** Tab order is DOM order and DOM order is reading order; no positive
`tabindex`. Composite widgets (tabs, segmented control, menu, table with row
selection) use one Tab stop and arrow keys inside. Enter and Space activate;
Escape closes dialogs and menus; nothing traps focus except a modal dialog. The
first stop on every screen is "Skip to main content".

**Live regions.** Updates are announced through one `aria-live="polite"` region per
screen, batched to at most one announcement every 5 seconds, with a Pause control
that also pauses the visual updates. `aria-live="assertive"` is used only for the
failure of an action the user just took. Reconnecting and stale are page-level
banners, not repeated per value.

**Forms.** Labels are always visible and programmatically associated; a
placeholder is never the label. Required fields say "required" in words. Errors
appear inline next to the field and in a summary that receives focus on a failed
submit; validation runs on submit and on blur, never on every keystroke.

**Critical actions (UX-03).** Approve, deny, request and any resolve use the
`ConfirmDialog` pattern: it restates target, class, reason, expected benefit and
harm, constraints, expiry and policy status, defaults focus to Cancel, and
disables Approve while the request is in flight. The result is shown in place -
success, denial (with the policy reason), failure (with the error code and
whether it is retryable) - and announced assertively. There is no optimistic UI
for commands: the state shown is the state the server returned.

**Loading, empty, error, forbidden, stale.** A skeleton appears after 300 ms of
loading, not before. An empty state says why it is empty and what would fill it. An
error state names what failed, offers retry, and keeps the last known data visible
marked stale. Forbidden names the missing capability. Every state in
`docs/ux/ROLE_JOURNEYS_AND_SCREEN_INVENTORY.md` has one of these.

**Formatting.** All times are UTC, shown as `HH:MM:SS UTC` with an age ("3 s ago")
and the full ISO time available to assistive technology. Every number has its
unit. Live metrics, timestamps, speeds, counts and ETAs use tabular numerals
(`--typography-numeric`). Numbers use one fixed format, not the browser locale.

**Motion.** Durations come from tokens. Under `prefers-reduced-motion: reduce` all
transitions and animations collapse to the final state and nothing auto-moves;
live charts render a static snapshot with a manual refresh. No content flashes.

**Zoom and reflow.** Sizes are in `rem`; the layout survives 200% text zoom and
reflows to one column at 320 CSS px (400% zoom) with no two-dimensional page
scroll - only the map and wide tables scroll, each in a labelled container.
Measured in P08.10 on every screen at 1366x768, 1920x1080, 390x844, 640x512 (200%) and
320x256 (400%): no sideways page scroll and no visible content outside the viewport that is
not inside its own scroll container (`e2e/acceptance.spec.ts`). Below 520 px of height
(a short viewport: 200-400% zoom, a phone on its side) the sticky header and the fixed
bottom bar scroll with the page instead - found by the 400% check, where they covered the
whole viewport and the control under them could not be reached (WCAG 2.4.11). The
longest confirmation dialogs keep their controls reachable at 320x256, by scrolling in
the dialog and by Tab.

**Identity and separation.** The demo operator's identity is announced on every screen by
a `DEMO IDENTITY` tag in the header, and the demo screen carries its own banner; the
demonstration controls are a separate service with their own audit trail, so nothing on
them can be mistaken for an operational action.

**Failing identity provider.** A token that cannot be renewed is one of two different
problems and the screen says which: the session ended (sign in again) or the sign-in
service cannot be reached (wait, then try again). Both keep the destination. A page opened
while the provider is down shows the sign-in screen with the reason instead of leaving the
person on the browser's own error page.

## Charts and tables

- A chart never stands alone: a `Show as table` toggle, one Tab stop away, renders
  the same series as a real table, and the chart has an accessible name and a
  text summary of the current value and the trend.
- Series are told apart by dash pattern and marker shape as well as colour; no
  more than four series share a chart.
- A forecast draws actual and predicted as different line styles, the uncertainty
  as a labelled band with its coverage (for example "80% interval"), and names the
  model version and truth label `predicted`. When the model abstained, the band is
  absent and the chart says why.
- Real-time charts have Pause and Resume, show the time of the newest point, and
  mark gaps as gaps - never interpolate across missing data.
- Heatmaps carry a numeric legend and a table view.
- Axes always show units; the y-axis never starts at a value that exaggerates a
  small change without saying so.

## Responsive behaviour

| Class | Width | Behaviour |
|---|---|---|
| Field | up to 767 px | one column, bottom navigation, 44 px targets, read-only |
| Compact | 768-1279 px | one main column; the right panel drops below; navigation collapses to a labelled menu button |
| Laptop | 1280 px and up | full layout; the reference |
| Projector | 1800 px and up | large type, read-only wall display variant |

## Screen composition

Which components each screen is built from. Every inventory screen appears.

| Screen | Components |
|---|---|
| `login` | `AppShell`, `Button`, `Banner`, `StateViews` |
| `forbidden` | `AppShell`, `StateViews`, `Button` |
| `session-expired` | `AppShell`, `StateViews`, `Button` |
| `not-found` | `AppShell`, `StateViews`, `Button` |
| `map` | `AppShell`, `LiveFeedStatus`, `SegmentedControl`, `LayerToggle`, `MapCanvas`, `MapLegend`, `KeyValueList`, `DataTable`, `StatusChip`, `FreshnessBadge`, `TruthBadge`, `LiveRegion`, `Banner`, `Timeline` |
| `analytics-corridors` | `AppShell`, `LiveFeedStatus`, `Tabs`, `LineChart`, `DataTable`, `KeyValueList`, `FreshnessBadge`, `TruthBadge`, `Banner`, `Select` |
| `analytics-intersections` | `AppShell`, `LiveFeedStatus`, `Tabs`, `KeyValueList`, `DataTable`, `StatusChip`, `FreshnessBadge`, `TruthBadge` |
| `analytics-devices` | `AppShell`, `LiveFeedStatus`, `DataTable`, `StatusChip`, `FreshnessBadge`, `TruthBadge`, `Select`, `Banner` |
| `incidents` | `AppShell`, `LiveFeedStatus`, `DataTable`, `StatusChip`, `Select`, `Pagination`, `LiveRegion` |
| `incident-detail` | `AppShell`, `LiveFeedStatus`, `Stepper`, `Timeline`, `KeyValueList`, `StatusChip`, `TruthBadge`, `MapCanvas`, `Button`, `TextField`, `ConfirmDialog`, `CommandLifecycle` |
| `dispatch` | `AppShell`, `LiveFeedStatus`, `DataTable`, `StatusChip`, `Select`, `Dialog`, `TextField`, `Button` |
| `dispatch-detail` | `AppShell`, `LiveFeedStatus`, `RouteCard`, `MapCanvas`, `Stepper`, `Timeline`, `CommandLifecycle`, `KeyValueList`, `Button`, `ConfirmDialog` |
| `field` | `AppShell`, `LiveFeedStatus`, `Stepper`, `KeyValueList`, `RouteCard`, `StatusChip`, `FreshnessBadge`, `Banner` |
| `actions-recommendations` | `AppShell`, `LiveFeedStatus`, `DataTable`, `StatusChip`, `KeyValueList`, `Button`, `ConfirmDialog`, `TruthBadge` |
| `actions-commands` | `AppShell`, `LiveFeedStatus`, `DataTable`, `StatusChip`, `Select`, `Button`, `ConfirmDialog`, `Pagination` |
| `actions-command-detail` | `AppShell`, `LiveFeedStatus`, `CommandLifecycle`, `Timeline`, `KeyValueList`, `StatusChip`, `ConfirmDialog`, `Banner` |
| `actions-outcomes` | `AppShell`, `DataTable`, `StatusChip`, `KeyValueList`, `TruthBadge` |
| `audit` | `AppShell`, `DataTable`, `Select`, `TextField`, `Pagination` |
| `operations` | `AppShell`, `LiveFeedStatus`, `KeyValueList`, `StatusChip`, `FreshnessBadge`, `Banner`, `DataTable` |
| `handover` | `AppShell`, `DataTable`, `TextField`, `Button`, `ConfirmDialog`, `TruthBadge` |
| `demo` | `AppShell`, `Banner`, `Button`, `DataTable`, `StatusChip`, `ConfirmDialog` |

## Components

Each component lists the seven fields a developer needs. `Tokens` names the custom
properties it reads; `Used by` names the inventory screens.

### AppShell

- **Purpose**: the page frame and the four landmarks every screen shares.
- **Anatomy**: `SkipLink`; `Header` (product name, `LiveFeedStatus`, "SIMULATED" badge, user menu); `NavMenu` (only the screens the role's capabilities allow); `main` with one `h1`; optional complementary side panel.
- **States**: default; compact (navigation collapses to a menu button); field (bottom navigation); loading (skeleton main); signed-out redirect.
- **Keyboard**: skip link first; navigation is one Tab stop with arrow keys; the menu button opens with Enter or Space and closes with Escape.
- **ARIA**: `header role=banner`, `nav aria-label="Primary"`, `main`, `aside aria-label`; current page `aria-current="page"`; one `h1` per page.
- **Tokens**: `--color-background`, `--color-card`, `--color-border`, `--size-nav-width`, `--size-header-height`, `--breakpoint-compact-min`.
- **Used by**: `login`, `forbidden`, `session-expired`, `not-found`, `map`, `analytics-corridors`, `analytics-intersections`, `analytics-devices`, `incidents`, `incident-detail`, `dispatch`, `dispatch-detail`, `field`, `actions-recommendations`, `actions-commands`, `actions-command-detail`, `actions-outcomes`, `audit`, `operations`, `handover`, `demo`.

### LiveFeedStatus

- **Purpose**: one page-level statement of whether the live feed is connected, with the age of the newest value.
- **Anatomy**: status shape, label, "updated N s ago", and a Pause/Resume control.
- **States**: connected; reconnecting (with last connected time and "values may be older than shown"); paused; offline. Definitions come from the `feed` status domain.
- **Keyboard**: Pause/Resume is a normal button.
- **ARIA**: `role="status"`; changes are announced politely; the age text updates at most every 5 seconds.
- **Tokens**: `--color-accent`, `--color-warning`, `--color-info`, `--color-destructive`, `--typography-numeric`.
- **Used by**: `map`, `analytics-corridors`, `analytics-intersections`, `analytics-devices`, `incidents`, `incident-detail`, `dispatch`, `dispatch-detail`, `field`, `actions-recommendations`, `actions-commands`, `actions-command-detail`, `operations`.

### StatusChip

- **Purpose**: show any backend state as a shape plus a word plus a tone.
- **Anatomy**: a shape glyph, the label, and an optional detail line; the domain and state pick the entry from `status.json`.
- **States**: one per entry in the status vocabulary below; unknown state (a value the UI does not recognise) renders as the raw value with a neutral ring and is logged, never hidden.
- **Keyboard**: not focusable by itself; when it is the only content of an interactive row it takes the row's focus.
- **ARIA**: the label is real text, so no `aria-label` is needed; the shape is `aria-hidden`; the meaning text is available through `aria-describedby` where a definition is shown.
- **Tokens**: `--color-accent`, `--color-warning`, `--color-destructive`, `--color-info`, `--color-neutral`, `--radius-pill`, `--typography-size-sm`.
- **Used by**: `map`, `analytics-intersections`, `analytics-devices`, `incidents`, `incident-detail`, `dispatch`, `field`, `actions-recommendations`, `actions-commands`, `actions-command-detail`, `actions-outcomes`, `operations`, `demo`.

### TruthBadge

- **Purpose**: show a record's `truth_label` on every value it labels.
- **Anatomy**: shape plus label from the `truth` domain, small, adjacent to the value.
- **States**: simulated; measured; inferred; predicted; operator entered; verified.
- **Keyboard**: not focusable; the definition is reachable by the surrounding row's description.
- **ARIA**: visible text; a definition through `aria-describedby`.
- **Tokens**: `--color-info`, `--color-accent`, `--color-warning`, `--color-neutral`, `--typography-size-xs`.
- **Used by**: `map`, `analytics-corridors`, `analytics-intersections`, `analytics-devices`, `incident-detail`, `actions-recommendations`, `actions-outcomes`, `handover`.

### FreshnessBadge

- **Purpose**: show whether a value is fresh, stale or unknown, with its observation time and age.
- **Anatomy**: `freshness` shape and label, the observed time in UTC, and the age.
- **States**: fresh; stale (value de-emphasised, never hidden); unknown (no number is shown in place of the value).
- **Keyboard**: not focusable.
- **ARIA**: the text reads "Fresh, observed 09:14:52 UTC, 3 seconds ago"; the age is not re-announced on every tick.
- **Tokens**: `--color-accent`, `--color-warning`, `--color-neutral`, `--typography-numeric`.
- **Used by**: `map`, `analytics-corridors`, `analytics-intersections`, `analytics-devices`, `field`, `operations`.

### KeyValueList

- **Purpose**: the standard way to show labelled values: name, value, unit, observed time, truth label and freshness.
- **Anatomy**: a `dl` of rows; each row is label, value with unit, and optional `TruthBadge` and `FreshnessBadge`.
- **States**: value present; stale; unknown ("not available", never zero); loading skeleton.
- **Keyboard**: not focusable.
- **ARIA**: native `dl`, `dt`, `dd`.
- **Tokens**: `--color-muted-foreground`, `--typography-family-mono`, `--typography-numeric`, `--space-md`.
- **Used by**: `map`, `analytics-corridors`, `analytics-intersections`, `incident-detail`, `dispatch-detail`, `field`, `actions-recommendations`, `actions-command-detail`, `actions-outcomes`, `operations`.

### Button

- **Purpose**: an action. Four variants: primary, secondary, danger, ghost.
- **Anatomy**: label (wraps, never truncated), optional leading shape, a progress indicator when busy.
- **States**: default; hover; focus (two-tone ring); active; disabled (dashed outline plus reduced emphasis, still readable); busy (disabled, shows progress, announced).
- **Keyboard**: Enter and Space activate; the button never submits twice while busy.
- **ARIA**: a real `button`; `aria-busy` while busy; `aria-disabled` rather than the `disabled` attribute where the reason must stay discoverable.
- **Tokens**: `--color-accent`, `--color-on-accent`, `--color-destructive`, `--color-on-destructive`, `--color-border-strong`, `--size-control-height`, `--size-touch-min`, `--radius-md`, `--motion-duration-fast`.
- **Used by**: `login`, `forbidden`, `session-expired`, `not-found`, `incident-detail`, `dispatch`, `dispatch-detail`, `actions-recommendations`, `actions-commands`, `handover`, `demo`.

### SegmentedControl

- **Purpose**: switch between mutually exclusive views, for example Map and List.
- **Anatomy**: two or three options in one bordered group; the selected option is filled and labelled.
- **States**: selected; unselected; focus; disabled.
- **Keyboard**: one Tab stop; arrow keys move and select; Home and End go to the ends.
- **ARIA**: `role="radiogroup"` with `role="radio"` items and `aria-checked`, or a native radio group.
- **Tokens**: `--color-muted`, `--color-accent`, `--color-on-accent`, `--color-border-strong`, `--radius-md`.
- **Used by**: `map`.

### LayerToggle

- **Purpose**: turn a map layer on or off.
- **Anatomy**: a checkbox with a visible label.
- **States**: on; off; focus; disabled (when the role lacks the layer's capability the layer is absent, not disabled).
- **Keyboard**: Space toggles; each is a Tab stop.
- **ARIA**: native checkbox; a hidden layer is announced by a polite live message.
- **Tokens**: `--color-accent`, `--color-border-strong`, `--size-touch-min`.
- **Used by**: `map`.

### Select

- **Purpose**: choose one value, typically a filter.
- **Anatomy**: a visible label and a native `select`.
- **States**: default; focus; disabled; error.
- **Keyboard**: native.
- **ARIA**: a `label` element bound with `for`; the result count after filtering is announced politely.
- **Tokens**: `--color-card`, `--color-border-strong`, `--size-control-height`, `--radius-md`.
- **Used by**: `analytics-corridors`, `analytics-devices`, `incidents`, `dispatch`, `actions-commands`, `audit`.

### TextField

- **Purpose**: single-line and multi-line text input.
- **Anatomy**: visible label, optional hint, input, inline error text.
- **States**: default; focus; disabled; error (text in `--color-danger-text` plus an error shape); required (in words).
- **Keyboard**: native.
- **ARIA**: `label` bound with `for`; hint and error in `aria-describedby`; `aria-invalid` on error; a failed submit moves focus to an error summary.
- **Tokens**: `--color-background`, `--color-foreground`, `--color-border-strong`, `--color-danger-text`, `--size-control-height`.
- **Used by**: `incident-detail`, `dispatch`, `audit`, `handover`.

### DataTable

- **Purpose**: the standard tabular view; also the accessible equivalent of the map and of every chart.
- **Anatomy**: a real `table` with `caption`, `th scope` headers, sortable columns, optional row selection, a live-updating body, and a `Pagination` footer.
- **States**: loading (skeleton rows); empty (says why); error (keeps the last rows, marked stale); updating (paused or live); a selected row.
- **Keyboard**: column headers are buttons (Enter or Space sorts, current sort announced); with row selection the table is one Tab stop and Up and Down move the active row, Enter selects.
- **ARIA**: `aria-sort` on the sorted header; `aria-selected` on rows in a selectable table; the caption names the table; result counts announced politely.
- **Tokens**: `--color-card`, `--color-muted`, `--color-border`, `--color-muted-foreground`, `--typography-numeric`, `--space-md`.
- **Used by**: `map`, `analytics-corridors`, `analytics-intersections`, `analytics-devices`, `incidents`, `dispatch`, `actions-recommendations`, `actions-commands`, `actions-outcomes`, `audit`, `operations`, `handover`, `demo`.

### Pagination

- **Purpose**: load the next page of a cursor-paginated list (the API uses opaque cursors, not offsets).
- **Anatomy**: a "Load more" button and a count of what is shown; there are no page numbers because cursors have none.
- **States**: more available; loading; end of list; error (retry).
- **Keyboard**: a normal button; focus stays on it and the newly loaded rows are announced politely.
- **ARIA**: `aria-live` announcement "N more loaded".
- **Tokens**: `--size-control-height`, `--color-muted-foreground`.
- **Used by**: `incidents`, `actions-commands`, `audit`.

### Tabs

- **Purpose**: switch between related panels on one screen.
- **Anatomy**: a tab list and panels; the active tab is underlined and bold.
- **States**: selected; focus; disabled.
- **Keyboard**: one Tab stop into the tab list; arrow keys move; Home and End; the panel is the next Tab stop.
- **ARIA**: `role="tablist"`, `tab`, `tabpanel`, `aria-selected`, `aria-controls`.
- **Tokens**: `--color-accent`, `--color-border`, `--typography-weight-semibold`.
- **Used by**: `analytics-corridors`, `analytics-intersections`.

### Dialog

- **Purpose**: a modal for a short task that must be completed or cancelled.
- **Anatomy**: overlay, a titled panel, content, and an action row.
- **States**: open; closing; busy.
- **Keyboard**: focus moves in and is trapped; Escape cancels; focus returns to the opener.
- **ARIA**: `role="dialog"`, `aria-modal="true"`, `aria-labelledby`, `aria-describedby`; the page behind is `inert`.
- **Tokens**: `--color-overlay`, `--color-card`, `--shadow-xl`, `--radius-xl`, `--z-dialog`.
- **Used by**: `dispatch`.

### ConfirmDialog

- **Purpose**: the explicit confirmation step for a critical action (UX-03).
- **Anatomy**: a `Dialog` with a summary list (target, action and safety class, requester, reason, expected benefit, expected harm, constraints, expiry), the four-eyes and policy statements, an optional required reason, and Cancel, Deny, Approve.
- **States**: ready; busy (Approve disabled, progress shown); result shown in place (success, denial with policy reason, failure with error code and retryability); four-eyes not satisfied (the requester cannot approve: Approve is absent and the reason is stated); expired while open (approval disabled).
- **Keyboard**: initial focus on Cancel; Tab cycles; Escape cancels; Enter on Approve submits once.
- **ARIA**: as `Dialog`, with the result announced assertively.
- **Tokens**: `--color-overlay`, `--color-card`, `--color-accent`, `--color-destructive`, `--color-danger-text`, `--z-dialog`.
- **Used by**: `incident-detail`, `dispatch-detail`, `actions-recommendations`, `actions-commands`, `actions-command-detail`, `handover`, `demo`.

### Banner

- **Purpose**: one page-level message: stale data, reconnecting, an API failure, paused updates, or the permanent "simulated" and "demo" notices.
- **Anatomy**: a status shape, a message, an optional action (Retry, Resume).
- **States**: info; warning; danger; dismissible only for informational messages.
- **Keyboard**: the action is a normal button; the banner is not a focus stop otherwise.
- **ARIA**: `role="status"` for information and warnings; `role="alert"` for failures of a user action.
- **Tokens**: `--color-info`, `--color-warning`, `--color-destructive`, `--color-card`, `--color-danger-text`.
- **Used by**: `login`, `map`, `analytics-corridors`, `analytics-devices`, `field`, `actions-command-detail`, `operations`, `demo`.

### LiveRegion

- **Purpose**: announce updates to screen-reader users and show the recent-updates log.
- **Anatomy**: a visually present log of the last few updates and a hidden-but-live announcer, with a Pause updates button.
- **States**: live; paused; nothing new.
- **Keyboard**: the Pause updates button.
- **ARIA**: `aria-live="polite"`, `aria-atomic="false"`; batched to one announcement per 5 seconds.
- **Tokens**: `--color-muted-foreground`, `--typography-size-sm`.
- **Used by**: `map`, `incidents`.

### Stepper

- **Purpose**: show a lifecycle position, for example an incident's status or a unit's progress.
- **Anatomy**: labelled nodes in order; done, current and upcoming are distinguished by fill, outline and label, not colour alone; only legal next steps are interactive.
- **States**: done; current; upcoming; unavailable.
- **Keyboard**: interactive nodes are Tab stops; Enter records the step (after confirmation where the action is critical).
- **ARIA**: an ordered list; `aria-current="step"` on the current node.
- **Tokens**: `--color-accent`, `--color-border-strong`, `--color-muted`.
- **Used by**: `incident-detail`, `dispatch-detail`, `field`.

### Timeline

- **Purpose**: an append-only sequence of events with source, time and truth label.
- **Anatomy**: a list of entries, each with UTC time, source, description and `TruthBadge`; oldest or newest first is stated.
- **States**: loading; empty; entry linked to its record.
- **Keyboard**: links inside entries are Tab stops.
- **ARIA**: an ordered list; entries are never truncated.
- **Tokens**: `--color-border`, `--color-muted-foreground`, `--typography-numeric`.
- **Used by**: `map`, `incident-detail`, `dispatch-detail`, `actions-command-detail`.

### MapCanvas

- **Purpose**: the schematic map of the district: corridors, intersections, segments, devices, incidents and emergency units.
- **Anatomy**: an SVG whose selectable elements are real focusable elements with accessible names; pan and zoom by transform; the legend sits outside the SVG.
- **States**: loading; empty; a selected element; a hidden layer; replay (not live); stale (elements de-emphasised with their age); reconnecting.
- **Keyboard**: the canvas is one Tab stop; arrow keys pan; plus and minus zoom; Tab steps through selectable elements in a fixed order; Enter selects; Escape clears selection. Every fact on the map is also in `DataTable`.
- **ARIA**: `role="application"` is avoided; the SVG has a `title` and `desc`, and each element has `role="button"` and `aria-label` with its state in words.
- **Tokens**: `--color-map-background`, `--color-neutral`, `--color-warning`, `--color-destructive`, `--color-info`, `--color-ring`.
- **Used by**: `map`, `incident-detail`, `dispatch-detail`.

### MapLegend

- **Purpose**: explain line styles and symbols in words.
- **Anatomy**: sample line or shape plus its meaning, as text.
- **States**: static; layers hidden are marked.
- **Keyboard**: not a focus stop.
- **ARIA**: a list with real text.
- **Tokens**: `--color-neutral`, `--color-warning`, `--color-destructive`, `--typography-size-xs`.
- **Used by**: `map`.

### LineChart

- **Purpose**: a time series with optional forecast and uncertainty band.
- **Anatomy**: a labelled SVG with axes and units, series distinguished by dash and marker, a band with its coverage, a current-value summary, Pause and Resume, and a `Show as table` toggle.
- **States**: loading; empty; live; paused; forecast abstained (band absent with the reason); quality suspect (marked); a gap (drawn as a gap).
- **Keyboard**: the chart is one Tab stop; left and right move a crosshair through points and the value is announced; the table toggle is the next stop.
- **ARIA**: `role="img"` with an accessible name and a text summary; the data table is the accessible equivalent.
- **Tokens**: `--color-info`, `--color-accent`, `--color-warning`, `--color-neutral`, `--typography-family-mono`, `--typography-numeric`.
- **Used by**: `analytics-corridors`.

### RouteCard

- **Purpose**: one route alternative with its ETA and uncertainty.
- **Anatomy**: name, ETA with uncertainty and `TruthBadge` "predicted", distance, constraints applied (avoids a closure, passes a hazard), and a Select action.
- **States**: selected; alternative; blocked (never offered); no route (the list says so and asks for a human decision).
- **Keyboard**: Select is a button; the selected card is marked in text and outline.
- **ARIA**: a list item with a heading; the selected state is in text.
- **Tokens**: `--color-muted`, `--color-accent`, `--color-border-strong`, `--typography-family-mono`.
- **Used by**: `dispatch-detail`, `field`.

### CommandLifecycle

- **Purpose**: show a command's whole path in one place: requested, approved or denied, executing, executed or failed, then the verified outcome or rollback.
- **Anatomy**: a `Stepper` of command states, the `StatusChip` for the current state, who requested and approved (with their roles), the policy decision and any error, and the linked outcome.
- **States**: every command state in the status vocabulary, including policy unavailable and rolled back; outcome pending, effective, ineffective, unsafe, unknown.
- **Keyboard**: links to the command detail and the outcome.
- **ARIA**: an ordered list with `aria-current="step"`; state changes announced politely.
- **Tokens**: `--color-accent`, `--color-warning`, `--color-destructive`, `--color-info`, `--color-neutral`.
- **Used by**: `incident-detail`, `dispatch-detail`, `actions-command-detail`.

### StateViews

- **Purpose**: the loading, empty, error, forbidden, not-found and offline states for any panel or page.
- **Anatomy**: a heading that names the state, a sentence saying what happened and what to do, and a Retry or navigation action.
- **States**: loading (skeleton after 300 ms); empty; error (with a correlation id, never a stack trace); forbidden (names the missing capability); not found; offline.
- **Keyboard**: the action is a normal button; on a route change the heading receives focus.
- **ARIA**: `role="status"` for loading and empty; `role="alert"` for error.
- **Tokens**: `--color-card`, `--color-danger-text`, `--color-muted-foreground`.
- **Used by**: `login`, `forbidden`, `session-expired`, `not-found`.

## Generated reference

Do not edit between the markers; run `python source-code/frontend/tools/design.py --build`.

<!-- BEGIN GENERATED: design -->

### Colour tokens and measured contrast

| Token | Value |
|---|---|
| `--color-background` | `#0F172A` |
| `--color-foreground` | `#F8FAFC` |
| `--color-card` | `#1B2336` |
| `--color-card-foreground` | `#F8FAFC` |
| `--color-muted` | `#272F42` |
| `--color-muted-foreground` | `#94A3B8` |
| `--color-primary` | `#1E293B` |
| `--color-on-primary` | `#FFFFFF` |
| `--color-secondary` | `#334155` |
| `--color-on-secondary` | `#FFFFFF` |
| `--color-accent` | `#22C55E` |
| `--color-on-accent` | `#0F172A` |
| `--color-border` | `#475569` |
| `--color-border-strong` | `#6E7D93` |
| `--color-destructive` | `#EF4444` |
| `--color-on-destructive` | `#000000` |
| `--color-danger-text` | `#F87171` |
| `--color-warning` | `#F59E0B` |
| `--color-info` | `#38BDF8` |
| `--color-neutral` | `#94A3B8` |
| `--color-ring` | `#FFFFFF` |
| `--color-ring-inner` | `#0F172A` |
| `--color-overlay` | `rgba(0, 0, 0, 0.6)` |
| `--color-map-background` | `#0B1220` |

| Foreground on background | Ratio | Needed | Use |
|---|---|---|---|
| `foreground` on `background` | 17.06:1 | 4.5:1 | body text on the page |
| `foreground` on `card` | 14.98:1 | 4.5:1 | body text on panels |
| `foreground` on `muted` | 12.77:1 | 4.5:1 | text on selected rows and chips |
| `muted-foreground` on `background` | 6.96:1 | 4.5:1 | secondary text on the page |
| `muted-foreground` on `card` | 6.11:1 | 4.5:1 | secondary text on panels |
| `muted-foreground` on `muted` | 5.21:1 | 4.5:1 | secondary text on selected rows |
| `on-accent` on `accent` | 7.83:1 | 4.5:1 | primary button label |
| `on-primary` on `primary` | 14.63:1 | 4.5:1 | primary surface label |
| `on-secondary` on `secondary` | 10.35:1 | 4.5:1 | secondary surface label |
| `on-destructive` on `destructive` | 5.58:1 | 4.5:1 | destructive button label |
| `danger-text` on `card` | 5.66:1 | 4.5:1 | error text on panels |
| `danger-text` on `background` | 6.45:1 | 4.5:1 | error text on the page |
| `accent` on `card` | 6.88:1 | 4.5:1 | success text and links on panels |
| `warning` on `card` | 7.30:1 | 4.5:1 | warning text on panels |
| `info` on `card` | 7.31:1 | 4.5:1 | informational text and links on panels |
| `accent` on `card` | 6.88:1 | 3.0:1 | status shape: ok |
| `warning` on `card` | 7.30:1 | 3.0:1 | status shape: warning |
| `destructive` on `card` | 4.16:1 | 3.0:1 | status shape: danger |
| `info` on `card` | 7.31:1 | 3.0:1 | status shape: info |
| `neutral` on `card` | 6.11:1 | 3.0:1 | status shape: neutral |
| `destructive` on `map-background` | 4.98:1 | 3.0:1 | map: closed segment, incident marker |
| `warning` on `map-background` | 8.72:1 | 3.0:1 | map: slow segment |
| `neutral` on `map-background` | 7.30:1 | 3.0:1 | map: flowing segment and labels |
| `info` on `map-background` | 8.74:1 | 3.0:1 | map: emergency unit |
| `border-strong` on `card` | 3.74:1 | 3.0:1 | control boundary on panels |
| `border-strong` on `background` | 4.27:1 | 3.0:1 | control boundary on the page |
| `border-strong` on `muted` | 3.19:1 | 3.0:1 | control boundary on selected rows |
| `ring` on `ring-inner` | 17.85:1 | 3.0:1 | focus ring: outer ring against the inner ring |
| `ring` on `background` | 17.85:1 | 3.0:1 | focus ring against the page |
| `ring-inner` on `accent` | 7.83:1 | 3.0:1 | focus ring inner edge against the accent button |

### Departures from MASTER.md

| Token | Was | Now | Why (measured) |
|---|---|---|---|
| `border-strong` | (absent; inputs used --color-border #475569) | #6E7D93 | WCAG 1.4.11 needs 3:1 for the boundary of a control. #475569 measures 2.07:1 on cards and 2.36:1 on the page background. The new token is 3.19:1 or better on card, page and muted surfaces (3.74, 4.27 and 3.19). #475569 stays for decorative dividers. |
| `danger-text` | (absent; destructive #EF4444 used for text) | #F87171 | #EF4444 measures 4.16:1 on cards, below the 4.5:1 text minimum. #F87171 measures 5.66:1. #EF4444 is kept for shapes and fills, where 3:1 applies and it measures 4.16:1 or better. |
| `ring + ring-inner` | single 2px white outline | two-tone ring | A white ring measures 2.28:1 against the green accent button, so a lone outline can vanish. A dark inner ring plus a white outer ring keeps 3:1 or better against any surface it sits on. |
| `warning, info, neutral, map-background` | (absent) | added | The operations status vocabulary needs warning, info and neutral tones and a map surface; each is contrast-checked below. |

### Other tokens

| Group | Tokens |
|---|---|
| typography | `--typography-family-heading`: `"IBM Plex Sans", "Inter", system-ui, -apple-system, "Segoe UI", sans-serif`, `--typography-family-body`: `"Inter", system-ui, -apple-system, "Segoe UI", sans-serif`, `--typography-family-mono`: `"IBM Plex Mono", ui-monospace, "Cascadia Mono", Consolas, monospace`, `--typography-size-xs`: `0.75rem`, `--typography-size-sm`: `0.8125rem`, `--typography-size-md`: `0.875rem`, `--typography-size-lg`: `1rem`, `--typography-size-xl`: `1.125rem`, `--typography-size-2xl`: `1.25rem`, `--typography-size-3xl`: `1.5rem`, `--typography-size-display`: `2rem`, `--typography-line-tight`: `1.25`, `--typography-line-normal`: `1.5`, `--typography-weight-regular`: `400`, `--typography-weight-medium`: `500`, `--typography-weight-semibold`: `600`, `--typography-weight-bold`: `700`, `--typography-numeric`: `tabular-nums` |
| space | `--space-xs`: `2px`, `--space-sm`: `4px`, `--space-md`: `8px`, `--space-lg`: `12px`, `--space-xl`: `16px`, `--space-2xl`: `24px`, `--space-3xl`: `32px` |
| radius | `--radius-sm`: `6px`, `--radius-md`: `8px`, `--radius-lg`: `12px`, `--radius-xl`: `16px`, `--radius-pill`: `999px` |
| shadow | `--shadow-sm`: `0 1px 2px rgba(0,0,0,0.05)`, `--shadow-md`: `0 4px 6px rgba(0,0,0,0.1)`, `--shadow-lg`: `0 10px 15px rgba(0,0,0,0.1)`, `--shadow-xl`: `0 20px 25px rgba(0,0,0,0.15)` |
| motion | `--motion-duration-fast`: `150ms`, `--motion-duration-base`: `200ms`, `--motion-duration-slow`: `300ms`, `--motion-easing`: `cubic-bezier(0.2, 0, 0, 1)` |
| size | `--size-touch-min`: `44px`, `--size-control-height`: `36px`, `--size-control-height-lg`: `44px`, `--size-nav-width`: `200px`, `--size-header-height`: `48px`, `--size-focus-width`: `2px` |
| breakpoint | `--breakpoint-field-max`: `767px`, `--breakpoint-compact-min`: `768px`, `--breakpoint-laptop-min`: `1280px`, `--breakpoint-projector-min`: `1800px` |
| z | `--z-base`: `0`, `--z-sticky`: `10`, `--z-overlay`: `40`, `--z-dialog`: `50`, `--z-toast`: `60` |

### Status vocabulary

Shape and word are always shown together; tone is a hint.

**command**

| State | Label | Shape | Tone | Meaning |
|---|---|---|---|---|
| `requested` | Pending approval | half | warn | Waiting for a second person. Not approved. |
| `requested_policy_unavailable` | Policy unavailable | hexagon | warn | The policy engine could not decide. Not approved; never silently approved. |
| `approved` | Approved | check | ok | Policy approved. Waiting for the executor. |
| `denied` | Denied | cross | danger | A reviewer or policy refused it. The reason is shown. |
| `expired` | Expired | dash | neutral | Not acted on before it expired. |
| `executing` | Executing | pause | info | The command executor dispatched it to the adapter. |
| `executed` | Executed | circle | ok | The adapter acknowledged. The outcome is still to be verified. |
| `failed` | Failed | square | danger | The adapter failed. Error code and whether it is retryable are shown. |
| `rolled_back` | Rolled back | arrow-left | warn | Independent verification found it unsafe and it was undone. |

**recommendation**

| State | Label | Shape | Tone | Meaning |
|---|---|---|---|---|
| `proposed` | Proposed | diamond | info | A system recommendation. Not a command, not executed. |
| `requested` | Command requested | half | warn | A command now exists for it. |
| `superseded` | Superseded | ring | neutral | A fresher recommendation replaced it. |
| `expired` | Expired | dash | neutral | Past its validity window. |

**outcome**

| State | Label | Shape | Tone | Meaning |
|---|---|---|---|---|
| `effective` | Effective | check | ok | Independently verified as an improvement. |
| `ineffective` | Ineffective | dash | neutral | No change beyond the measured noise band. |
| `unsafe` | Unsafe (rolled back) | arrow-left | danger | Made things worse beyond the threshold and was rolled back. |
| `unknown` | Unknown (escalated) | hexagon | warn | Insufficient evidence to classify. Escalated. Never shown as success. |

**incident**

| State | Label | Shape | Tone | Meaning |
|---|---|---|---|---|
| `open` | Open | square | danger | Raised and not yet acknowledged. |
| `acknowledged` | Acknowledged | half | warn | Someone has taken notice. |
| `investigating` | Investigating | diamond | info | Under active investigation. |
| `escalated` | Escalated | triangle | danger | Raised to a higher level of attention. |
| `resolved` | Resolved | check | ok | Resolved. A merged duplicate shows the surviving incident. |
| `reopened` | Reopened | arrow-left | warn | Evidence returned after resolution. |

**severity**

| State | Label | Shape | Tone | Meaning |
|---|---|---|---|---|
| `critical` | Critical | square | danger | Highest severity. |
| `high` | High | triangle | warn | High severity. |
| `medium` | Medium | diamond | info | Medium severity. |
| `low` | Low | ring | neutral | Low severity. |

**call**

| State | Label | Shape | Tone | Meaning |
|---|---|---|---|---|
| `received` | Received | ring | info | The call has arrived. |
| `dispatched` | Dispatched | half | info | Sent for assignment. |
| `unit_assigned` | Unit assigned | diamond | info | A unit has been assigned. |
| `en_route` | En route | arrow-left | info | A unit is on its way. |
| `on_scene` | On scene | circle | ok | A unit has arrived. |
| `cleared` | Cleared | check | ok | The call is finished. |
| `cancelled` | Cancelled | cross | neutral | The call was cancelled. |

**assignment**

| State | Label | Shape | Tone | Meaning |
|---|---|---|---|---|
| `assigned` | Assigned | ring | info | Assigned, not yet acknowledged. |
| `acknowledged` | Acknowledged | half | info | The unit acknowledged. |
| `en_route` | En route | arrow-left | info | The unit is on its way. |
| `staged` | Staged | pause | warn | Held until another agency's unit arrives. |
| `on_scene` | On scene | circle | ok | The unit has arrived. |
| `clear` | Clear | check | ok | The unit has cleared. |
| `unavailable` | Unavailable | cross | danger | The unit cannot respond. Reassign. |

**freshness**

| State | Label | Shape | Tone | Meaning |
|---|---|---|---|---|
| `fresh` | Fresh | circle | ok | Within the freshness budget. |
| `stale` | Stale | triangle | warn | Past the freshness budget. Shown with its age, never as live. |
| `unknown` | Unknown | ring | neutral | Nothing received. No number is invented. |

**feed**

| State | Label | Shape | Tone | Meaning |
|---|---|---|---|---|
| `connected` | Live feed connected | circle | ok | Updates are arriving. |
| `reconnecting` | Reconnecting | triangle | warn | Values may be older than shown. |
| `paused` | Updates paused | pause | info | The operator paused updates. |
| `offline` | Offline | cross | danger | No connection. The last data is shown with its age. |

**device**

| State | Label | Shape | Tone | Meaning |
|---|---|---|---|---|
| `active` | Active | circle | ok | In service. |
| `inactive` | Inactive | ring | neutral | Not in service. |
| `maintenance` | Maintenance | hexagon | info | Being maintained. |
| `decommissioned` | Decommissioned | cross | neutral | Retired. |

**truth**

| State | Label | Shape | Tone | Meaning |
|---|---|---|---|---|
| `simulated` | Simulated | hexagon | info | Produced by the simulator. |
| `measured` | Measured | circle | ok | Measured by a device. |
| `inferred` | Inferred | diamond | warn | Derived from evidence; not verified. |
| `predicted` | Predicted | half | info | A forecast or ETA, with its uncertainty. |
| `operator_entered` | Operator entered | square | neutral | Typed by a person; attributed. |
| `verified` | Verified | check | ok | Independently verified. |

**service**

| State | Label | Shape | Tone | Meaning |
|---|---|---|---|---|
| `healthy` | Healthy | circle | ok | The probe just made succeeded. |
| `degraded` | Degraded | triangle | warn | It answers, but late or with a fault. Look at the detail. |
| `down` | Down | cross | danger | The probe just made failed, or it has stopped reporting. |
| `unknown` | Unknown | ring | neutral | It has never reported, so its state is not known. Not shown as healthy. |

**audit**

| State | Label | Shape | Tone | Meaning |
|---|---|---|---|---|
| `allowed` | Allowed | circle | ok | The action was accepted and recorded. |
| `denied` | Refused | square | warn | The action was refused: a role, a rule or the state of the thing did not allow it. |
| `failed` | Failed | cross | danger | The action was allowed but could not be completed. |

**handover**

| State | Label | Shape | Tone | Meaning |
|---|---|---|---|---|
| `awaiting_acknowledgement` | Awaiting acknowledgement | half | warn | Written, and the incoming shift has not acknowledged it by name yet. |
| `acknowledged` | Acknowledged | check | ok | The incoming person acknowledged it by name. |

**run**

| State | Label | Shape | Tone | Meaning |
|---|---|---|---|---|
| `running` | Running | half | info | The scenario is being replayed. |
| `completed` | Completed | check | ok | The replay finished and its accepted set is recorded. |
| `failed` | Failed | cross | danger | The replay did not finish. |
| `reset` | Reset | arrow-left | neutral | The run was reset by the demo operator. |

### Screens, capabilities and API

| Screen | Capability (roles) | Required API | Optional by capability |
|---|---|---|---|
| `login` | public | - | - |
| `forbidden` | public | - | - |
| `session-expired` | public | - | - |
| `not-found` | public | - | - |
| `map` | `map.view` (operator, supervisor, dispatcher, incident_commander, field_responder, auditor, demo_operator) | `topology`, `network-state`, `network-state.list`, `devices.list`, `kpis.corridors`, `observations.list`, `live` | `incidents.list`, `emergency.calls.list` |
| `analytics-corridors` | `analytics.view` (operator, supervisor, dispatcher, incident_commander, auditor, demo_operator) | `kpis.corridors`, `forecasts.corridors`, `candidates.list` | - |
| `analytics-intersections` | `analytics.view` (operator, supervisor, dispatcher, incident_commander, auditor, demo_operator) | `network-state`, `devices.list`, `observations.list` | - |
| `analytics-devices` | `analytics.view` (operator, supervisor, dispatcher, incident_commander, auditor, demo_operator) | `devices.list`, `devices.detail`, `observations.list` | - |
| `incidents` | `incidents.view` (operator, supervisor, dispatcher, incident_commander, field_responder, auditor) | `incidents.list` | - |
| `incident-detail` | `incidents.view` (operator, supervisor, dispatcher, incident_commander, field_responder, auditor) | `incidents.detail` | `incidents.transition`, `incidents.owner`, `incidents.notes`, `recommendations.list`, `commands.list` |
| `dispatch` | `emergency.view` (operator, supervisor, dispatcher, incident_commander, field_responder, auditor) | `emergency.calls.list` | `emergency.calls.create` |
| `dispatch-detail` | `emergency.view` (operator, supervisor, dispatcher, incident_commander, field_responder, auditor) | `emergency.calls.detail` | `emergency.calls.assign`, `emergency.calls.transition`, `emergency.assignments.transition`, `routes.query`, `emergency.assignments.route` |
| `field` | `emergency.view` (operator, supervisor, dispatcher, incident_commander, field_responder, auditor) | `emergency.calls.list`, `emergency.calls.detail` | `incidents.list`, `routes.query` |
| `actions-recommendations` | `recommendations.view` (operator, supervisor, dispatcher, incident_commander, auditor) | `recommendations.list` | `recommendations.request` |
| `actions-commands` | `commands.view` (operator, supervisor, dispatcher, incident_commander, auditor) | `commands.list` | `commands.create`, `commands.review` |
| `actions-command-detail` | `commands.view` (operator, supervisor, dispatcher, incident_commander, auditor) | `commands.detail` | `commands.review`, `outcomes.list`, `commands.override` |
| `actions-outcomes` | `outcomes.view` (operator, supervisor, dispatcher, incident_commander, auditor) | `outcomes.list`, `outcomes.detail` | - |
| `audit` | `audit.view` (auditor) | `audit.list` | `audit.export` |
| `operations` | `ops.view` (operator, supervisor, incident_commander, auditor) | `ops.status` | - |
| `handover` | `handover.view` (operator, supervisor, dispatcher, incident_commander, field_responder, auditor) | `handovers.list` | `handovers.create`, `handovers.acknowledge`, `incidents.list`, `commands.list`, `emergency.calls.list` |
| `demo` | `demo.control` (demo_operator) | `demo.runs.list`, `demo.runs.start`, `demo.runs.detail`, `demo.runs.replay`, `demo.runs.reset`, `demo.audit` | - |

<!-- END GENERATED: design -->
