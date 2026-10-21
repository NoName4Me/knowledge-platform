import assert from "node:assert/strict";
import test from "node:test";
import { documentLabel } from "../src/lib/document-label.ts";

test("资料标题与文件名一致时只显示标题", () => {
  assert.equal(
    documentLabel({ title: "验收指南", filename: "验收指南", replaces_id: null }),
    "验收指南",
  );
});

test("同名替换资料显示文件名和新版本标识", () => {
  assert.equal(
    documentLabel({
      title: "验收指南",
      filename: "guide-v2.txt",
      replaces_id: "old-id",
    }),
    "验收指南（guide-v2.txt，新版本）",
  );
});
