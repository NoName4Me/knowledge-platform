import { AuthProvider, useAuth } from "./auth/AuthContext";
import { InvitePage } from "./pages/InvitePage";
import { LoginPage } from "./pages/LoginPage";
import { WorkspacePage } from "./pages/WorkspacePage";
import { PlatformAdminPage } from "./pages/PlatformAdminPage";
import { ProfileDialog } from "./components/ProfileDialog";
import { useToast } from "./lib/toast-context";
import { Brand } from "./components/Icon";
import { ToastProvider } from "./components/ToastProvider";
import "./styles/app.css";
import "./styles/workbench.css";

function Routes() {
  const { status, session } = useAuth();
  const toast = useToast();
  const inviteToken = window.location.pathname.match(/^\/invite\/([^/]+)$/)?.[1];
  const platform = window.location.pathname === "/platform";

  if (status === "loading") {
    return (
      <main className="boot">
        <span className="display boot__mark">知识工作台</span>
      </main>
    );
  }

  if (inviteToken) {
    return (
      <InvitePage
        token={decodeURIComponent(inviteToken)}
        onDone={() => {
          window.history.replaceState(null, "", "/");
          window.location.reload();
        }}
      />
    );
  }

  if (status === "password_change_required") {
    return (
      <main className="password-gate">
        <Brand />
        <ProfileDialog
          forced
          onClose={() => undefined}
          onError={(value) => toast.notify(value, { kind: "error" })}
          onSuccess={(value) => toast.notify(value, { kind: "success" })}
          onPasswordChanged={async () => window.location.assign("/")}
        />
      </main>
    );
  }

  if (platform && status === "authenticated") {
    return session?.user.is_platform_admin
      ? <PlatformAdminPage />
      : <main className="workbench-loading"><h1>无权访问平台管理</h1><a className="btn" href="/">返回工作台</a></main>;
  }

  return status === "authenticated" ? <WorkspacePage /> : <LoginPage />;
}

export default function App() {
  return (
    <ToastProvider>
      <AuthProvider>
        <Routes />
      </AuthProvider>
    </ToastProvider>
  );
}
