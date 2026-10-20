// 仅保存界面选择，不保存正文或凭据；按账号、团队隔离并兼容禁用存储的浏览器。
export function readPreference(key: string): string {
  try { return localStorage.getItem(`kp:workspace:${key}`) ?? ""; }
  catch { return ""; }
}
export function writePreference(key: string, value: string): void {
  try { localStorage.setItem(`kp:workspace:${key}`, value); }
  catch { /* 存储不可用时仍可正常切换，只是不跨刷新保留。 */ }
}
