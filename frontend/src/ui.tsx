import {
  cloneElement,
  isValidElement,
  type ReactElement,
  type ReactNode,
  useId,
  useState,
} from "react";
import {
  X,
  LoaderCircle,
  Inbox,
  ArrowUpRight,
  CheckCircle2,
  AlertCircle,
} from "lucide-react";
import { statusNames } from "./api";
export function Badge({
  status,
  children,
}: {
  status?: string;
  children?: ReactNode;
}) {
  return (
    <span className={`badge ${status || ""}`}>
      {children || statusNames[status || ""] || status}
    </span>
  );
}
export function ModeBadge({ mode }: { mode: string }) {
  return (
    <span className={`badge ${mode === "live" ? "live" : "rehearsal"}`}>
      {mode === "live" ? "真实模型调用" : "本地演练 · 非模型生成"}
    </span>
  );
}
export function Empty({
  title = "暂无内容",
  detail = "创建第一条记录，开始你的研究工作。",
  action,
}: {
  title?: string;
  detail?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <Inbox size={32} />
      <strong>{title}</strong>
      <p>{detail}</p>
      {action}
    </div>
  );
}
export function SectionTitle({
  eyebrow,
  title,
  detail,
  actions,
}: {
  eyebrow?: string;
  title: string;
  detail?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="section-title">
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        {detail && <p>{detail}</p>}
      </div>
      <div className="section-actions">{actions}</div>
    </div>
  );
}
export function Panel({
  title,
  detail,
  children,
  actions,
  className = "",
}: {
  title?: string;
  detail?: string;
  children: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {title && (
        <div className="panel-head">
          <div>
            <h2>{title}</h2>
            {detail && <p>{detail}</p>}
          </div>
          {actions}
        </div>
      )}
      {children}
    </section>
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
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`modal ${wide ? "wide" : ""}`}
      >
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="icon-button" onClick={onClose} aria-label="关闭">
            <X size={20} />
          </button>
        </div>
        {children}
      </section>
    </div>
  );
}
export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  const generatedId = useId();
  const isControl =
    isValidElement(children) &&
    typeof children.type === "string" &&
    ["input", "select", "textarea"].includes(children.type);
  const control = isControl
    ? (children as ReactElement<{ id?: string; "aria-describedby"?: string }>)
    : null;
  const controlId = control?.props.id || `field-${generatedId}`;
  const hintId = `hint-${generatedId}`;
  const labelId = `label-${generatedId}`;
  return (
    <div
      className="field"
      role={isControl ? undefined : "group"}
      aria-labelledby={isControl ? undefined : labelId}
    >
      {isControl ? (
        <label id={labelId} htmlFor={controlId}>
          {label}
        </label>
      ) : (
        <span id={labelId}>{label}</span>
      )}
      {control
        ? cloneElement(control, {
            id: controlId,
            "aria-describedby":
              [control.props["aria-describedby"], hint ? hintId : undefined]
                .filter(Boolean)
                .join(" ") || undefined,
          })
        : children}
      {hint && <small id={hintId}>{hint}</small>}
    </div>
  );
}
export function JsonView({ value }: { value: any }) {
  return (
    <pre className="json-view">
      {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
    </pre>
  );
}
export function ActionButton({
  children,
  onClick,
  className = "button",
  disabled = false,
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => Promise<unknown> | void;
  className?: string;
  disabled?: boolean;
  type?: "button" | "submit";
}) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      type={type}
      className={className}
      disabled={disabled || busy}
      onClick={async () => {
        if (!onClick) return;
        setBusy(true);
        try {
          await onClick();
        } finally {
          setBusy(false);
        }
      }}
    >
      {busy ? <LoaderCircle className="spin" size={16} /> : null}
      {children}
    </button>
  );
}
export function InlineMessage({
  children,
  error = false,
}: {
  children: ReactNode;
  error?: boolean;
}) {
  return (
    <div className={`inline-message ${error ? "error" : ""}`}>
      {error ? <AlertCircle size={17} /> : <CheckCircle2 size={17} />}
      <div>{children}</div>
    </div>
  );
}
