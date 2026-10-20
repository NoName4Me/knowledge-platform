import { useEffect, useRef, useState, type FormEvent } from "react";

type Props = {
  title: string; label: string; initial: string; maxLength?: number; hint?: string;
  onSave: (name: string) => Promise<void>; onClose: () => void;
};
export function NameDialog({ title, label, initial, maxLength = 200, hint, onSave, onClose }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const [value, setValue] = useState(initial);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const el = ref.current;
    const previous = document.activeElement;
    el?.showModal();
    return () => { el?.close(); if (previous instanceof HTMLElement) previous.focus(); };
  }, []);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (saving || !value.trim()) return;
    setSaving(true); setError("");
    try { await onSave(value.trim()); onClose(); }
    catch (err) { setError(err instanceof Error ? err.message : "保存失败，请重试"); }
    finally { setSaving(false); }
  }
  return <dialog ref={ref} className="maintenance-dialog" aria-labelledby="name-dialog-title"
    onCancel={(event) => { event.preventDefault(); if (!saving) onClose(); }}>
    <form onSubmit={(event) => void submit(event)}>
      <h2 id="name-dialog-title">{title}</h2>
      <label className="field"><span>{label}</span><input autoFocus value={value} maxLength={maxLength}
        onChange={(e) => setValue(e.target.value)} required disabled={saving} /></label>
      {hint && <p className="maintenance-hint">{hint}</p>}
      {error && <p className="error-text" role="alert">{error}</p>}
      <div className="maintenance-actions"><button type="button" className="btn btn--ghost" onClick={onClose} disabled={saving}>取消</button>
        <button className="btn" disabled={saving || !value.trim()}>{saving ? "保存中…" : "保存"}</button></div>
    </form>
  </dialog>;
}
