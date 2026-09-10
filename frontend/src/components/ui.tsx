import { AlertCircle, ArrowRight, Check, FileText, LoaderCircle, RefreshCw, X } from 'lucide-react';
import { useEffect, useRef } from 'react';
import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from 'react';

export function Button({
  variant = 'secondary',
  busy,
  children,
  className = '',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  busy?: boolean;
}) {
  return (
    <button
      className={`button button-${variant} ${className}`}
      {...props}
      disabled={props.disabled || busy}
      aria-busy={busy || undefined}
    >
      {busy && <LoaderCircle size={17} className="spin" aria-hidden="true" />}
      {children}
    </button>
  );
}

export function Badge({
  children,
  tone = 'neutral',
}: {
  children: ReactNode;
  tone?: 'neutral' | 'green' | 'amber' | 'red' | 'blue';
}) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function PageIntro({
  eyebrow,
  title,
  children,
  actions,
}: {
  eyebrow?: string;
  title: string;
  children?: ReactNode;
  actions?: ReactNode;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus({ preventScroll: true });
  }, []);
  return (
    <header className="page-intro">
      <div>
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h1 ref={heading} tabIndex={-1}>
          {title}
        </h1>
        {children && <div className="page-description">{children}</div>}
      </div>
      {actions && <div className="intro-actions">{actions}</div>}
    </header>
  );
}

export function Panel({ children, className = '', ...props }: HTMLAttributes<HTMLElement>) {
  return (
    <section className={`panel ${className}`} {...props}>
      {children}
    </section>
  );
}

export function PanelHeading({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="panel-heading">
      <h2>{title}</h2>
      {children}
    </div>
  );
}

export function ErrorNotice({
  message,
  retry,
  title = 'We couldn’t complete that.',
}: {
  message?: string | null;
  retry?: () => void;
  title?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  if (!message) return null;
  return (
    <div className="notice notice-error" role="alert" tabIndex={-1} ref={ref}>
      <AlertCircle size={20} aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        <p>{message}</p>
        {retry && (
          <Button variant="ghost" onClick={retry}>
            <RefreshCw size={15} /> Try again
          </Button>
        )}
      </div>
    </div>
  );
}

export function Notice({
  children,
  title,
  tone = 'info',
}: {
  children?: ReactNode;
  title?: string;
  tone?: 'info' | 'success' | 'warning';
}) {
  return (
    <div className={`notice notice-${tone}`} role={tone === 'success' ? 'status' : undefined}>
      {tone === 'success' ? (
        <Check size={20} aria-hidden="true" />
      ) : (
        <AlertCircle size={20} aria-hidden="true" />
      )}
      <div>
        {title && <strong>{title}</strong>}
        {children && <div className="notice-copy">{children}</div>}
      </div>
    </div>
  );
}

export function Loading({
  label = 'Loading your workspace…',
  compact = false,
}: {
  label?: string;
  compact?: boolean;
}) {
  return (
    <div className={`loading ${compact ? 'loading-compact' : ''}`} role="status">
      <LoaderCircle size={22} className="spin" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function EmptyState({
  title,
  children,
  action,
  icon,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-icon">{icon ?? <FileText size={28} strokeWidth={1.4} />}</div>
      <h2>{title}</h2>
      {children && <p>{children}</p>}
      {action && <div className="empty-action">{action}</div>}
    </div>
  );
}

export function Field({
  label,
  hint,
  error,
  children,
  htmlFor,
  optional,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: ReactNode;
  htmlFor?: string;
  optional?: boolean;
}) {
  return (
    <div className="field">
      <label htmlFor={htmlFor}>
        {label}
        {optional && <span className="field-optional">Optional</span>}
      </label>
      {children}
      {hint && <p className="field-hint">{hint}</p>}
      {error && <p className="field-error">{error}</p>}
    </div>
  );
}

export function Modal({
  title,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    return () => {
      previous?.focus();
    };
  }, []);
  return (
    <dialog
      ref={dialog}
      className={`modal ${wide ? 'modal-wide' : ''}`}
      onCancel={onClose}
      aria-labelledby="modal-title"
      onClick={(event) => {
        if (event.target === dialog.current) onClose();
      }}
    >
      <div className="modal-header">
        <h2 id="modal-title">{title}</h2>
        <button className="icon-button" aria-label="Close dialog" onClick={onClose}>
          <X size={20} />
        </button>
      </div>
      {children}
    </dialog>
  );
}

export function KeyValue({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="key-value">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className="brand">
      <span className="brand-mark" aria-hidden="true">
        <ArrowRight size={21} strokeWidth={1.8} />
      </span>
      {!compact && (
        <span>
          OrderToWork<span className="brand-period">.</span>
        </span>
      )}
    </div>
  );
}
