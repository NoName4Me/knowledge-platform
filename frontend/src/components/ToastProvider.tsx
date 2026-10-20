import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Context, type ToastKind, type ToastOptions } from "../lib/toast-context";
import { Icon } from "./Icon";

type Toast = {
  id: number;
  message: string;
  kind: ToastKind;
  action?: { label: string; onClick: () => void };
  duration: number;
};

// 失败信息需要读完，成功与提示短暂停留即可。
const DEFAULT_DURATION: Record<ToastKind, number> = {
  error: 8000,
  success: 4000,
  info: 5000,
};

// 同时最多展示几条，其余丢弃；重复的同文提示复用卡片并重新计时。
const MAX_VISIBLE = 4;

const ICON: Record<ToastKind, "alert" | "check" | "info"> = {
  error: "alert",
  success: "check",
  info: "info",
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const nextId = useRef(1);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());
  const keys = useRef(new Map<string, number>());

  const dismiss = useCallback((id: number) => {
    setItems((current) => current.filter((item) => item.id !== id));
  }, []);

  const notify = useCallback(
    (message: string, options: ToastOptions = {}) => {
      const kind = options.kind ?? "info";
      const duration = options.duration ?? DEFAULT_DURATION[kind];
      const key = `${kind}:${message}`;
      let id = keys.current.get(key);
      if (id === undefined) {
        id = nextId.current++;
        keys.current.set(key, id);
        setItems((current) =>
          [...current, { id: id as number, message, kind, action: options.action, duration }].slice(
            -MAX_VISIBLE,
          ),
        );
      }
      const previous = timers.current.get(id);
      if (previous) clearTimeout(previous);
      if (duration > 0) {
        timers.current.set(
          id,
          setTimeout(() => dismiss(id as number), duration),
        );
      }
    },
    [dismiss],
  );

  // 卡片消失后回收计时器与去重键，避免长会话里累积。
  useEffect(() => {
    const active = new Set(items.map((item) => item.id));
    for (const [id, timer] of timers.current) {
      if (!active.has(id)) {
        clearTimeout(timer);
        timers.current.delete(id);
      }
    }
    for (const [key, id] of keys.current) {
      if (!active.has(id)) keys.current.delete(key);
    }
  }, [items]);

  useEffect(
    () => () => {
      for (const timer of timers.current.values()) clearTimeout(timer);
      timers.current.clear();
      keys.current.clear();
    },
    [],
  );

  return (
    <Context.Provider value={{ notify, dismiss }}>
      {children}
      {items.length > 0 && (
        <div className="toast-viewport" aria-label="通知">
          {items.map((item) => (
            <div
              key={item.id}
              className={`toast toast--${item.kind}`}
              role={item.kind === "error" ? "alert" : "status"}
              onMouseEnter={() => {
                const timer = timers.current.get(item.id);
                if (timer) {
                  clearTimeout(timer);
                  timers.current.delete(item.id);
                }
              }}
              onMouseLeave={() => {
                if (item.duration <= 0) return;
                const timer = timers.current.get(item.id);
                if (timer) clearTimeout(timer);
                timers.current.set(
                  item.id,
                  setTimeout(() => dismiss(item.id), item.duration),
                );
              }}
            >
              <Icon name={ICON[item.kind]} size={17} />
              <div className="toast__body">
                <p>{item.message}</p>
                {item.action && (
                  <button
                    type="button"
                    className="toast__action"
                    onClick={() => {
                      dismiss(item.id);
                      item.action?.onClick();
                    }}
                  >
                    {item.action.label}
                  </button>
                )}
              </div>
              <button
                type="button"
                className="toast__close"
                aria-label="关闭通知"
                onClick={() => dismiss(item.id)}
              >
                <Icon name="close" size={15} />
              </button>
            </div>
          ))}
        </div>
      )}
    </Context.Provider>
  );
}
