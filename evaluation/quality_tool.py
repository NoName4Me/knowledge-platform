from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import statistics
import sys
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

SCENARIOS = ("document_qa", "policy_lookup", "knowledge_learning")
KINDS = ("answerable", "unanswerable", "confusing")
CORPUS_ORDER = (
    "http-semantics",
    "robots-protocol",
    "wcag-22",
    "cc-by-4.0",
    "python-lists",
    "python-exceptions",
    "html-structure",
    "css-grid",
)
FIELDS = {
    "id",
    "scenario",
    "kind",
    "question",
    "expected_document",
    "expected_terms",
    "evidence",
}


@dataclass(frozen=True)
class Question:
    id: str
    scenario: str
    kind: str
    question: str
    expected_document: str | None
    expected_terms: list[str]
    evidence: str


@dataclass
class RunResult:
    id: str
    scenario: str
    kind: str
    question: str
    expected_document: str | None
    expected_terms: list[str]
    evidence: str
    status: str | None = None
    answer: str | None = None
    citations: list[dict[str, Any]] | None = None
    first_content_seconds: float | None = None
    complete_seconds: float | None = None
    error_code: str | None = None
    error_message: str | None = None
    automatic_pass: bool | None = None
    automatic_notes: list[str] | None = None
    human_correct: bool | None = None
    human_citation_supported: bool | None = None
    human_note: str | None = None


def load_questions(path: Path) -> list[Question]:
    questions: list[Question] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            raw = json.loads(line)
            if set(raw) != FIELDS:
                raise ValueError(f"{path}:{line_number}: 字段不符合固定合同")
            question = Question(**raw)
            if question.id in seen:
                raise ValueError(f"{path}:{line_number}: 重复 id {question.id}")
            seen.add(question.id)
            questions.append(question)

    if len(questions) != 60:
        raise ValueError(f"固定题集必须恰好 60 题，实际 {len(questions)}")
    for scenario in SCENARIOS:
        items = [item for item in questions if item.scenario == scenario]
        if len(items) != 20:
            raise ValueError(f"{scenario} 必须恰好 20 题，实际 {len(items)}")
    if abs(
        sum(item.kind == "unanswerable" for item in questions) / len(questions) - 1 / 3
    ) > 0.05:
        raise ValueError("无依据题须占题集的 1/3（允许 ±5 个百分点）")
    for scenario in SCENARIOS:
        scenario_items = [item for item in questions if item.scenario == scenario]
        if abs(
            sum(item.kind == "unanswerable" for item in scenario_items)
            / len(scenario_items)
            - 1 / 3
        ) > 0.1:
            raise ValueError(f"{scenario} 的无依据题比例偏离 1/3")
    for question in questions:
        if question.kind == "answerable":
            if (
                question.expected_document not in CORPUS_ORDER
                or not question.expected_terms
            ):
                raise ValueError(f"{question.id}: 有答案题缺少预期资料或关键词")
        elif question.expected_document is not None or question.expected_terms:
            raise ValueError(f"{question.id}: 无依据/易混淆题不能声明确定依据")
        if not question.question.strip() or not question.evidence.strip():
            raise ValueError(f"{question.id}: 问题与预期依据不能为空")
    return questions


def corpus_manifest(root: Path) -> dict[str, str]:
    manifest: dict[str, str] = {}
    for name in CORPUS_ORDER:
        path = root / f"{name}.txt"
        if not path.is_file():
            raise ValueError(f"缺少评测资料 {path}")
        manifest[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return manifest


class ApiClient:
    def __init__(self, base_url: str) -> None:
        self.client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=90.0)
        self.csrf = ""

    async def close(self) -> None:
        await self.client.aclose()

    async def initialize(self) -> None:
        response = await self.client.get("/api/auth/csrf")
        response.raise_for_status()
        self.csrf = response.json()["csrf_token"]

    def _headers(self) -> dict[str, str]:
        base_url = self.client.base_url
        base_origin = f"{base_url.scheme}://{base_url.host}"
        if base_url.port is not None:
            base_origin += f":{base_url.port}"
        return {
            "Origin": base_origin,
            "X-CSRF-Token": self.csrf,
        }

    async def login(self, email: str, password: str) -> dict[str, Any]:
        response = await self.client.post(
            "/api/auth/login",
            json={"email": email, "password": password},
            headers=self._headers(),
        )
        response.raise_for_status()
        data = response.json()
        self.csrf = data["csrf_token"]
        return data

    async def create_knowledge_base(
        self, team_id: str, name: str
    ) -> dict[str, Any]:
        response = await self.client.post(
            f"/api/teams/{team_id}/knowledge-bases",
            json={"name": name},
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()

    async def upload(
        self, team_id: str, knowledge_base_id: str, path: Path, document_uuid: uuid.UUID
    ) -> dict[str, Any]:
        response = await self.client.post(
            f"/api/teams/{team_id}/documents",
            data={"knowledge_base_id": knowledge_base_id},
            files={"file": (path.name, path.read_bytes(), "text/plain")},
            headers={
                **self._headers(),
                "Idempotency-Key": str(document_uuid),
            },
        )
        response.raise_for_status()
        return response.json()

    async def list_documents(
        self, team_id: str, knowledge_base_id: str
    ) -> list[dict[str, Any]]:
        response = await self.client.get(
            f"/api/teams/{team_id}/documents",
            params={"knowledge_base_id": knowledge_base_id},
        )
        response.raise_for_status()
        return response.json()

    async def delete_document(self, team_id: str, document_id: str) -> None:
        base_url = self.client.base_url
        origin = f"{base_url.scheme}://{base_url.host}"
        if base_url.port is not None:
            origin += f":{base_url.port}"
        response = await self.client.delete(
            f"/api/teams/{team_id}/documents/{document_id}",
            headers={"Origin": origin, "X-CSRF-Token": self.csrf},
        )
        response.raise_for_status()

    async def create_conversation(
        self, team_id: str, knowledge_base_id: str, title: str
    ) -> dict[str, Any]:
        response = await self.client.post(
            f"/api/teams/{team_id}/knowledge-bases/{knowledge_base_id}/conversations",
            json={"title": title},
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()

    async def get_turn(
        self, team_id: str, knowledge_base_id: str, conversation_id: str, turn_id: str
    ) -> dict[str, Any]:
        response = await self.client.get(
            f"/api/teams/{team_id}/knowledge-bases/{knowledge_base_id}/"
            f"conversations/{conversation_id}/turns"
        )
        response.raise_for_status()
        for turn in response.json():
            if turn["id"] == turn_id:
                return turn
        raise RuntimeError(f"轮次 {turn_id} 未出现在本人历史中")

    async def stream_turn(
        self,
        team_id: str,
        knowledge_base_id: str,
        conversation_id: str,
        question: str,
    ) -> AsyncIterator[dict[str, Any]]:
        response = await self.client.send(
            self.client.build_request(
                "POST",
                f"/api/teams/{team_id}/knowledge-bases/{knowledge_base_id}/"
                f"conversations/{conversation_id}/turns/stream",
                json={"client_request_id": str(uuid.uuid4()), "question": question},
                headers={**self._headers(), "Accept": "text/event-stream"},
            ),
            stream=True,
        )
        try:
            response.raise_for_status()
            event_name = ""
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    event_name = line.removeprefix("event:").strip()
                elif line.startswith("data:"):
                    payload = line.removeprefix("data:").strip()
                    if event_name == "done":
                        yield {"type": "done"}
                        event_name = ""
                        continue
                    if payload:
                        yield json.loads(payload)
        finally:
            await response.aclose()


def check_answer(result: RunResult) -> None:
    notes: list[str] = []
    passed = result.status in {"completed", "insufficient"}
    citations = result.citations or []
    answer = result.answer or ""
    if result.status != "completed" and result.kind != "unanswerable":
        passed = False
        notes.append(f"终态为 {result.status}")
    if result.kind == "unanswerable":
        if result.status != "insufficient":
            passed = False
            notes.append("无依据题未进入 insufficient")
        if citations:
            passed = False
            notes.append("无依据题仍返回引用")
    else:
        if result.status != "completed":
            passed = False
        if not citations:
            passed = False
            notes.append("有依据题没有引用")
        if result.expected_document and not any(
            citation["document_title"] == result.expected_document
            for citation in citations
        ):
            passed = False
            notes.append("引用未包含预期文档")
        missing = [term for term in result.expected_terms if term.lower() not in answer.lower()]
        if missing:
            passed = False
            notes.append(f"答案缺少预期词：{', '.join(missing)}")
    result.automatic_pass = passed
    result.automatic_notes = notes


def percentile(values: list[float], ratio: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(ratio * len(ordered)) - 1)
    return ordered[index]


def summarize(results: list[RunResult]) -> dict[str, Any]:
    answerable = [item for item in results if item.kind != "unanswerable"]
    unanswerable = [item for item in results if item.kind == "unanswerable"]
    first_times = [
        item.first_content_seconds
        for item in results
        if item.first_content_seconds is not None
    ]
    complete_times = [
        item.complete_seconds
        for item in results
        if item.complete_seconds is not None
    ]
    return {
        "question_count": len(results),
        "automatic_pass_rate": (
            sum(bool(item.automatic_pass) for item in results) / len(results)
            if results
            else 0.0
        ),
        "automatic_answerable_pass_rate": (
            sum(bool(item.automatic_pass) for item in answerable) / len(answerable)
            if answerable
            else 0.0
        ),
        "automatic_unanswerable_pass_rate": (
            sum(bool(item.automatic_pass) for item in unanswerable) / len(unanswerable)
            if unanswerable
            else 0.0
        ),
        "first_content_p95_seconds": percentile(first_times, 0.95),
        "complete_p95_seconds": percentile(complete_times, 0.95),
        "first_content_mean_seconds": (
            statistics.fmean(first_times) if first_times else None
        ),
        "complete_mean_seconds": (
            statistics.fmean(complete_times) if complete_times else None
        ),
        "errors": sum(item.status not in {"completed", "insufficient"} for item in results),
    }


def write_packets(
    results: list[RunResult], output: Path, *, include_answers: bool
) -> None:
    with output.open("w", encoding="utf-8") as destination:
        for result in results:
            payload = asdict(result)
            if not include_answers:
                payload["answer"] = None
                payload["citations"] = None
                payload["automatic_pass"] = None
                payload["automatic_notes"] = None
            destination.write(json.dumps(payload, ensure_ascii=False) + "\n")


def load_results(path: Path) -> list[RunResult]:
    with path.open(encoding="utf-8") as source:
        return [RunResult(**json.loads(line)) for line in source if line.strip()]


def write_review_score(
    source: Path, reviewed: Path, destination: Path
) -> dict[str, Any]:
    original = {item.id: item for item in load_results(source)}
    review = load_results(reviewed)
    if len(review) != 60 or {item.id for item in review} != set(original):
        raise ValueError("人工评审文件必须与原结果使用完全相同的 60 个 id")

    answerable = [item for item in review if item.kind != "unanswerable"]
    unanswerable = [item for item in review if item.kind == "unanswerable"]
    unanswered = [
        item.id
        for item in review
        if item.human_correct is None
        or (item.kind != "unanswerable" and item.human_citation_supported is None)
    ]
    if unanswered:
        raise ValueError(f"仍有题未完成人工评审：{', '.join(unanswered)}")

    correct = sum(bool(item.human_correct) for item in answerable) / len(answerable)
    supported = (
        sum(bool(item.human_citation_supported) for item in answerable)
        / len(answerable)
    )
    refused = sum(bool(item.human_correct) for item in unanswerable) / len(unanswerable)
    summary = {
        "question_count": len(review),
        "answerable_correct_rate": correct,
        "citation_supported_rate": supported,
        "unanswerable_refusal_rate": refused,
        "thresholds_passed": correct >= 0.9 and supported >= 0.9 and refused >= 0.9,
    }
    destination.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


async def wait_for_documents(
    client: ApiClient,
    team_id: str,
    knowledge_base_id: str,
    expected: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    expected = expected or set(CORPUS_ORDER)
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        by_title = {
            document["title"]: document
            for document in await client.list_documents(team_id, knowledge_base_id)
        }
        if all(
            name in by_title and by_title[name]["status"] == "ready"
            for name in expected
        ):
            return by_title
        failed = [
            name
            for name in expected
            if name in by_title and by_title[name]["status"] == "failed"
        ]
        if failed:
            raise RuntimeError(f"文档处理失败：{', '.join(failed)}")
        await asyncio.sleep(2)
    raise TimeoutError("等待 8 份文档处理完成超时")


async def run_question(
    client: ApiClient,
    question: Question,
    team_id: str,
    knowledge_base_id: str,
) -> RunResult:
    conversation = await client.create_conversation(
        team_id, knowledge_base_id, f"__kplatform_eval__{question.id}"
    )
    result = RunResult(**asdict(question))
    started = time.perf_counter()
    turn_id: str | None = None
    try:
        async for event in client.stream_turn(
            team_id, knowledge_base_id, conversation["id"], question.question
        ):
            if event["type"] == "turn" and event.get("turn"):
                turn_id = event["turn"]["id"]
                if event["turn"]["status"] != "answering":
                    result.answer = event["turn"]["answer"]
                    result.citations = event["turn"]["citations"]
            elif event["type"] == "delta":
                if result.first_content_seconds is None and event.get("text"):
                    result.first_content_seconds = time.perf_counter() - started
            elif event["type"] == "error":
                result.error_code = event.get("error_code")
                result.error_message = event.get("message")
        result.complete_seconds = time.perf_counter() - started
        if turn_id:
            turn = await client.get_turn(
                team_id, knowledge_base_id, conversation["id"], turn_id
            )
            result.status = turn["status"]
            result.answer = turn["answer"]
            result.citations = turn["citations"]
            result.error_code = turn["error_code"]
    except Exception as exc:  # noqa: BLE001
        result.complete_seconds = time.perf_counter() - started
        result.status = "runtime_error"
        result.error_message = f"{type(exc).__name__}: {exc}"
    check_answer(result)
    return result


async def execute(args: argparse.Namespace) -> int:
    questions_path = Path(args.questions).resolve()
    corpus_root = Path(args.corpus).resolve()
    results_path = Path(args.results).resolve()
    questions = load_questions(questions_path)
    manifest = corpus_manifest(corpus_root)
    results_path.parent.mkdir(parents=True, exist_ok=True)

    email = os.environ.get("KP_EVAL_EMAIL", "")
    password = os.environ.get("KP_EVAL_PASSWORD", "")
    team_id = os.environ.get("KP_EVAL_TEAM_ID", "")
    if not all((email, password, team_id)):
        raise ValueError(
            "需要 KP_EVAL_EMAIL、KP_EVAL_PASSWORD、KP_EVAL_TEAM_ID 环境变量"
        )

    client = ApiClient(args.base_url)
    uploaded_document_ids: list[str] = []
    keep_resources = False
    try:
        await client.initialize()
        await client.login(email, password)
        suffix = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        kb = await client.create_knowledge_base(team_id, f"__kplatform_eval__{suffix}")
        for name in CORPUS_ORDER:
            document = await client.upload(
                team_id,
                kb["id"],
                corpus_root / f"{name}.txt",
                uuid.uuid5(uuid.NAMESPACE_URL, f"{suffix}:{name}"),
            )
            uploaded_document_ids.append(document["id"])
        documents = await wait_for_documents(client, team_id, kb["id"])

        if args.jobs == 1:
            results = []
            for question in questions:
                result = await run_question(client, question, team_id, kb["id"])
                results.append(result)
                print(f"{question.id}: {result.status} {result.complete_seconds:.2f}s")
        else:
            semaphore = asyncio.Semaphore(args.jobs)

            async def bounded(question: Question) -> RunResult:
                async with semaphore:
                    started = time.perf_counter()
                    result = await run_question(
                        client, question, team_id, kb["id"]
                    )
                    print(
                        f"batch={started:.3f} {question.id}: "
                        f"{result.status} {result.complete_seconds:.2f}s"
                    )
                    return result

            results = await asyncio.gather(*(bounded(item) for item in questions))

        summary = summarize(results)
        if not args.keep_resources:
            for document_id in uploaded_document_ids:
                await client.delete_document(team_id, document_id)
            uploaded_document_ids.clear()
        summary["resources_deleted"] = not args.keep_resources
        run_metadata = {
            "created_at": datetime.now(UTC).isoformat(),
            "base_url": args.base_url,
            "team_id": team_id,
            "knowledge_base_id": kb["id"],
            "jobs": args.jobs,
            "corpus_sha256": manifest,
            "document_ids": {
                name: documents[name]["id"] for name in CORPUS_ORDER
            },
            "git_commit": os.environ.get("KP_EVAL_GIT_COMMIT", ""),
            "generate_model": os.environ.get("KP_EVAL_GENERATE_MODEL", ""),
            "embedding_model": os.environ.get("KP_EVAL_EMBEDDING_MODEL", ""),
            "summary": summary,
        }
        (results_path.parent / "run-metadata.json").write_text(
            json.dumps(run_metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        write_packets(results, results_path, include_answers=True)
        write_packets(
            results,
            results_path.with_name("review-blind.jsonl"),
            include_answers=False,
        )
        write_packets(
            results,
            results_path.with_name("review-template.jsonl"),
            include_answers=False,
        )
        keep_resources = args.keep_resources
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["errors"] == 0 else 1
    finally:
        if not keep_resources:
            for document_id in uploaded_document_ids:
                try:
                    await client.delete_document(team_id, document_id)
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"warning: 无法清理评测文档 {document_id}: {exc}",
                        file=sys.stderr,
                    )
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="K-Platform 固定题集与并发验收")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="校验固定题集和语料清单")
    validate.add_argument(
        "--questions",
        default="evaluation/dataset/questions.jsonl",
    )
    validate.add_argument("--corpus", default="evaluation/corpus")

    run = subparsers.add_parser("run", help="实际上传语料并通过流式接口提问")
    run.add_argument("--base-url", default="http://127.0.0.1:5173")
    run.add_argument("--questions", default="evaluation/dataset/questions.jsonl")
    run.add_argument("--corpus", default="evaluation/corpus")
    run.add_argument(
        "--results", default="evaluation/results/current/results.jsonl"
    )
    run.add_argument("--jobs", type=int, default=1)
    run.add_argument(
        "--keep-resources",
        action="store_true",
        help="成功后保留本次评测文档，便于排查；默认结束后逻辑删除",
    )

    score = subparsers.add_parser("score", help="汇总人工评审结果")
    score.add_argument("--source", required=True)
    score.add_argument("--reviewed", required=True)
    score.add_argument("--output", required=True)

    args = parser.parse_args()
    if args.command == "validate":
        questions = load_questions(Path(args.questions))
        manifest = corpus_manifest(Path(args.corpus))
        print(
            json.dumps(
                {
                    "questions": len(questions),
                    "scenarios": {
                        scenario: sum(
                            item.scenario == scenario for item in questions
                        )
                        for scenario in SCENARIOS
                    },
                    "kinds": {
                        kind: sum(item.kind == kind for item in questions)
                        for kind in KINDS
                    },
                    "corpora": manifest,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "score":
        summary = write_review_score(
            Path(args.source), Path(args.reviewed), Path(args.output)
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if not summary["thresholds_passed"]:
            raise SystemExit(1)
        return
    if args.jobs < 1:
        parser.error("--jobs 必须大于零")
    try:
        raise SystemExit(asyncio.run(execute(args)))
    except (ValueError, TimeoutError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
