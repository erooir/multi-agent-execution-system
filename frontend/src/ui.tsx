import {
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type FormEvent,
} from "react";
import { X, LoaderCircle, Inbox, ArrowUpRight } from "lucide-react";
import { statusNames, statusTone } from "./domain";
export function Badge({
  status,
  children,
}: {
  status: string;
  children?: ReactNode;
}) {
  return (
    <span className={`badge ${statusTone(status)}`}>
      <i />
      {children || statusNames[status] || status || "未知"}
    </span>
  );
}
export function Empty({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <Inbox size={32} />
      <h3>{title}</h3>
      {description && <p>{description}</p>}
      {action}
    </div>
  );
}
export function Field({
  label,
  hint,
  children,
  className = "",
}: {
  label: string;
  hint?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={`field ${className}`}>
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}
export function JsonView({
  value,
  label = "查看完整记录",
}: {
  value: unknown;
  label?: string;
}) {
  return (
    <details className="json">
      <summary>{label}</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}
export function ErrorLine({ children }: { children: ReactNode }) {
  return (
    <div className="error-line" role="alert">
      {children}
    </div>
  );
}
export function SectionTitle({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="section-title">
      <div>
        {eyebrow && <span className="eyebrow">{eyebrow}</span>}
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      <div className="actions">{actions}</div>
    </div>
  );
}
export function Panel({
  title,
  children,
  actions,
  className = "",
}: {
  title?: string;
  children: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {title && (
        <div className="panel-title">
          <h2>{title}</h2>
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}
export function LinkButton({
  children,
  onClick,
}: {
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <button className="link-button" onClick={onClick}>
      {children}
      <ArrowUpRight size={15} />
    </button>
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
  const ref = useRef<HTMLDialogElement>(null),
    closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const el = ref.current;
    const previous = document.activeElement as HTMLElement;
    el?.showModal();
    const cancel = (e: Event) => {
      e.preventDefault();
      closeRef.current();
    };
    el?.addEventListener("cancel", cancel);
    return () => {
      el?.removeEventListener("cancel", cancel);
      el?.close();
      previous?.focus();
    };
  }, []);
  return (
    <dialog
      aria-label={title}
      ref={ref}
      className={`modal ${wide ? "wide" : ""}`}
      onClick={(e) => {
        if (e.target === e.currentTarget) {
          const r = e.currentTarget.getBoundingClientRect();
          if (
            e.clientX < r.left ||
            e.clientX > r.right ||
            e.clientY < r.top ||
            e.clientY > r.bottom
          )
            onClose();
        }
      }}
    >
      <div className="modal-head">
        <h2>{title}</h2>
        <button
          className="icon-button"
          aria-label="关闭对话框"
          onClick={onClose}
        >
          <X size={21} />
        </button>
      </div>
      {children}
    </dialog>
  );
}
export function FormModal({
  title,
  children,
  onClose,
  onSubmit,
  submit = "保存",
  wide = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  onSubmit: () => Promise<any>;
  submit?: string;
  wide?: boolean;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await onSubmit();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={title}
      onClose={() => {
        if (!busy) onClose();
      }}
      wide={wide}
    >
      <form onSubmit={save}>
        <fieldset disabled={busy}>
          <div className="modal-body">
            {children}
            {error && <ErrorLine>{error}</ErrorLine>}
          </div>
          <div className="modal-foot">
            <button type="button" onClick={onClose}>
              取消
            </button>
            <button className="primary" type="submit">
              {busy && <LoaderCircle size={16} className="spin" />}
              {busy ? "正在提交…" : submit}
            </button>
          </div>
        </fieldset>
      </form>
    </Modal>
  );
}
export function Confirm({
  title,
  description,
  onClose,
  onConfirm,
  danger = false,
  confirm = "确认",
}: {
  title: string;
  description: string;
  onClose: () => void;
  onConfirm: () => Promise<any>;
  danger?: boolean;
  confirm?: string;
}) {
  return (
    <FormModal
      title={title}
      onClose={onClose}
      onSubmit={onConfirm}
      submit={confirm}
    >
      <p className={danger ? "danger-text" : ""}>{description}</p>
    </FormModal>
  );
}
export function Tabs({
  items,
  value,
  onChange,
}: {
  items: { id: string; label: string }[];
  value: string;
  onChange: (id: string) => void;
}) {
  return (
    <div className="tabs">
      {items.map((item) => (
        <button
          key={item.id}
          className={item.id === value ? "selected" : ""}
          aria-pressed={item.id === value}
          onClick={() => onChange(item.id)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}
