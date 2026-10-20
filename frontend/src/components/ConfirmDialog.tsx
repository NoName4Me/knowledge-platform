import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Icon } from "./Icon";

type Props = {
  title: string;
  /** 说明这次操作会影响什么；纯文本或列表均可。 */
  description: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  /** 危险操作使用红色主按钮，并默认把焦点放在取消上。 */
  tone?: "default" | "danger";
  onConfirm: () => void | Promise<void>;
  onClose: () => void;
};

export function ConfirmDialog({
  title,
  description,
  confirmLabel,
  cancelLabel = "取消",
  tone = "default",
  onConfirm,
  onClose,
}: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const dialog = ref.current;
    const previous = window.document.activeElement;
    dialog?.showModal();
    return () => {
      dialog?.close();
      if (previous instanceof HTMLElement && previous.isConnected) {
        previous.focus({ preventScroll: true });
      }
    };
  }, []);

  async function confirm() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await onConfirm();
      onClose();
    } catch (err) {
      // 失败时留在弹窗内说明原因，用户可直接重试或取消。
      setError(err instanceof Error ? err.message : "操作失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  return (
    <dialog
      ref={ref}
      className="maintenance-dialog confirm-dialog"
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <div className="confirm-dialog__head">
        {tone === "danger" && (
          <span className="confirm-dialog__mark" aria-hidden="true">
            <Icon name="alert" size={19} />
          </span>
        )}
        <h2 id={titleId}>{title}</h2>
      </div>
      <div id={descriptionId} className="confirm-dialog__body">
        {description}
      </div>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      <div className="maintenance-actions">
        <button
          type="button"
          className="btn btn--ghost"
          disabled={busy}
          autoFocus={tone === "danger"}
          onClick={onClose}
        >
          {cancelLabel}
        </button>
        <button
          type="button"
          className={tone === "danger" ? "btn btn--danger" : "btn"}
          disabled={busy}
          autoFocus={tone !== "danger"}
          onClick={() => void confirm()}
        >
          {busy ? "处理中…" : confirmLabel}
        </button>
      </div>
    </dialog>
  );
}
