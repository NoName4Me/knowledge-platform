export type WorkspaceRoute = { kind: "home" } | { kind: "knowledge"; teamId: string; knowledgeBaseId: string } | { kind: "missing" };
const uuid = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
export function workspaceRoute(path: string): WorkspaceRoute {
  if (path === "/" || path === "") return { kind: "home" };
  const match = path.match(new RegExp(`^/teams/(${uuid})/knowledge-bases/(${uuid})/?$`));
  return match ? { kind: "knowledge", teamId: match[1], knowledgeBaseId: match[2] } : { kind: "missing" };
}
export function knowledgeBasePath(teamId: string, knowledgeBaseId: string): string {
  return `/teams/${encodeURIComponent(teamId)}/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}`;
}
