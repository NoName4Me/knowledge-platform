import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Icon } from "./Icon";

export type PopoverOption = {
  id: string;
  label: string;
  group?: string;
};

type MenuProps = {
  label: string;
  trigger: ReactNode;
  align?: "start" | "end";
  className?: string;
  children: (close: () => void) => ReactNode;
};

export function PopoverMenu({
  label,
  trigger,
  align = "end",
  className = "",
  children,
}: MenuProps) {
  const root = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const dismiss = (event: PointerEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target)) {
        setOpen(false);
      }
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  return (
    <div ref={root} className={`popover${className ? ` ${className}` : ""}`}>
      <button
        ref={triggerRef}
        type="button"
        className="popover__trigger"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {trigger}
      </button>
      {open && (
        <div
          className={`popover__panel popover__panel--${align}${className === "tree-menu" ? " popover__panel--compact" : ""}`}
          role="menu"
          aria-label={label}
          onClick={(event) => event.stopPropagation()}
          onDragStart={(event) => event.stopPropagation()}
        >
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  );
}

type SelectProps = {
  value: string;
  onChange: (value: string) => void;
  options: PopoverOption[];
  label: string;
  disabled?: boolean;
  placeholder?: string;
  className?: string;
};

export function SelectMenu({
  value,
  onChange,
  options,
  label,
  disabled = false,
  placeholder = "请选择",
  className = "",
}: SelectProps) {
  const root = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listId = useId();
  const [open, setOpen] = useState(false);
  const selected = options.find((option) => option.id === value);

  useEffect(() => {
    if (!open) return;
    const dismiss = (event: PointerEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target)) {
        setOpen(false);
      }
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  return (
    <div ref={root} className={`select-menu${className ? ` ${className}` : ""}`}>
      <button
        ref={triggerRef}
        type="button"
        className="select-menu__trigger"
        aria-label={label}
        aria-controls={listId}
        aria-expanded={open}
        aria-haspopup="listbox"
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            setOpen(true);
          }
        }}
      >
        <span>{selected?.label ?? placeholder}</span>
        <Icon name="chevron" size={14} />
      </button>
      {open && (
        <div id={listId} className="select-menu__panel" role="listbox" aria-label={label}>
          {options.map((option, index) => {
            const previous = options[index - 1];
            const beginsGroup = option.group && option.group !== previous?.group;
            return (
              <div key={option.id}>
                {beginsGroup && <p className="select-menu__group">{option.group}</p>}
                <button
                  type="button"
                  role="option"
                  aria-selected={option.id === value}
                  className={option.id === value ? "is-selected" : ""}
                  onClick={() => {
                    onChange(option.id);
                    setOpen(false);
                    triggerRef.current?.focus();
                  }}
                >
                  <span>{option.label}</span>
                  {option.id === value && <Icon name="check" size={15} />}
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
