import assert from "node:assert/strict";
import test from "node:test";
import { citationFragments, citationNumber, referenceNumbers } from "../src/lib/citation-text.ts";
import { knowledgeBasePath, workspaceRoute } from "../src/lib/workspace-route.ts";

test("稀疏编号按正文真实标记定位，不使用引用数组下标", () => {
  assert.deepEqual(referenceNumbers("第二条[2]，第五条[5]，重复第二条[2]。"), [2, 5]);
  assert.equal(citationNumber("第二条[2]，第五条[5]", 2, 0), 2);
  assert.equal(citationNumber("第二条[2]，第五条[5]", 2, 1), 5);
  assert.equal(citationNumber("无标记", 1, 0, 5), 5);
});
test("相邻引用共同对应前一句，不产生重复文字", () => {
  const segments = citationFragments("适用团队成员。[2][5]下一句没有引用。", [2, 5]);
  assert.deepEqual(segments, [{ text: "适用团队成员。", numbers: [2, 5] }, { text: "下一句没有引用。", numbers: [] }]);
});
test("不同引用分别高亮自己的句段；未引用的前句不误高亮", () => {
  assert.deepEqual(citationFragments("前置说明。第一项。[1]第二项。[2]", [1, 2]), [
    { text: "前置说明。", numbers: [] }, { text: "第一项。", numbers: [1] },
    { text: "第二项。", numbers: [2] },
  ]);
});
test("独立一行引用仍关联前文，不丢失换行", () => {
  const result = citationFragments("第一段文字。\n\n[1]", [1]);
  assert.equal(result.map((part) => part.text).join(""), "第一段文字。\n\n");
  assert.deepEqual(result[0].numbers, [1]);
});
test("未知引用不链接、Markdown链接不误当引用、原始HTML不执行", () => {
  const text = '<img src=x onerror=alert(1)>[99] 与 [1](https://example.com)';
  assert.deepEqual(citationFragments(text, [2]), [{ text, numbers: [] }]);
  assert.deepEqual(referenceNumbers("[0][-1][1](https://example.com)"), []);
});
test("同一引用多次出现时保留所有关联段落", () => {
  assert.deepEqual(citationFragments("第一项。[1]\n第二项。[1]", [1]).filter((item) => item.numbers.includes(1)).map((item) => item.text.trim()), ["第一项。", "第二项。"]);
});
test("没有有效标记的回答保持原样，空草稿也可渲染", () => {
  assert.deepEqual(citationFragments("无依据说明", []), [{ text: "无依据说明", numbers: [] }]);
  assert.deepEqual(citationFragments("", []), [{ text: "", numbers: [] }]);
});
test("知识库路径可刷新定位，未知路径不会静默进入其他团队", () => {
  const team = "11111111-1111-1111-1111-111111111111";
  const kb = "22222222-2222-2222-2222-222222222222";
  assert.deepEqual(workspaceRoute(knowledgeBasePath(team, kb)), { kind: "knowledge", teamId: team, knowledgeBaseId: kb });
  assert.deepEqual(workspaceRoute("/"), { kind: "home" });
  assert.deepEqual(workspaceRoute("/teams/bad/knowledge-bases/missing"), { kind: "missing" });
  assert.deepEqual(workspaceRoute("/other"), { kind: "missing" });
});
