import type { ButtonHTMLAttributes } from "react";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "danger" | "ghost";
  busy?: boolean;
}

/** `busy` disables the button and says so, so an action cannot be submitted twice. */
export function Button({ variant = "secondary", busy = false, disabled, children, className = "", type = "button", ...rest }: Props) {
  return (
    <button {...rest} type={type} disabled={disabled || busy} aria-busy={busy || undefined} className={`btn btn-${variant} ${className}`.trim()}>
      {busy ? <span className="spinner" aria-hidden="true" /> : null}
      <span>{children}</span>
    </button>
  );
}
