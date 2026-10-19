import { AuthProvider, useAuth } from "./auth/AuthContext";
import { LoginPage } from "./pages/LoginPage";
import { WorkspacePage } from "./pages/WorkspacePage";
import "./styles/app.css";

function Routes() {
  const { status } = useAuth();

  if (status === "loading") {
    return (
      <main className="boot">
        <span className="display boot__mark">知识工作台</span>
      </main>
    );
  }

  return status === "authenticated" ? <WorkspacePage /> : <LoginPage />;
}

export default function App() {
  return (
    <AuthProvider>
      <Routes />
    </AuthProvider>
  );
}
