import type { TeamSummary } from "./api";

export function teamRoleLabel(role: TeamSummary["role"]): string {
  if (role === "admin") return "团队管理员";
  if (role === "viewer") return "查阅成员";
  return "普通成员";
}
