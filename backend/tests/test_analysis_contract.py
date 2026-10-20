"""V2 协议与校验层的边界测试。

这些测试只依赖纯函数：不连数据库、不调用模型。它们固定 T01 的合同边界，
使后续 T02/T03 的实现有明确的对错标准。
"""

from __future__ import annotations

import json
import uuid
from datetime import date

import pytest
from pydantic import ValidationError

from app.analysis_contract import (
    AnalysisBudgetExhausted,
    AnalysisInvalidInput,
    AnalysisLimitExceeded,
    AnalysisLimits,
    AnalysisModelOutputInvalid,
    AnalysisSourceUnavailable,
    FieldSpec,
    dedupe_candidates,
    locate_excerpt,
    parse_batch_output,
    parse_date_value,
    parse_json_object,
    validate_batch_budget,
    validate_create_request,
    validate_model_call_count,
    validate_text_budget,
)
from app.schemas import AnalysisCreateRequest, AnalysisFieldInput

CHUNK_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_CHUNK_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
FIELD_TEXT = uuid.UUID("33333333-3333-3333-3333-333333333333")
FIELD_NUMBER = uuid.UUID("44444444-4444-4444-4444-444444444444")
FIELD_DATE = uuid.UUID("55555555-5555-5555-5555-555555555555")
FIELD_LIST = uuid.UUID("66666666-6666-6666-6666-666666666666")


def limits(**overrides: object) -> AnalysisLimits:
    base: dict[str, object] = {
        "max_documents": 3,
        "max_fields": 4,
        "max_document_chars": 1000,
        "max_total_chars": 2000,
        "batch_chars": 500,
        "max_calls_per_document": 5,
        "max_output_chars": 4000,
        "max_value_chars": 100,
        "max_list_items": 5,
        "max_citations_per_field": 3,
        "timeout_seconds": 600,
        "max_attempts": 2,
        "user_max_active_tasks": 2,
        "team_max_active_tasks": 10,
        "lease_seconds": 180,
    }
    base.update(overrides)
    return AnalysisLimits(**base)  # type: ignore[arg-type]


def make_request(**overrides: object) -> AnalysisCreateRequest:
    payload: dict[str, object] = {
        "kind": "extraction",
        "document_ids": [uuid.uuid4()],
        "fields": [
            {"name": "字段一", "description": "说明", "value_type": "text"},
        ],
    }
    payload.update(overrides)
    return AnalysisCreateRequest.model_validate(payload)


def specs() -> dict[uuid.UUID, FieldSpec]:
    return {
        FIELD_TEXT: FieldSpec(id=FIELD_TEXT, name="文本", value_type="text"),
        FIELD_NUMBER: FieldSpec(id=FIELD_NUMBER, name="数字", value_type="number"),
        FIELD_DATE: FieldSpec(id=FIELD_DATE, name="日期", value_type="date"),
        FIELD_LIST: FieldSpec(id=FIELD_LIST, name="列表", value_type="text_list"),
    }


CONTENTS = {
    CHUNK_ID: "制度规定：差旅交通费用凭有效票据报销，标准为 60%。住宿上限每晚 500 元。",
}


def batch(fields: list[dict[str, object]], *, limit: AnalysisLimits | None = None) -> list:
    raw = json.dumps({"fields": fields}, ensure_ascii=False)
    return parse_batch_output(
        raw,
        specs=specs(),
        chunk_contents=CONTENTS,
        limits=limit or limits(),
    )


def candidate(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "chunk_id": str(CHUNK_ID),
        "quote": "差旅交通费用凭有效票据报销",
        "raw_value": "凭有效票据报销",
        "value_text": "凭有效票据报销",
    }
    payload.update(overrides)
    return payload


# --- 创建请求 -----------------------------------------------------------------


def test_create_request_shape_rejects_unknown_type():
    with pytest.raises(ValidationError):
        make_request(fields=[{"name": "字段", "description": "说明", "value_type": "object"}])


def test_create_request_shape_rejects_empty_lists():
    with pytest.raises(ValidationError):
        make_request(document_ids=[])
    with pytest.raises(ValidationError):
        make_request(fields=[])


def test_create_request_rejects_duplicate_documents():
    document_id = uuid.uuid4()
    with pytest.raises(AnalysisInvalidInput):
        validate_create_request(make_request(document_ids=[document_id, document_id]), limits())


def test_create_request_rejects_duplicate_field_names_after_whitespace():
    with pytest.raises(AnalysisInvalidInput, match="字段名称重复"):
        validate_create_request(
            make_request(
                fields=[
                    {"name": "发布 日期", "description": "说明", "value_type": "date"},
                    {"name": "发布日期", "description": "说明", "value_type": "date"},
                ]
            ),
            limits(),
        )


def test_create_request_rejects_blank_name_and_description():
    with pytest.raises(AnalysisInvalidInput, match="空白"):
        validate_create_request(
            make_request(fields=[{"name": "   ", "description": "说明", "value_type": "text"}]),
            limits(),
        )
    with pytest.raises(AnalysisInvalidInput, match="缺少说明"):
        validate_create_request(
            make_request(fields=[{"name": "字段", "description": "  ", "value_type": "text"}]),
            limits(),
        )


def test_create_request_enforces_document_and_field_limits():
    with pytest.raises(AnalysisLimitExceeded, match="最多分析"):
        validate_create_request(
            make_request(document_ids=[uuid.uuid4() for _ in range(4)]), limits()
        )
    with pytest.raises(AnalysisLimitExceeded, match="最多定义"):
        validate_create_request(
            make_request(
                fields=[
                    {"name": f"字段{index}", "description": "说明", "value_type": "text"}
                    for index in range(5)
                ]
            ),
            limits(),
        )


def test_comparison_requires_two_documents():
    with pytest.raises(AnalysisInvalidInput, match="至少需要两份"):
        validate_create_request(
            make_request(kind="comparison", document_ids=[uuid.uuid4()]), limits()
        )
    validate_create_request(
        make_request(kind="comparison", document_ids=[uuid.uuid4(), uuid.uuid4()]),
        limits(),
    )


# --- 预算 ---------------------------------------------------------------------


def test_text_budget_rejects_empty_and_oversized_documents():
    with pytest.raises(AnalysisSourceUnavailable):
        validate_text_budget([0], limits())
    with pytest.raises(AnalysisLimitExceeded, match="单份文档"):
        validate_text_budget([1001], limits())
    with pytest.raises(AnalysisLimitExceeded, match="合计"):
        # 每份都在单文档上限内，但总量超过团队级上限。
        validate_text_budget([1000, 1000, 100], limits())
    validate_text_budget([500, 500], limits())


def test_batch_and_call_budgets():
    with pytest.raises(AnalysisInvalidInput):
        validate_batch_budget(0, limits())
    with pytest.raises(AnalysisLimitExceeded, match="批次"):
        validate_batch_budget(501, limits())
    validate_batch_budget(500, limits())

    validate_model_call_count(4, limits())
    with pytest.raises(AnalysisBudgetExhausted, match="上限"):
        validate_model_call_count(5, limits())


# --- 模型输出 -----------------------------------------------------------------


def test_parse_json_object_tolerates_code_fence():
    payload = parse_json_object('```json\n{"fields": []}\n```', limits())
    assert payload == {"fields": []}


def test_parse_json_object_rejects_non_object_and_oversized_output():
    with pytest.raises(AnalysisModelOutputInvalid):
        parse_json_object("没有 JSON", limits())
    with pytest.raises(AnalysisModelOutputInvalid):
        parse_json_object('"just a string"', limits())
    with pytest.raises(AnalysisLimitExceeded, match="输出超过"):
        parse_json_object("{}", limits(max_output_chars=1))


def test_batch_rejects_unknown_field_and_foreign_chunk():
    with pytest.raises(AnalysisModelOutputInvalid, match="未定义"):
        batch([{"field_id": str(uuid.uuid4()), "candidates": [candidate()]}])
    with pytest.raises(AnalysisModelOutputInvalid, match="批次之外"):
        batch(
            [
                {
                    "field_id": str(FIELD_TEXT),
                    "candidates": [candidate(chunk_id=str(OTHER_CHUNK_ID))],
                }
            ]
        )


def test_batch_rejects_quote_not_in_source():
    with pytest.raises(AnalysisModelOutputInvalid, match="摘录"):
        batch(
            [
                {
                    "field_id": str(FIELD_TEXT),
                    "candidates": [candidate(quote="这段话不在原文中")],
                }
            ]
        )


def test_batch_rejects_extra_fields_and_structural_noise():
    with pytest.raises(AnalysisModelOutputInvalid):
        parse_batch_output(
            json.dumps({"fields": [], "summary": "多余字段"}),
            specs=specs(),
            chunk_contents=CONTENTS,
            limits=limits(),
        )
    with pytest.raises(AnalysisModelOutputInvalid):
        batch([{"field_id": str(FIELD_TEXT), "candidates": [], "note": "多余"}])


def test_batch_rejects_type_mismatch_and_multiple_types():
    with pytest.raises(AnalysisModelOutputInvalid, match="声明为"):
        batch(
            [
                {
                    "field_id": str(FIELD_NUMBER),
                    "candidates": [candidate(value_text="60%")],
                }
            ]
        )
    with pytest.raises(AnalysisModelOutputInvalid, match="多种类型"):
        batch(
            [
                {
                    "field_id": str(FIELD_NUMBER),
                    "candidates": [candidate(value_number=60, value_text="60")],
                }
            ]
        )


def test_batch_rejects_missing_value_and_empty_list_item():
    with pytest.raises(AnalysisModelOutputInvalid, match="缺少取值"):
        batch(
            [
                {
                    "field_id": str(FIELD_TEXT),
                    "candidates": [
                        {"chunk_id": str(CHUNK_ID), "quote": "差旅", "raw_value": "差旅"}
                    ],
                }
            ]
        )
    with pytest.raises(AnalysisModelOutputInvalid, match="空白项"):
        batch(
            [
                {
                    "field_id": str(FIELD_LIST),
                    "candidates": [candidate(value_text=None, value_list=["交通费", "  "])],
                }
            ]
        )


def test_batch_enforces_value_list_and_citation_limits():
    with pytest.raises(AnalysisLimitExceeded, match="长度上限"):
        batch(
            [
                {
                    "field_id": str(FIELD_TEXT),
                    "candidates": [candidate(value_text="字" * 101)],
                }
            ]
        )
    with pytest.raises(AnalysisLimitExceeded, match="列表超过"):
        batch(
            [
                {
                    "field_id": str(FIELD_LIST),
                    "candidates": [candidate(value_text=None, value_list=["项"] * 6)],
                }
            ]
        )
    with pytest.raises(AnalysisLimitExceeded, match="候选超过"):
        batch(
            [
                {
                    "field_id": str(FIELD_TEXT),
                    "candidates": [candidate() for _ in range(4)],
                }
            ]
        )


def test_batch_rejects_invalid_numbers_and_dates():
    with pytest.raises(AnalysisModelOutputInvalid, match="有限数"):
        batch(
            [
                {
                    "field_id": str(FIELD_NUMBER),
                    "candidates": [candidate(value_text=None, value_number=float("nan"))],
                }
            ]
        )
    with pytest.raises(AnalysisModelOutputInvalid, match="日期格式"):
        batch(
            [
                {
                    "field_id": str(FIELD_DATE),
                    "candidates": [candidate(value_text=None, value_date="2026 年")],
                }
            ]
        )
    with pytest.raises(AnalysisModelOutputInvalid, match="越界"):
        batch(
            [
                {
                    "field_id": str(FIELD_DATE),
                    "candidates": [candidate(value_text=None, value_date="2026-13-01")],
                }
            ]
        )


def test_batch_accepts_valid_values_and_derives_date_precision():
    validated = batch(
        [
            {
                "field_id": str(FIELD_NUMBER),
                "candidates": [
                    candidate(
                        quote="标准为 60%",
                        raw_value="60%",
                        value_text=None,
                        value_number=60,
                        unit="%",
                    )
                ],
            },
            {
                "field_id": str(FIELD_DATE),
                "candidates": [
                    candidate(
                        quote="标准为 60%",
                        raw_value="2026",
                        value_text=None,
                        value_date="2026",
                    )
                ],
            },
            {
                "field_id": str(FIELD_TEXT),
                "candidates": [candidate(ambiguous=True, note="条件不同")],
            },
        ]
    )
    by_field = {item.field_id: item for item in validated}
    assert by_field[FIELD_NUMBER].value.value_number == 60
    assert by_field[FIELD_NUMBER].value.unit == "%"
    assert by_field[FIELD_DATE].value.value_date == date(2026, 1, 1)
    assert by_field[FIELD_DATE].value.date_precision == "year"
    assert by_field[FIELD_TEXT].status == "needs_review"
    assert by_field[FIELD_TEXT].note == "条件不同"


def test_date_precision_is_derived_from_value_not_model_claim():
    assert parse_date_value("2026") == (date(2026, 1, 1), "year")
    assert parse_date_value("2026-06") == (date(2026, 6, 1), "month")
    assert parse_date_value("2026-06-15") == (date(2026, 6, 15), "day")


def test_candidate_records_excerpt_from_source_not_model_text():
    validated = batch(
        [
            {
                "field_id": str(FIELD_TEXT),
                "candidates": [
                    candidate(quote="差旅  交通费用\n凭有效票据报销", raw_value="凭有效票据报销")
                ],
            }
        ]
    )
    assert len(validated) == 1
    excerpt = validated[0].excerpt
    # 摘录来自片段原文，保留原文空白，而不是模型回传的折叠文本。
    assert "差旅交通费用凭有效票据报销" in excerpt
    content = CONTENTS[CHUNK_ID]
    assert validated[0].locator_start == content.index("差旅交通费用")
    assert content[validated[0].locator_start : validated[0].locator_end] == excerpt


def test_locate_excerpt_returns_none_for_missing_text():
    assert locate_excerpt("原文内容", "不存在") is None
    assert locate_excerpt("", "任意") is None


def test_dedupe_candidates_removes_repeated_values():
    validated = batch(
        [
            {
                "field_id": str(FIELD_TEXT),
                "candidates": [candidate(), candidate()],
            }
        ]
    )
    assert len(validated) == 2
    assert len(dedupe_candidates(validated)) == 1


def test_generated_frontend_types_match_backend_contract():
    from app.typegen import render, target_path

    path = target_path()
    assert path.exists(), "缺少生成的前端类型文件"
    assert path.read_text(encoding="utf-8") == render(), "生成类型与后端合同不一致"


def test_field_input_requires_non_empty_name_and_description():
    with pytest.raises(ValidationError):
        AnalysisFieldInput(name="", description="说明", value_type="text")
    with pytest.raises(ValidationError):
        AnalysisFieldInput(name="字段", description="", value_type="text")
