import { useState, type FormEvent } from "react";

import { useAuth } from "../auth/AuthContext";
import { ApiError } from "../lib/api";

export function LoginPage() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(email, password);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "无法连接服务，请稍后重试");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <header className="login__bar">
        <span className="display login__mark">知识工作台</span>
        <span className="eyebrow-free-label">团队文档问答</span>
      </header>

      <section className="login__statement" aria-labelledby="login-title">
        <h1 id="login-title" className="display login__sentence">
          <span>上传资料</span>
          <span className="login__strip" aria-hidden="true">
            <FragmentStrip />
          </span>
          <span>问答有据</span>
        </h1>
        <p className="login__support">
          把团队的公开资料放进来，提问后可以看到答案引用的原文位置。
        </p>
      </section>

      <form className="login__form" onSubmit={handleSubmit} noValidate>
        <div className="field">
          <label htmlFor="email">邮箱</label>
          <input
            id="email"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="password">密码</label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </div>
        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
        <button className="btn" type="submit" disabled={busy}>
          {busy ? "正在登录" : "登录"}
        </button>
      </form>
    </main>
  );
}

function FragmentStrip() {
  return (
    <span className="fragment" data-ui>
      <span className="fragment__source">差旅管理办法 · 第 3 页</span>
      <span className="fragment__quote">
        因公出差产生的交通费用，凭有效票据报销。
      </span>
      <span className="fragment__meta">引用 1 / 3</span>
    </span>
  );
}
