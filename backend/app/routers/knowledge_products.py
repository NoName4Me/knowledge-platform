from __future__ import annotations

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app import knowledge_products as products
from app.auth import TeamContext, get_team_context
from app.db import get_session
from app.schemas import (
    AnalysisSuggestionOut,
    AnalysisSuggestionRequest,
    DocumentGuideOut,
    DocumentSearchOut,
    NoteCreateRequest,
    NoteListOut,
    NoteOut,
    NoteUpdateRequest,
    ReportFromAnalysisRequest,
    ReportFromSourcesRequest,
    ReportGapOut,
    ReportListOut,
    ReportOut,
    ReportSectionSuggestionOut,
    ReportSectionSuggestionRequest,
    ReportUpdateRequest,
    SavedRequirementCreate,
    SavedRequirementListOut,
    SavedRequirementOut,
)

router = APIRouter(
    prefix="/api/teams/{team_id}/knowledge-bases/{knowledge_base_id}",
    tags=["knowledge-products"],
)


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, products.KnowledgeProductError):
        return HTTPException(status_code=exc.status_code, detail=exc.detail)
    return HTTPException(status_code=500, detail="处理失败")


@router.get("/notes", response_model=NoteListOut)
def list_notes(
    knowledge_base_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> NoteListOut:
    try:
        return NoteListOut(
            items=[
                products._note_out(item)
                for item in products.list_notes(session, context, knowledge_base_id)
            ]
        )
    except products.KnowledgeProductError as exc:
        raise _http(exc) from exc


@router.post("/notes", response_model=NoteOut, status_code=201)
def create_note(
    knowledge_base_id: uuid.UUID,
    payload: NoteCreateRequest,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> NoteOut:
    try:
        note = products.create_note(session, context, knowledge_base_id, payload)
        session.commit()
        return note
    except products.KnowledgeProductError as exc:
        session.rollback()
        raise _http(exc) from exc


@router.get("/notes/{note_id}", response_model=NoteOut)
def get_note(
    knowledge_base_id: uuid.UUID,
    note_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> NoteOut:
    try:
        return products.get_note(session, context, knowledge_base_id, note_id)
    except products.KnowledgeProductError as exc:
        raise _http(exc) from exc


@router.put("/notes/{note_id}", response_model=NoteOut)
def update_note(
    knowledge_base_id: uuid.UUID,
    note_id: uuid.UUID,
    payload: NoteUpdateRequest,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> NoteOut:
    try:
        note = products.update_note(session, context, knowledge_base_id, note_id, payload)
        session.commit()
        return note
    except products.KnowledgeProductError as exc:
        session.rollback()
        raise _http(exc) from exc


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(
    knowledge_base_id: uuid.UUID,
    note_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> None:
    try:
        products.delete_note(session, context, knowledge_base_id, note_id)
        session.commit()
    except products.KnowledgeProductError as exc:
        session.rollback()
        raise _http(exc) from exc


@router.get("/reports", response_model=ReportListOut)
def list_reports(
    knowledge_base_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> ReportListOut:
    try:
        return ReportListOut(
            items=products.list_reports(session, context, knowledge_base_id)
        )
    except products.KnowledgeProductError as exc:
        raise _http(exc) from exc


@router.post("/reports/from-analysis/{analysis_id}", response_model=ReportOut, status_code=201)
def create_report_from_analysis(
    knowledge_base_id: uuid.UUID,
    analysis_id: uuid.UUID,
    payload: ReportFromAnalysisRequest,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> ReportOut:
    try:
        report = products.create_report_from_analysis(
            session, context, knowledge_base_id, analysis_id, payload
        )
        session.commit()
        return report
    except products.KnowledgeProductError as exc:
        session.rollback()
        raise _http(exc) from exc


@router.post("/reports/from-sources", response_model=ReportOut, status_code=201)
def create_report_from_sources(
    knowledge_base_id: uuid.UUID,
    payload: ReportFromSourcesRequest,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> ReportOut:
    try:
        report = products.create_report_from_sources(
            session, context, knowledge_base_id, payload
        )
        session.commit()
        return report
    except products.KnowledgeProductError as exc:
        session.rollback()
        raise _http(exc) from exc


@router.get("/reports/{report_id}", response_model=ReportOut)
def get_report(
    knowledge_base_id: uuid.UUID,
    report_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> ReportOut:
    try:
        return products.get_report(session, context, knowledge_base_id, report_id)
    except products.KnowledgeProductError as exc:
        raise _http(exc) from exc


@router.put("/reports/{report_id}", response_model=ReportOut)
def update_report(
    knowledge_base_id: uuid.UUID,
    report_id: uuid.UUID,
    payload: ReportUpdateRequest,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> ReportOut:
    try:
        report = products.update_report(
            session, context, knowledge_base_id, report_id, payload
        )
        session.commit()
        return report
    except products.KnowledgeProductError as exc:
        session.rollback()
        raise _http(exc) from exc


@router.delete("/reports/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_report(
    knowledge_base_id: uuid.UUID,
    report_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> None:
    try:
        products.delete_report(session, context, knowledge_base_id, report_id)
        session.commit()
    except products.KnowledgeProductError as exc:
        session.rollback()
        raise _http(exc) from exc


@router.get("/reports/{report_id}/export.docx")
def export_report_docx(
    knowledge_base_id: uuid.UUID,
    report_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> Response:
    try:
        data = products.report_docx(session, context, knowledge_base_id, report_id)
        report = products.get_report(session, context, knowledge_base_id, report_id)
    except products.KnowledgeProductError as exc:
        raise _http(exc) from exc
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": (
                "attachment; filename*=UTF-8''"
                + quote(report.title + ".docx", safe="")
            )
        },
    )


@router.get("/reports/{report_id}/export.csv")
def export_report_csv(
    knowledge_base_id: uuid.UUID,
    report_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> Response:
    try:
        content = products.report_csv(session, context, knowledge_base_id, report_id)
    except products.KnowledgeProductError as exc:
        raise _http(exc) from exc
    return Response(
        content=content.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=report.csv"},
    )


@router.get("/reports/{report_id}/gaps", response_model=ReportGapOut)
def get_report_gaps(
    knowledge_base_id: uuid.UUID,
    report_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> ReportGapOut:
    try:
        return products.report_gaps(session, context, knowledge_base_id, report_id)
    except products.KnowledgeProductError as exc:
        raise _http(exc) from exc


@router.post(
    "/reports/{report_id}/sections/{section_id}/suggest",
    response_model=ReportSectionSuggestionOut,
)
def suggest_report_section(
    knowledge_base_id: uuid.UUID,
    report_id: uuid.UUID,
    section_id: uuid.UUID,
    payload: ReportSectionSuggestionRequest,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> ReportSectionSuggestionOut:
    try:
        return products.suggest_report_section(
            session, context, knowledge_base_id, report_id, section_id, payload
        )
    except products.KnowledgeProductError as exc:
        raise _http(exc) from exc


@router.get("/reports/{report_id}/export.xlsx")
def export_report_xlsx(
    knowledge_base_id: uuid.UUID,
    report_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> Response:
    try:
        data = products.report_xlsx(session, context, knowledge_base_id, report_id)
        report = products.get_report(session, context, knowledge_base_id, report_id)
    except products.KnowledgeProductError as exc:
        raise _http(exc) from exc
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''"
            + quote(report.title + ".xlsx", safe="")
        },
    )


@router.get("/reports/{report_id}/export.pdf")
def export_report_pdf(
    knowledge_base_id: uuid.UUID,
    report_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> Response:
    try:
        data = products.report_pdf(session, context, knowledge_base_id, report_id)
        report = products.get_report(session, context, knowledge_base_id, report_id)
    except products.KnowledgeProductError as exc:
        raise _http(exc) from exc
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''"
            + quote(report.title + ".pdf", safe="")
        },
    )


@router.get("/requirements", response_model=SavedRequirementListOut)
def list_requirements(
    knowledge_base_id: uuid.UUID,
    kind: str | None = Query(default=None),
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> SavedRequirementListOut:
    try:
        rows = products.list_requirements(session, context, knowledge_base_id, kind)
        return SavedRequirementListOut(
            items=[
                SavedRequirementOut(
                    id=row.id,
                    kind=row.kind,
                    name=row.name,
                    payload=row.payload,
                    updated_at=row.updated_at,
                )
                for row in rows
            ]
        )
    except products.KnowledgeProductError as exc:
        raise _http(exc) from exc


@router.post("/requirements", response_model=SavedRequirementOut, status_code=201)
def create_requirement(
    knowledge_base_id: uuid.UUID,
    payload: SavedRequirementCreate,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> SavedRequirementOut:
    try:
        row = products.create_requirement(session, context, knowledge_base_id, payload)
        session.commit()
        return SavedRequirementOut(
            id=row.id,
            kind=row.kind,
            name=row.name,
            payload=row.payload,
            updated_at=row.updated_at,
        )
    except products.KnowledgeProductError as exc:
        session.rollback()
        raise _http(exc) from exc


@router.delete("/requirements/{requirement_id}", status_code=204)
def delete_requirement(
    knowledge_base_id: uuid.UUID,
    requirement_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> None:
    try:
        products.delete_requirement(session, context, knowledge_base_id, requirement_id)
        session.commit()
    except products.KnowledgeProductError as exc:
        session.rollback()
        raise _http(exc) from exc


@router.post("/analysis-suggestions", response_model=AnalysisSuggestionOut)
def suggest_analysis_fields(
    knowledge_base_id: uuid.UUID,
    payload: AnalysisSuggestionRequest,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> AnalysisSuggestionOut:
    try:
        fields, rationale = products.suggest_analysis_fields(
            session, context, knowledge_base_id, payload
        )
        return AnalysisSuggestionOut(fields=fields, rationale=rationale)
    except products.KnowledgeProductError as exc:
        raise _http(exc) from exc


@router.get("/documents/{document_id}/guide", response_model=DocumentGuideOut)
def get_document_guide(
    knowledge_base_id: uuid.UUID,
    document_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> DocumentGuideOut:
    try:
        return DocumentGuideOut.model_validate(
            products.document_guide(session, context, knowledge_base_id, document_id)
        )
    except products.KnowledgeProductError as exc:
        raise _http(exc) from exc


@router.get("/document-search", response_model=DocumentSearchOut)
def search_documents(
    knowledge_base_id: uuid.UUID,
    q: str = Query(min_length=1, max_length=200),
    document_id: uuid.UUID | None = Query(default=None),
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> DocumentSearchOut:
    try:
        return DocumentSearchOut(
            query=q,
            hits=products.search_documents(
                session, context, knowledge_base_id, q, document_id
            ),
        )
    except products.KnowledgeProductError as exc:
        raise _http(exc) from exc
