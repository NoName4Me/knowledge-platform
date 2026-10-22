import { useEffect, useState, type FormEvent } from "react";
import { useAuth } from "../auth/AuthContext";
import { Brand, Icon } from "../components/Icon";
import { api, ApiError, type InvitationPreview } from "../lib/api";

type Props = {
  token: string;
  onDone: () => void;
};

export function InvitePage({ token, onDone }: Props) {
  const { status, login, refresh } = useAuth();
  const [preview, setPreview] = useState<InvitationPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");

  useEffect(() => {
    let cancelled = false;
    if (status === "authenticated") {
      return () => {
        cancelled = true;
      };
    }
    api.previewInvitation(token).then((data) => {
      if (cancelled) return;
      setPreview(data);
      if (data.status === "ok" && data.email) setEmail(data.email);
    }).catch((err) => {
      if (!cancelled) setError(err instanceof ApiError ? err.message : "无法读取邀请");
    });
    return () => {
      cancelled = true;
    };
  }, [token, status]);

  async function register(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.registerWithInvitation(token, email, displayName, password);
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "加入团队失败");
    } finally {
      setBusy(false);
    }
  }

  async function accept(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      await api.acceptInvitation(token);
      await refresh();
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "接受邀请失败");
    } finally {
      setBusy(false);
    }
  }

  const unavailable = preview && preview.status !== "ok";
  const effectivePreview = status === "authenticated"
    ? { status: "ok" as const, team_name: null, email_masked: null }
    : preview;
  return (
    <main className="invite">
      <section className="invite__card" aria-labelledby="invite-title">
        <Brand />
        <span className="invite__icon"><Icon name="shield" size={25} /></span>
        <p className="invite__eyebrow">团队邀请</p>
        <h1 id="invite-title">
          {effectivePreview?.status === "ok" ? (effectivePreview.team_name ? `加入「${effectivePreview.team_name}」` : "接受团队邀请") : "接受团队邀请"}
        </h1>
        {effectivePreview?.status === "ok" && effectivePreview.email_masked && (
          <p className="invite__note">
            邀请仅限 <strong>{effectivePreview.email_masked}</strong> 使用。加入后可以查看团队资料并提问。
          </p>
        )}
        {error && <p className="error-text" role="alert">{error}</p>}
        {unavailable && (
          <div className="invite__unavailable">
            <p>{preview.status === "used" ? "该邀请已被使用。" : preview.status === "expired" ? "该邀请已过期。" : "邀请链接无效。"}</p>
            <p>请联系团队管理员重新生成邀请。</p>
          </div>
        )}
        {effectivePreview?.status === "ok" && status !== "authenticated" && (
          <>
            <form className="login__form invite__form" onSubmit={register} aria-busy={busy}>
              <p className="invite__section">首次使用，设置你的账号</p>
              <div className="field">
                <label htmlFor="invite-email">邮箱</label>
                <input id="invite-email" type="email" autoComplete="username" value={email} readOnly disabled required />
              </div>
              <div className="field">
                <label htmlFor="invite-name">姓名</label>
                <input id="invite-name" autoComplete="name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} required />
              </div>
              <div className="field">
                <label htmlFor="invite-password">设置密码</label>
                <input id="invite-password" type="password" autoComplete="new-password" minLength={8} value={password} onChange={(event) => setPassword(event.target.value)} required />
              </div>
              <button className="btn login__submit" disabled={busy}>{busy ? "正在加入…" : "加入团队"}</button>
            </form>
            <details className="invite__existing">
              <summary>我已注册过账号</summary>
              <form className="login__form" onSubmit={accept} aria-busy={busy}>
                <div className="field">
                  <label htmlFor="accept-email">邮箱</label>
                  <input id="accept-email" type="email" autoComplete="username" value={email} readOnly disabled required />
                </div>
                <div className="field">
                  <label htmlFor="accept-password">密码</label>
                  <input id="accept-password" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required />
                </div>
                <button className="btn btn--ghost login__submit" disabled={busy}>登录并加入</button>
              </form>
            </details>
          </>
        )}
        {effectivePreview?.status === "ok" && status === "authenticated" && (
          <button
            className="btn login__submit"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              api.acceptInvitation(token)
                .then(refresh)
                .then(onDone)
                .catch((err) => setError(err instanceof ApiError ? err.message : "接受邀请失败"))
                .finally(() => setBusy(false));
            }}
          >
            {busy ? "正在加入…" : "加入当前团队"}
          </button>
        )}
      </section>
    </main>
  );
}
