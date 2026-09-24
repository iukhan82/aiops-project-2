import { useEffect, useRef, type ReactNode } from "react";
import { useLocation } from "react-router-dom";

/**
 * One `h1` per screen. On a route change focus moves to it, so a keyboard or screen-reader user starts at the top. On the
 * first load of the document it does not: the browser's own start of document (the skip link) is already the right place.
 */
export function Page({ title, subtitle, actions, children }: { title: string; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  const heading = useRef<HTMLHeadingElement>(null);
  const location = useLocation();
  useEffect(() => {
    document.title = `${title} - Traffic Operations`;
    if (location.key !== "default") heading.current?.focus({ preventScroll: true });
  }, [title, location.pathname]);
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 ref={heading} tabIndex={-1}>
            {title}
          </h1>
          {subtitle ? <p className="page-subtitle">{subtitle}</p> : null}
        </div>
        {actions ? <div className="page-actions">{actions}</div> : null}
      </div>
      {children}
    </div>
  );
}

export function Panel({ title, children, actions, id, className = "" }: { title?: string; children: ReactNode; actions?: ReactNode; id?: string; className?: string }) {
  const headingId = id ? `${id}-heading` : undefined;
  return (
    <section className={`panel ${className}`.trim()} aria-labelledby={title ? headingId : undefined} id={id}>
      {title || actions ? (
        <div className="panel-head">
          {title ? <h2 id={headingId}>{title}</h2> : <span />}
          {actions}
        </div>
      ) : null}
      {children}
    </section>
  );
}
