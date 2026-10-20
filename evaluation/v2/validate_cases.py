"""校验 V2 固定样例：文档存在、格式可解析、预期依据能在原文中定位。

这个脚本不调用模型、不连接数据库，只做静态校验，可以在 CI 与本地随时执行：

    backend/.venv/bin/python evaluation/v2/validate_cases.py

它保证样例本身是自洽的：每个 `anchor` 都能在对应文档的已发布片段中找到，
每个 `anchor_absent` 都不出现。否则后续任务会基于错误的期望开发。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

import json  # noqa: E402

from app.analysis_contract import locate_excerpt  # noqa: E402
from app.parsing import UnsupportedDocument, extract_document  # noqa: E402

REQUIRED_CASE_FIELDS = ("id", "kind", "documents", "fields", "expectations")
# 预期状态要么是单一 status，要么是 status_any（例如同一字段对应多个条件取值）。
REQUIRED_EXPECTATION_FIELDS = ("field", "document")


def load_cases(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def parsed_chunks(corpus: Path, filename: str) -> list[str]:
    data = (corpus / filename).read_bytes()
    return [chunk.content for chunk in extract_document(filename, data).chunks]


def find_anchor(chunks: list[str], anchor: str) -> bool:
    return any(locate_excerpt(content, anchor) is not None for content in chunks)


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 V2 固定样例自洽性")
    parser.add_argument("--cases", default="evaluation/v2/dataset/cases.json")
    parser.add_argument("--corpus", default="evaluation/v2/corpus")
    args = parser.parse_args()

    # 相对路径一律按仓库根解析，避免调用位置影响结果。
    cases_path = Path(args.cases)
    if not cases_path.is_absolute():
        cases_path = REPO_ROOT / cases_path
    corpus = Path(args.corpus)
    if not corpus.is_absolute():
        corpus = REPO_ROOT / corpus
    payload = load_cases(cases_path)

    problems: list[str] = []
    documents = {document["id"]: document for document in payload["documents"]}

    parsed: dict[str, list[str]] = {}
    for document_id, document in documents.items():
        filename = document["file"]
        if not (corpus / filename).exists():
            problems.append(f"缺少语料文件 {filename}")
            continue
        try:
            parsed[document_id] = parsed_chunks(corpus, filename)
        except UnsupportedDocument as exc:
            problems.append(f"{filename} 无法解析：{exc}")

    seen_cases: set[str] = set()
    for case in payload["cases"]:
        case_id = case.get("id", "<无 ID>")
        if case_id in seen_cases:
            problems.append(f"样例 ID 重复：{case_id}")
        seen_cases.add(case_id)
        for name in REQUIRED_CASE_FIELDS:
            if name not in case:
                problems.append(f"{case_id} 缺少字段 {name}")
        for document_id in case.get("documents", []):
            if document_id not in documents:
                problems.append(f"{case_id} 引用了未声明的文档 {document_id}")
        if case.get("kind") == "comparison" and len(case.get("documents", [])) < 2:
            problems.append(f"{case_id} 是对比样例，但文档少于两份")

        field_names = [field["name"] for field in case.get("fields", [])]
        if len(set(field_names)) != len(field_names):
            problems.append(f"{case_id} 字段名称重复")

        for expectation in case.get("expectations", []):
            field = expectation.get("field")
            if field not in field_names:
                problems.append(f"{case_id} 预期引用了未定义字段 {field}")
            for name in REQUIRED_EXPECTATION_FIELDS:
                if name not in expectation:
                    problems.append(f"{case_id}/{field} 预期缺少 {name}")
            if "status" not in expectation and "status_any" not in expectation:
                problems.append(f"{case_id}/{field} 预期缺少 status 或 status_any")
            document_id = expectation.get("document")
            chunks = parsed.get(document_id)
            if chunks is None:
                problems.append(f"{case_id}/{field} 预期引用了未解析文档 {document_id}")
                continue
            anchors = list(expectation.get("anchors") or [])
            if expectation.get("anchor"):
                anchors.append(expectation["anchor"])
            for anchor in anchors:
                if not find_anchor(chunks, anchor):
                    problems.append(f"{case_id}/{field} 依据在原文中找不到：{anchor}")
            absent = expectation.get("anchor_absent")
            if absent and find_anchor(chunks, absent):
                problems.append(f"{case_id}/{field} 声称未提及，但原文含：{absent}")

        for conclusion in case.get("conclusions", []) or []:
            field = conclusion.get("field")
            if field not in field_names:
                problems.append(f"{case_id} 结论引用了未定义字段 {field}")
            for document_id in conclusion.get("documents", []):
                if document_id not in case.get("documents", []):
                    problems.append(
                        f"{case_id} 结论引用了不在本次范围的文档 {document_id}"
                    )

    if problems:
        for problem in problems:
            print("错误：", problem)
        print(f"校验失败：{len(problems)} 处问题")
        return 1

    print(
        f"校验通过：{len(documents)} 份语料，{len(payload['cases'])} 个样例，"
        f"{sum(len(case['expectations']) for case in payload['cases'])} 条预期"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
