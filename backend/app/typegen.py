"""V2 与 V4 前端类型的可重复生成入口。

V1 的 `frontend/src/lib/api.ts` 是手写类型。V2 起不再手工维护一份平行定义：这里从
后端 Pydantic 合同直接生成 `frontend/src/lib/analysis-types.ts`，CI 用 `--check`
比对，避免后端 schema 与前端类型漂移。

未实现路由的 schema 通过显式导出列表生成，不为生成类型开放空实现接口；路由实现
后再把生成的类型接进 `api.ts`。

用法：

    cd backend
    .venv/bin/python -m app.typegen            # 写入
    .venv/bin/python -m app.typegen --check    # 只校验是否一致
"""

from __future__ import annotations

import argparse
import datetime
import enum
import sys
import types
import uuid
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel

from app import schemas

# 导出顺序即文件中的声明顺序；新增 V2 合同模型时在此登记。
EXPORTED_MODELS: tuple[type[BaseModel], ...] = (
    schemas.AnalysisFieldInput,
    schemas.AnalysisCreateRequest,
    schemas.AnalysisFieldOut,
    schemas.AnalysisTaskOut,
    schemas.AnalysisTaskListOut,
    schemas.AnalysisCitationOut,
    schemas.AnalysisFieldResultOut,
    schemas.AnalysisDocumentOut,
    schemas.AnalysisDocumentResultOut,
    schemas.AnalysisSummaryOut,
    schemas.AnalysisConclusionOut,
    schemas.AnalysisDetailOut,
    schemas.EvidenceSnapshotInput,
    schemas.EvidenceSnapshotOut,
    schemas.NoteCreateRequest,
    schemas.NoteUpdateRequest,
    schemas.NoteOut,
    schemas.NoteListOut,
    schemas.MissingItemOut,
    schemas.ReportGapOut,
    schemas.ReportCitationOut,
    schemas.ReportSectionOut,
    schemas.ReportSectionSuggestionRequest,
    schemas.ReportSectionSuggestionOut,
    schemas.ReportSectionUpdate,
    schemas.ReportSourceOut,
    schemas.ReportOut,
    schemas.ReportSummaryOut,
    schemas.ReportListOut,
    schemas.ReportFromAnalysisRequest,
    schemas.ReportFromSourcesRequest,
    schemas.ReportUpdateRequest,
    schemas.SavedRequirementCreate,
    schemas.SavedRequirementOut,
    schemas.SavedRequirementListOut,
    schemas.AnalysisSuggestionRequest,
    schemas.AnalysisSuggestionOut,
    schemas.DocumentGuideOut,
    schemas.DocumentSearchHitOut,
    schemas.DocumentSearchOut,
    schemas.DocumentOut,
    schemas.TextDocumentCreate,
    schemas.WebDocumentCreate,
    schemas.DocumentImpactOut,
    schemas.DocumentReplaceOut,
    schemas.AutomationCreateRequest,
    schemas.AutomationStepOut,
    schemas.AutomationOut,
    schemas.AutomationDetailOut,
    schemas.AutomationListOut,
)

HEADER = """// 由后端合同自动生成，请勿手工编辑。
//
// 生成命令：cd backend && .venv/bin/python -m app.typegen
// 一致性校验：cd backend && .venv/bin/python -m app.typegen --check
//
// 来源：backend/app/schemas.py 中 EXPORTED_MODELS 登记的 V2/V4 合同模型。
"""


def _is_nullable(annotation: Any) -> bool:
    """判断注解是否允许 None；可空字段必须在 TS 里显式带上 null。"""
    if annotation is type(None):
        return True
    if get_origin(annotation) in (Union, types.UnionType):
        return any(_is_nullable(arg) for arg in get_args(annotation))
    return False


def _render_union(args: tuple[Any, ...]) -> str:
    """把 Union 参数渲染成 TS 联合类型，保留 None 对应的 null。"""
    parts: list[str] = []
    for arg in args:
        if arg is type(None):
            parts.append("null")
            continue
        parts.append(_render_annotation(arg))
    if not parts:
        return "null"
    return parts[0] if len(parts) == 1 else " | ".join(dict.fromkeys(parts))


def _render_literal(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return f'"{value}"'


def _render_annotation(annotation: Any) -> str:
    if annotation is type(None):
        return "null"
    if annotation is str or annotation is uuid.UUID:
        return "string"
    if annotation in (datetime.datetime, datetime.date):
        return "string"
    if annotation is bool:
        return "boolean"
    if annotation is int or annotation is float:
        return "number"
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return " | ".join(f'"{member.value}"' for member in annotation)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation.__name__

    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Literal:
        return " | ".join(_render_literal(arg) for arg in args)
    if origin in (list, tuple, set, frozenset):
        item = _render_annotation(args[0]) if args else "unknown"
        return f"{item}[]"
    if origin in (dict,):
        return "Record<string, unknown>"
    if origin is Union or isinstance(annotation, types.UnionType):
        return _render_union(tuple(args))
    raise TypeError(f"无法生成前端类型的注解：{annotation!r}")


def render_model(model: type[BaseModel]) -> str:
    lines = [f"export type {model.__name__} = {{"]
    for name, field in model.model_fields.items():
        rendered = _render_annotation(field.annotation)
        if field.is_required():
            # 必填字段仍可能是 null（如可空的 UUID）；渲染结果已包含 null。
            lines.append(f"  {name}: {rendered};")
            continue
        # 有默认值的字段在请求里可以省略；是否可空由注解决定，不能一律加 null。
        lines.append(f"  {name}?: {rendered};")
    lines.append("};")
    return "\n".join(lines)


def render() -> str:
    blocks = [HEADER.rstrip("\n"), ""]
    for model in EXPORTED_MODELS:
        blocks.append(render_model(model))
        blocks.append("")
    return "\n".join(blocks).rstrip("\n") + "\n"


def target_path() -> Path:
    # app/typegen.py -> backend/app -> backend -> 仓库根
    return Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "analysis-types.ts"


def main() -> int:
    parser = argparse.ArgumentParser(description="生成或校验 V2/V4 前端类型")
    parser.add_argument("--check", action="store_true", help="只校验，不写入")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    content = render()
    path = Path(args.output) if args.output else target_path()
    if args.check:
        if not path.exists():
            print(f"缺少生成文件：{path}", file=sys.stderr)
            return 1
        existing = path.read_text(encoding="utf-8")
        if existing != content:
            print(f"生成类型与后端合同不一致：{path}", file=sys.stderr)
            return 1
        print(f"生成类型一致：{path}")
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"已写入 {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
