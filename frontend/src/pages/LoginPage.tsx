import { useState, type FormEvent } from "react";
import { useAuth } from "../auth/AuthContext";
import { ApiError } from "../lib/api";
import { Brand, Icon } from "../components/Icon";

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
    try { await login(email, password); }
    catch (err) { setError(err instanceof ApiError ? err.message : "无法连接服务，请稍后重试"); }
    finally { setBusy(false); }
  }
  return (
    <main className="login">
      <section className="login__story" aria-labelledby="story-title">
        <Brand />
        <div className="login__story-content">
          <h1 id="story-title">让每个答案，<br />都有据可查。</h1>
          <p className="login__intro">把分散的文档汇集成团队知识。<br />从一个问题开始，回到原文找到依据。</p>
          <div className="reading-sample" aria-label="文档问答展示示例">
            <div className="reading-sample__bar"><span><Icon name="file" size={16} /> 团队资料</span><span>展示示例</span></div>
            <p className="reading-sample__question">如何从文档中找到需要的信息？</p>
            <p className="reading-sample__answer">围绕资料直接提问，回答会附上引用的原文位置，方便逐条核对。<sup>[1]</sup></p>
            <div className="reading-sample__source"><Icon name="quote" size={18} /><div><strong>工作台使用说明 <span>片段 1</span></strong><p>上传资料后，可以针对当前知识库提问，并查看回答引用的内容。</p></div></div>
          </div>
        </div>
        <p className="login__story-foot"><Icon name="shield" size={16} /> 资料按团队隔离，协作在团队内展开</p>
      </section>
      <section className="login__entry" aria-labelledby="login-title">
        <div className="login__form-wrap">
          <span className="login__entry-icon"><Icon name="book" size={26} /></span>
          <h2 id="login-title">登录工作台</h2>
          <p className="login__support">进入你的团队，继续资料与知识工作。</p>
          <form className="login__form" onSubmit={handleSubmit} aria-busy={busy}>
            <div className="field"><label htmlFor="email">邮箱</label><input id="email" type="email" autoComplete="username" placeholder="name@example.com" required value={email} onChange={(e) => setEmail(e.target.value)} /></div>
            <div className="field"><label htmlFor="password">密码</label><input id="password" type="password" autoComplete="current-password" placeholder="输入密码" required value={password} onChange={(e) => setPassword(e.target.value)} /></div>
            {error && <p className="error-text" role="alert">{error}</p>}
            <button className="btn login__submit" type="submit" disabled={busy}>{busy ? "正在登录…" : "登录工作台"}</button>
          </form>
          <p className="login__help">还没有账号？请联系团队管理员开通账号。</p>
        </div>
        <p className="login__footer">智能文档与知识工作台</p>
      </section>
    </main>
  );
}
