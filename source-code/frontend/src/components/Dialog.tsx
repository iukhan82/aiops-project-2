import { useEffect, useId, useRef, type ReactNode } from "react";
import { Banner } from "./Banner";
import { Button } from "./Button";
import { KeyValueList, type KeyValue } from "./KeyValueList";

interface DialogProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** The first control to focus. The safe choice (Cancel) is the default. */
  initialFocus?: "cancel" | "first";
  describedBy?: string;
}

/**
 * A modal on the platform's `<dialog>` element: the page behind is inert, Tab stays inside, Escape cancels, and focus returns to
 * whatever opened it. Focus lands on the element marked `data-autofocus` (Cancel in a confirmation), never on a destructive control.
 */
export function Dialog({ open, title, onClose, children, describedBy }: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const opener = useRef<HTMLElement | null>(null);
  const placed = useRef(false);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      placed.current = false;
      dialog.showModal();
    } else if (!open && dialog.open) {
      dialog.close();
      opener.current?.focus();
    }
  }, [open]);

  // Runs after every render: content can change while the dialog is open (loading, then the loaded command; busy, then done).
  // Until focus has been placed on the safe control, and whenever it has fallen out of the dialog, put it there.
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog || !open || !dialog.open) return;
    const active = document.activeElement;
    const inside = active !== null && active !== dialog && dialog.contains(active) && !(active instanceof HTMLButtonElement && active.disabled);
    if (inside && placed.current) return;
    const safe = dialog.querySelector<HTMLElement>("[data-autofocus]");
    if (safe) {
      if (!(safe instanceof HTMLButtonElement && safe.disabled)) {
        safe.focus();
        placed.current = true;
      }
      return;
    }
    dialog.querySelector<HTMLElement>("button:not(:disabled), input, select, textarea")?.focus();
    placed.current = true;
  });

  return (
    <dialog
      ref={ref}
      className="dialog"
      aria-labelledby={titleId}
      aria-describedby={describedBy}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      {open ? (
        <div className="dialog-body">
          <h2 id={titleId}>{title}</h2>
          {children}
        </div>
      ) : null}
    </dialog>
  );
}

export interface ConfirmResult {
  tone: "ok" | "warn" | "danger";
  title: string;
  message: string;
}

interface ConfirmProps {
  open: boolean;
  title: string;
  /** Everything the person deciding needs: target, class, requester, reason, expected benefit and harm, constraints, expiry. */
  summary: readonly KeyValue[];
  /** Plain statements the person must read: the four-eyes rule, the policy state. */
  statements?: readonly ReactNode[];
  /** Extra inputs (a reason). */
  children?: ReactNode;
  confirmLabel: string;
  confirmVariant?: "primary" | "danger";
  /** Approve is absent when this person is not allowed (for example, they requested it themselves). */
  canConfirm?: boolean;
  cannotConfirmBecause?: string;
  denyLabel?: string;
  onDeny?: () => void;
  busy: boolean;
  result: ConfirmResult | null;
  onConfirm: () => void;
  onClose: () => void;
}

/**
 * The confirmation step for a critical action. It restates what is about to happen, defaults focus to Cancel, disables the
 * decision buttons while the request is in flight (no double submit), and shows the outcome in place - success, refusal with the
 * reason, or failure - announced assertively. There is no optimistic update: the state shown is what the server returned.
 */
/** The content of a confirmation, for placing inside a `Dialog` that stays mounted (so focus can return to the opener on close). */
export function ConfirmBody({ summary, statements = [], children, confirmLabel, confirmVariant = "primary", canConfirm = true, cannotConfirmBecause, denyLabel, onDeny, busy, result, onConfirm, onClose }: Omit<ConfirmProps, "open" | "title">) {
  const done = result !== null;
  return (
    <>
      <KeyValueList items={summary} dense />
      {statements.length > 0 ? (
        <ul className="plain-list">
          {statements.map((statement, index) => (
            <li key={index}>{statement}</li>
          ))}
        </ul>
      ) : null}
      {children}
      {!canConfirm && cannotConfirmBecause ? <Banner tone="warn">{cannotConfirmBecause}</Banner> : null}
      <div className="confirm-result">
        {result ? <Banner tone={result.tone === "ok" ? "info" : result.tone} alert>{`${result.title}. ${result.message}`}</Banner> : null}
      </div>
      <div className="dialog-actions">
        <Button data-autofocus variant="secondary" onClick={onClose} disabled={busy}>
          {done ? "Close" : "Cancel"}
        </Button>
        {!done && onDeny && denyLabel && canConfirm ? (
          <Button variant="danger" onClick={onDeny} busy={busy}>
            {denyLabel}
          </Button>
        ) : null}
        {!done && canConfirm ? (
          <Button variant={confirmVariant} onClick={onConfirm} busy={busy}>
            {confirmLabel}
          </Button>
        ) : null}
      </div>
    </>
  );
}

export function ConfirmDialog(props: ConfirmProps) {
  const { open, title, onClose, busy } = props;
  return (
    <Dialog open={open} title={title} onClose={busy ? () => undefined : onClose}>
      <ConfirmBody {...props} />
    </Dialog>
  );
}
