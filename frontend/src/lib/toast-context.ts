import { createContext, useContext } from "react";

export type ToastKind = "error" | "info" | "success";

export type ToastAction = {
  label: string;
  onClick: () => void;
};

export type ToastOptions = {
  kind?: ToastKind;
  action?: ToastAction;
  duration?: number;
};

export type ToastState = {
  notify: (message: string, options?: ToastOptions) => void;
  dismiss: (id: number) => void;
};

export const Context = createContext<ToastState | null>(null);

export function useToast() {
  const context = useContext(Context);
  if (!context) throw new Error("通知组件必须位于应用根节点内");
  return context;
}
