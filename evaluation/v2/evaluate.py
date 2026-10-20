"""执行 V2 固定样例并记录自动合同检查结果。

真实模型质量评测必须使用配置了 `KP_ANALYSIS_MODEL_*` 的部署；替身结果只能通过
`--allow-stub` 显式运行，用来验证接口、状态和页面，不能作为质量证据。

    KP_EVAL_EMAIL=... KP_EVAL_PASSWORD=... KP_EVAL_TEAM_ID=... \
      backend/.venv/bin/python evaluation/v2/evaluate.py run \
      --base-url http://127.0.0.1:8080 \
      --results evaluation/v2/results/current/results.json

已配置真实分析模型时，不要加 `--allow-stub`；结果里只要出现 `provider=stub`，
命令就会以失败退出。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES = REPO_ROOT / "evaluation/v2/dataset/cases.json"
DEFAULT_CORPUS = REPO_ROOT / "evaluation/v2/corpus"
TERMINAL = {"completed", "partial", "failed", "invalidated"}


class ApiClient:
    def __init__(self, base_url: str) -> None:
        self.client = httpx.Client(base_url=base_url.rstrip("/"), timeout=60.0)
        self.csrf = ""

    def close(self) -> None:
        self.client.close()

    def _headers(self) -> dict[str, str]:
        port = self.client.base_url.port
        origin = f"{self.client.base_url.scheme}://{self.client.base_url.host}"
        if port is not None:
            origin += f":{port}"
        return {"Origin": origin, "X-CSRF-Token": self.csrf}

    def initialize(self) -> None:
        response = self.client.get("/api/auth/csrf")
        response.raise_for_status()
        self.csrf = response.json()["csrf_token"]

    def login(self, email: str, password: str) -> None:
        response = self.client.post(
            "/api/auth/login",
            json={"email": email, "password": password},
            headers=self._headers(),
        )
        response.raise_for_status()
        self.csrf = response.json()["csrf_token"]

    def create_knowledge_base(self, team_id: str, name: str) -> dict[str, Any]:
        response = self.client.post(
            f"/api/teams/{team_id}/knowledge-bases",
            json={"name": name},
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()

    def upload(
        self, team_id: str, knowledge_base_id: str, path: Path, key: uuid.UUID
    ) -> dict[str, Any]:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        response = self.client.post(
            f"/api/teams/{team_id}/documents",
            data={"knowledge_base_id": knowledge_base_id},
            files={"file": (path.name, path.read_bytes(), content_type)},
            headers={**self._headers(), "Idempotency-Key": str(key)},
        )
        response.raise_for_status()
        return response.json()

    def list_documents(self, team_id: str, knowledge_base_id: str) -> list[dict[str, Any]]:
        response = self.client.get(
            f"/api/teams/{team_id}/documents",
            params={"knowledge_base_id": knowledge_base_id},
        )
        response.raise_for_status()
        return response.json()

    def create_analysis(
        self,
        team_id: str,
        knowledge_base_id: str,
        payload: dict[str, Any],
        key: uuid.UUID,
    ) -> dict[str, Any]:
        response = self.client.post(
            f"/api/teams/{team_id}/knowledge-bases/{knowledge_base_id}/analyses",
            json=payload,
            headers={**self._headers(), "Idempotency-Key": str(key)},
        )
        response.raise_for_status()
        return response.json()

    def get_analysis(
        self, team_id: str, knowledge_base_id: str, task_id: str
    ) -> dict[str, Any]:
        response = self.client.get(
            f"/api/teams/{team_id}/knowledge-bases/{knowledge_base_id}/analyses/{task_id}"
        )
        response.raise_for_status()
        return response.json()

    def delete_document(self, team_id: str, document_id: str) -> None:
        response = self.client.delete(
            f"/api/teams/{team_id}/documents/{document_id}",
            headers=self._headers(),
        )
        response.raise_for_status()


def _compact(value: str) -> str:
    return "".join(value.split())


def _format_date(value: str | None, precision: str | None) -> str | None:
    if value is None:
        return None
    if precision == "year":
        return value[:4]
    if precision == "month":
        return value[:7]
    return value[:10]


def _field_value(field: dict[str, Any]) -> Any:
    if field["value_list"] is not None:
        return field["value_list"]
    if field["value_number"] is not None:
        return field["value_number"]
    if field["value_date"] is not None:
        return _format_date(field["value_date"], field.get("date_precision"))
    return field["value_text"]


def _check_expectation(
    expectation: dict[str, Any],
    result: dict[str, Any],
    documents: dict[str, str],
    fields: dict[str, str],
) -> list[str]:
    failures: list[str] = []
    document_name = expectation["document"]
    field_name = expectation["field"]
    document_id = documents[document_name]
    field_id = fields[field_name]
    row = next(
        (
            item
            for item in result["results"]
            if item["document_id"] == document_id
        ),
        None,
    )
    if row is None:
        return [f"{document_name}/{field_name}: 没有成功发布的字段结果"]
    field = next((item for item in row["fields"] if item["field_id"] == field_id), None)
    if field is None:
        return [f"{document_name}/{field_name}: 结果缺少该字段"]

    allowed = (
        [expectation["status"]]
        if "status" in expectation
        else list(expectation.get("status_any") or [])
    )
    if field["status"] not in allowed:
        failures.append(
            f"{document_name}/{field_name}: 状态 {field['status']}，预期 {allowed}"
        )
    if field["status"] == "not_mentioned":
        return failures

    actual = _field_value(field)
    if "expected_value" in expectation:
        expected = expectation["expected_value"]
        if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
            if abs(float(actual) - float(expected)) > 1e-9:
                failures.append(f"{field_name}: 取值 {actual}，预期 {expected}")
        elif actual != expected and _compact(str(actual)) != _compact(str(expected)):
            failures.append(f"{field_name}: 取值 {actual!r}，预期 {expected!r}")
    if expectation.get("must_include"):
        values = actual if isinstance(actual, list) else []
        missing = [item for item in expectation["must_include"] if item not in values]
        if missing:
            failures.append(f"{field_name}: 列表缺少 {missing}")
    expected_precision = expectation.get("expected_precision")
    if expected_precision and field.get("date_precision") != expected_precision:
        failures.append(
            f"{field_name}: 日期精度 {field.get('date_precision')}，预期 {expected_precision}"
        )

    excerpts = [_compact(item["excerpt"]) for item in field.get("citations", [])]
    anchors = list(expectation.get("anchors") or [])
    if expectation.get("anchor"):
        anchors.append(expectation["anchor"])
    for anchor in anchors:
        target = _compact(anchor)
        if not any(target in excerpt for excerpt in excerpts):
            failures.append(f"{field_name}: 缺少预期原文依据 {anchor}")
    return failures


def _check_conclusions(
    case: dict[str, Any],
    result: dict[str, Any],
    documents: dict[str, str],
    fields: dict[str, str],
) -> list[str]:
    failures: list[str] = []
    actual = result.get("conclusions", [])
    for expected in case.get("conclusions", []):
        field_id = fields[expected["field"]]
        expected_docs = {documents[name] for name in expected["documents"]}
        match = next(
            (
                item
                for item in actual
                if item["field_id"] == field_id and item["kind"] == expected["kind"]
            ),
            None,
        )
        if match is None:
            failures.append(
                f"对比结论缺少 {expected['kind']}/{expected['field']}"
            )
            continue
        if not expected_docs.issubset(set(match["document_ids"])):
            failures.append(
                f"对比结论 {expected['kind']}/{expected['field']} 缺少参与文档"
            )
        if expected["kind"] in {"common", "difference"} and not match["citations"]:
            failures.append(f"对比结论 {expected['kind']} 缺少依据")
    return failures


def _wait_documents(
    client: ApiClient, team_id: str, kb_id: str, expected: set[str]
) -> dict[str, dict[str, Any]]:
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        items = client.list_documents(team_id, kb_id)
        by_title = {item["title"]: item for item in items}
        failed = [
            name
            for name in expected
            if name in by_title and by_title[name]["status"] == "failed"
        ]
        if failed:
            raise RuntimeError(f"文档处理失败：{', '.join(failed)}")
        if all(
            name in by_title and by_title[name]["status"] == "ready"
            for name in expected
        ):
            return by_title
        time.sleep(2)
    raise TimeoutError("等待文档处理完成超时")


def _wait_analysis(
    client: ApiClient,
    team_id: str,
    kb_id: str,
    task_id: str,
    timeout: int = 600,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = client.get_analysis(team_id, kb_id, task_id)
        if result["task"]["status"] in TERMINAL:
            return result
        time.sleep(1.5)
    raise TimeoutError(f"分析任务 {task_id} 超时")


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args: argparse.Namespace) -> int:
    cases_path = Path(args.cases).resolve()
    corpus = Path(args.corpus).resolve()
    output = Path(args.results).resolve()
    payload = json.loads(cases_path.read_text(encoding="utf-8"))
    selected = set(args.case or [])
    cases = [
        case for case in payload["cases"] if not selected or case["id"] in selected
    ]
    unknown = selected - {case["id"] for case in cases}
    if unknown:
        raise ValueError(f"未知样例：{', '.join(sorted(unknown))}")

    email = os.environ.get("KP_EVAL_EMAIL", "")
    password = os.environ.get("KP_EVAL_PASSWORD", "")
    team_id = os.environ.get("KP_EVAL_TEAM_ID", "")
    if not all((email, password, team_id)):
        raise ValueError("需要 KP_EVAL_EMAIL、KP_EVAL_PASSWORD、KP_EVAL_TEAM_ID")

    client = ApiClient(args.base_url)
    uploaded: list[str] = []
    keep = False
    started = time.perf_counter()
    try:
        client.initialize()
        client.login(email, password)
        suffix = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        kb = client.create_knowledge_base(team_id, f"__kplatform_v2_eval__{suffix}")
        for item in payload["documents"]:
            key = uuid.uuid5(uuid.NAMESPACE_URL, f"{suffix}:v2:{item['id']}")
            document = client.upload(
                team_id, kb["id"], corpus / item["file"], key
            )
            uploaded.append(document["id"])
        by_title = _wait_documents(
            client, team_id, kb["id"], {item["id"] for item in payload["documents"]}
        )
        documents = {
            name: by_title[name]["id"] for name in by_title if name in by_title
        }

        results: list[dict[str, Any]] = []
        all_stub = False
        for case in cases:
            case_started = time.perf_counter()
            task = client.create_analysis(
                team_id,
                kb["id"],
                {
                    "kind": case["kind"],
                    "document_ids": [documents[name] for name in case["documents"]],
                    "fields": case["fields"],
                    "title": f"__v2_eval__{case['id']}",
                },
                uuid.uuid5(uuid.NAMESPACE_URL, f"{suffix}:{case['id']}"),
            )
            detail = _wait_analysis(client, team_id, kb["id"], task["id"])
            providers = {
                item.get("provider")
                for item in detail["documents"]
                if item.get("provider")
            }
            summary_payload = detail.get("summary") or {}
            if summary_payload.get("provider"):
                providers.add(summary_payload["provider"])
            all_stub = all_stub or (providers == {"stub"})
            field_ids = {item["name"]: item["id"] for item in detail["fields"]}
            failures: list[str] = []
            for expectation in case["expectations"]:
                failures.extend(
                    _check_expectation(
                        expectation, detail, documents, field_ids
                    )
                )
            if case["kind"] == "comparison":
                failures.extend(
                    _check_conclusions(case, detail, documents, field_ids)
                )
            if detail["task"]["status"] not in {
                "completed",
                "partial",
            }:
                failures.append(f"任务终态 {detail['task']['status']}")
            item = {
                "case_id": case["id"],
                "kind": case["kind"],
                "task_id": task["id"],
                "status": detail["task"]["status"],
                "providers": sorted(provider for provider in providers if provider),
                "elapsed_seconds": round(time.perf_counter() - case_started, 3),
                "automatic_pass": not failures,
                "failures": failures,
                "task": detail["task"],
                "fields": detail["fields"],
                "documents": detail["documents"],
                "results": detail["results"],
                "conclusions": detail["conclusions"],
                "summary": detail["summary"],
            }
            results.append(item)
            print(
                f"{case['id']}: {item['status']} "
                f"{'pass' if item['automatic_pass'] else 'fail'} "
                f"{item['elapsed_seconds']:.2f}s"
            )

        if all_stub and not args.allow_stub:
            raise RuntimeError(
                "本次结果由确定性替身生成，不能作为真实模型质量证据；"
                "仅做链路检查时显式加 --allow-stub"
            )
        summary = {
            "case_count": len(results),
            "automatic_pass": sum(item["automatic_pass"] for item in results),
            "automatic_fail": sum(not item["automatic_pass"] for item in results),
            "providers": sorted(
                {provider for item in results for provider in item["providers"]}
            ),
            "stub_only": all_stub,
            "quality_evidence": not all_stub,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "created_at": datetime.now(UTC).isoformat(),
                    "base_url": args.base_url,
                    "team_id": team_id,
                    "knowledge_base_id": kb["id"],
                    "git_commit": os.environ.get("KP_EVAL_GIT_COMMIT", ""),
                    "corpus_sha256": {
                        item["id"]: _hash_file(corpus / item["file"])
                        for item in payload["documents"]
                    },
                    "summary": summary,
                    "case_results": results,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        keep = args.keep_resources
        return (
            0
            if summary["automatic_fail"] == 0
            and (not all_stub or args.allow_stub)
            else 1
        )
    finally:
        if not keep:
            for document_id in uploaded:
                try:
                    client.delete_document(team_id, document_id)
                except Exception as exc:  # noqa: BLE001
                    print(f"warning: 无法清理 {document_id}: {exc}", file=sys.stderr)
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="V2 固定样例执行")
    subparsers = parser.add_subparsers(dest="command", required=True)
    command = subparsers.add_parser("run", help="上传公开样例并执行提取/对比")
    command.add_argument("--base-url", default="http://127.0.0.1:8080")
    command.add_argument("--cases", default=str(DEFAULT_CASES))
    command.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    command.add_argument(
        "--results", default="evaluation/v2/results/current/results.json"
    )
    command.add_argument("--case", action="append", help="只运行指定样例 ID，可重复")
    command.add_argument(
        "--allow-stub",
        action="store_true",
        help="明确允许替身结果；只用于链路和界面验证，标记为非质量证据",
    )
    command.add_argument(
        "--keep-resources",
        action="store_true",
        help="保留评测资料；默认在结束后逻辑删除",
    )
    args = parser.parse_args()
    try:
        raise SystemExit(run(args))
    except (ValueError, RuntimeError, TimeoutError, httpx.HTTPError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
