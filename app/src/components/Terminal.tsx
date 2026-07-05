import type { ReactNode } from "react";

/**
 * Shared terminal-window chrome for the approved phosphor/TUI design.
 * Pure presentation — no behavior lives here.
 */

/** macOS-style terminal window: traffic lights + `servebot@court — <ctx>`. */
export function TermWindow({
  context,
  className,
  children,
}: {
  context: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={`win ${className ?? ""}`}>
      <div className="titlebar">
        <span className="lights" aria-hidden="true">
          <i className="r" />
          <i className="y" />
          <i className="g" />
        </span>
        <span className="title">
          <b>servebot</b>@court&nbsp;&mdash; {context}
        </span>
      </div>
      <div className="term">{children}</div>
    </div>
  );
}

/** Box-framed output panel; the label sits on the top border rule. */
export function Panel({
  label,
  meta,
  className,
  ariaLabel,
  children,
}: {
  label: string;
  meta?: ReactNode;
  className?: string;
  ariaLabel?: string;
  children: ReactNode;
}) {
  return (
    <section className={`panel ${className ?? ""}`} aria-label={ariaLabel ?? label.replace(/_/g, " ")}>
      <span className="lbl" aria-hidden="true">
        <span className="hash">#</span> {label}
      </span>
      {meta !== undefined ? <span className="meta">{meta}</span> : null}
      {children}
    </section>
  );
}

/** Blinking block cursor for active/waiting states (static under reduced motion). */
export function Cursor() {
  return <span className="cursor" aria-hidden="true" />;
}
