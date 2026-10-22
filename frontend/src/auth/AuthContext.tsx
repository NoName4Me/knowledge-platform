import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { api, type MeResponse } from "../lib/api";

type AuthState = {
  status: "loading" | "anonymous" | "authenticated" | "password_change_required";
  session: MeResponse | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthState["status"]>("loading");
  const [session, setSession] = useState<MeResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .authSnapshot()
      .then((data) => {
        if (cancelled) return;
        setSession(data.session);
        setStatus(data.status);
      })
      .catch(() => {
        if (cancelled) return;
        setStatus("anonymous");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const data = await api.login(email, password);
    setSession(data);
    setStatus(data.user.must_change_password ? "password_change_required" : "authenticated");
  }, []);

  const logout = useCallback(async () => {
    await api.logout();
    setSession(null);
    setStatus("anonymous");
  }, []);

  const refresh = useCallback(async () => {
    const data = await api.authSnapshot();
    setSession(data.session);
    setStatus(data.status);
  }, []);

  const value = useMemo(
    () => ({ status, session, login, logout, refresh }),
    [status, session, login, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth 必须在 AuthProvider 内使用");
  }
  return context;
}
