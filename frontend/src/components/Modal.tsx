import { useEffect, useRef, useId, type ReactNode } from "react";
import { Icon } from "./Icon";

export function Modal({ title, onClose, busy = false, closeable = true, children }: {
  title: string; onClose: () => void; busy?: boolean; closeable?: boolean; children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const el = ref.current;
    const previous = document.activeElement;
    el?.showModal();
    return () => { el?.close(); if (previous instanceof HTMLElement && previous.isConnected) previous.focus(); };
  }, []);
  return <dialog ref={ref} className="maintenance-dialog" aria-labelledby={titleId}
    onCancel={(event) => { event.preventDefault(); if (!busy && closeable) onClose(); }}>
    <div className="modal-heading"><h2 id={titleId}>{title}</h2>{closeable && <button type="button" className="icon-button" disabled={busy} onClick={onClose} aria-label="关闭对话框"><Icon name="close" size={18} /></button>}</div>
    {children}
  </dialog>;
}
