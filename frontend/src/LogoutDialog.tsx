import { useLayoutEffect, useId, useRef, useState } from "react";
import { AlertCircle, LoaderCircle, X } from "lucide-react";

export default function LogoutDialog({
  onClose,
  onConfirm,
}: {
  onClose: () => void;
  onConfirm: () => Promise<void>;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const submittingRef = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const titleId = useId();
  const descriptionId = useId();

  useLayoutEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    dialog.showModal();
    cancelRef.current?.focus();
    // Close before DOM removal so the browser can restore focus. Do not handle
    // its close event: cleanup also runs during StrictMode's effect check.
    return () => dialog.close();
  }, []);

  function cancel() {
    if (!submittingRef.current) onClose();
  }

  async function confirm() {
    if (submittingRef.current) return;
    submittingRef.current = true;
    setBusy(true);
    setError("");
    try {
      await onConfirm();
    } catch {
      setError(
        "退出登录未完成，请检查网络连接后重试。也可以取消并继续使用当前页面。",
      );
    } finally {
      submittingRef.current = false;
      setBusy(false);
    }
  }

  return (
    <dialog
      ref={dialogRef}
      className="modal logout-dialog"
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      aria-busy={busy}
      onCancel={(event) => {
        event.preventDefault();
        cancel();
      }}
    >
      <div className="modal-head">
        <h2 id={titleId}>退出登录？</h2>
        <button
          type="button"
          className="icon-button"
          aria-label="关闭"
          onClick={cancel}
          disabled={busy}
        >
          <X size={20} />
        </button>
      </div>
      <p id={descriptionId} className="logout-message">
        退出后需要重新登录。请先保存尚未提交的内容。
      </p>
      {error && (
        <div className="inline-message error logout-error" role="alert">
          <AlertCircle size={17} />
          <div>{error}</div>
        </div>
      )}
      <div className="modal-actions">
        <button
          ref={cancelRef}
          type="button"
          className="button"
          autoFocus
          onClick={cancel}
          disabled={busy}
        >
          取消
        </button>
        <button
          type="button"
          className="button primary"
          onClick={confirm}
          disabled={busy}
        >
          {busy && <LoaderCircle className="spin" size={16} />}
          {busy ? "正在退出…" : "确认退出"}
        </button>
      </div>
    </dialog>
  );
}
