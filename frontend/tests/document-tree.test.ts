import assert from "node:assert/strict";
import test from "node:test";
import { splitFilename } from "../src/lib/document-format.ts";
import { canDrop, childrenOf, siblingListAfterDrop } from "../src/lib/document-tree.ts";

test("文件名后缀与可编辑主体分开", () => {
  assert.deepEqual(splitFilename("季度报告.final.docx"), {
    stem: "季度报告.final",
    extension: ".docx",
  });
});

test("拖到目录中部表示移入，拖到上下缘表示插入", () => {
  assert.equal(canDrop({ kind: "document", id: "doc" }, { kind: "inside", folderId: "folder" }), true);
  assert.equal(canDrop({ kind: "folder", id: "folder" }, { kind: "inside", folderId: "folder" }), false);
  assert.equal(canDrop({ kind: "document", id: "doc" }, {
    kind: "before", parentId: null, targetKind: "folder", targetId: "folder",
  }), true);
});

test("目录与文件按混合 sort_order 排列，插入后提交完整同级列表", () => {
  const folders = [
    { id: "folder", knowledge_base_id: "kb", parent_id: null, name: "甲", sort_order: 1 },
  ];
  const documents = [
    {
      id: "doc", knowledge_base_id: "kb", folder_id: null, sort_order: 0,
      title: "资料", filename: "资料.txt", size_bytes: 1, status: "ready" as const,
      failure_reason: null, created_at: "2026-01-01",
    },
  ];
  const nodes = childrenOf(folders, documents, null);
  assert.deepEqual(nodes.map((node) => node.id), ["doc", "folder"]);
  assert.deepEqual(
    siblingListAfterDrop(nodes, { kind: "folder", id: "folder" }, "doc", "before", "document"),
    [{ kind: "folder", id: "folder" }, { kind: "document", id: "doc" }],
  );
});
