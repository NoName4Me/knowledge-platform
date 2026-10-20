import { AuthProvider, useAuth } from "./auth/AuthContext";
import { InvitePage } from "./pages/InvitePage";
import { LoginPage } from "./pages/LoginPage";
import { WorkspacePage } from "./pages/WorkspacePage";
import { ToastProvider } from "./components/ToastProvider";
import "./styles/app.css";
import "./styles/workbench.css";

function Routes() {
  const { status } = useAuth();
  const inviteToken = window.location.pathname.match(/^\/invite\/([^/]+)$/)?.[1];

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
