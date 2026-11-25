"""T12 clinic-owned form templates, patient responses, review, and corrections."""

import math
from datetime import date
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import require_roles
from app.database import (
    FormTemplate,
    FormTemplateQuestion,
    FormTemplateVersion,
    Patient,
    PatientForm,
    PatientFormRevision,
    User,
    get_db,
    utcnow,
)
from app.portal.models import PortalAccount, PortalFormSubmission

router = APIRouter(prefix="/forms", tags=["forms"])
MANAGERS = ("clinic_manager", "administrator")
DOCTORS = ("clinic_manager", "doctor", "administrator")
STAFF_ROLES = ("clinic_manager", "doctor", "assistant", "reception", "administrator")
QUESTION_TYPES = {"short_text", "long_text", "yes_no", "single_choice", "multiple_choice", "date", "number"}


class Definition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sections: list[dict[str, Any]] = Field(min_length=1)

    @field_validator("sections")
    @classmethod
    def valid_sections(cls, sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
        section_ids: set[str] = set()
        question_ids: set[str] = set()
        previous: dict[str, dict[str, Any]] = {}
        for section in sections:
            if not isinstance(section, dict) or not isinstance(section.get("id"), str) or not section["id"] or not isinstance(section.get("title"), str) or not section["title"].strip() or not isinstance(section.get("questions"), list):
                raise ValueError("Each section requires id, title, and questions")
            if section["id"] in section_ids:
                raise ValueError("Section IDs must be unique")
            section_ids.add(section["id"])
            for question in section["questions"]:
                if not isinstance(question, dict):
                    raise TypeError("Questions must be objects")
                qid, qtype = question.get("id"), question.get("type")
                if not isinstance(qid, str) or not qid or qid in question_ids or not isinstance(qtype, str) or qtype not in QUESTION_TYPES:
                    raise ValueError("Question IDs must be unique and question type must be supported")
                if not isinstance(question.get("label"), str) or not question["label"].strip() or not isinstance(question.get("required", False), bool):
                    raise ValueError("Questions require a label and boolean required flag")
                opts = question.get("options", [])
                if qtype in {"single_choice", "multiple_choice"} and (not isinstance(opts, list) or not opts or any(not isinstance(o, dict) or not isinstance(o.get("value"), str) or not isinstance(o.get("label"), str) for o in opts) or len({o["value"] for o in opts}) != len(opts)):
                    raise ValueError("Choice questions require unique value/label options")
                condition = question.get("condition")
                if condition is not None:
                    if not isinstance(condition, dict) or set(condition) != {"question_id", "operator", "value"} or condition["operator"] != "equals" or not isinstance(condition["question_id"], str) or condition["question_id"] not in previous:
                        raise ValueError("Conditions must reference an earlier question and use equals")
                    controller = previous[condition["question_id"]]
                    if controller.get("condition") is not None:
                        raise ValueError("Conditions cannot depend on another conditional question")
                    allowed = {"yes", "no"} if controller["type"] == "yes_no" else {o["value"] for o in controller.get("options", [])} if controller["type"] == "single_choice" else set()
                    if not isinstance(condition["value"], str) or condition["value"] not in allowed:
                        raise ValueError("Condition value must be yes/no or an allowed single-choice value")
                question_ids.add(qid)
                previous[qid] = question
        return sections


class TemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class VersionCreate(BaseModel):
    definition: Definition


class AnswersUpdate(BaseModel):
    answers: dict[str, Any]


class ReviewInput(BaseModel):
    decision: Literal["validated", "needs_clarification"]
    reason: str | None = Field(default=None, max_length=1000)


class CorrectionInput(BaseModel):
    answers: dict[str, Any]
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def reason_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A correction reason is required")
        return value.strip()


def _visible_questions(definition: dict[str, Any], answers: dict[str, Any]) -> dict[str, dict[str, Any]]:
    visible: dict[str, dict[str, Any]] = {}
    questions = {q["id"]: q for section in definition["sections"] for q in section["questions"]}
    for section in definition["sections"]:
        for question in section["questions"]:
            condition = question.get("condition")
            actual = answers.get(condition["question_id"]) if condition else None
            controller = questions.get(condition["question_id"]) if condition else None
            if controller and controller["type"] == "yes_no" and isinstance(actual, bool):
                actual = "yes" if actual else "no"
            if condition is None or actual == condition["value"]:
                visible[question["id"]] = question
    return visible


def _validate_answers(definition: dict[str, Any], answers: dict[str, Any], *, submitting: bool) -> None:
    all_questions = {q["id"]: q for section in definition["sections"] for q in section["questions"]}
    if set(answers) - all_questions.keys():
        raise HTTPException(422, "Unknown question in answers")
    visible = _visible_questions(definition, answers)
    if set(answers) - visible.keys():
        raise HTTPException(422, "Answers to conditionally hidden questions are not allowed")
    for qid, question in visible.items():
        answer = answers.get(qid)
        if qid not in answers or answer is None or answer == [] or isinstance(answer, str) and not answer.strip():
            if submitting and question.get("required", False):
                raise HTTPException(422, f"Required question {qid} is unanswered")
            continue
        value, kind = answer, question["type"]
        valid = (
            isinstance(value, str) and (kind in {"short_text", "long_text"}) and len(value) <= (500 if kind == "short_text" else 5000)
            or isinstance(value, bool) and kind == "yes_no"
            or isinstance(value, str) and kind == "single_choice" and value in {o["value"] for o in question.get("options", [])}
            or isinstance(value, list) and kind == "multiple_choice" and all(isinstance(x, str) and x in {o["value"] for o in question.get("options", [])} for x in value) and len(set(value)) == len(value)
            or isinstance(value, str) and kind == "date" and _is_date(value)
            or isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and kind == "number"
        )
        if not valid:
            raise HTTPException(422, f"Invalid answer for question {qid}")


def _is_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _form_payload(form: PatientForm, version: FormTemplateVersion, *, include_definition: bool = False, include_answers: bool = False) -> dict[str, Any]:
    payload = {
        "id": str(form.id), "patient_id": str(form.patient_id), "template_version_id": str(version.id),
        "template_name": None, "version_number": version.version_number, "source": form.source,
        "status": form.status, "created_at": form.created_at.isoformat(),
        "submitted_at": form.submitted_at.isoformat() if form.submitted_at else None,
        "validated_by": str(form.validated_by_id) if form.validated_by_id else None,
        "validated_at": form.validated_at.isoformat() if form.validated_at else None,
        "clarification_reason": form.clarification_reason,
    }
    if include_definition:
        payload["definition"] = version.definition
    if include_answers:
        payload["answers"] = form.answers
    return payload


async def _replace_question_rows(db: AsyncSession, version: FormTemplateVersion, definition: dict[str, Any]) -> None:
    await db.execute(delete(FormTemplateQuestion).where(FormTemplateQuestion.template_version_id == version.id))
    position = 0
    for section in definition["sections"]:
        for question in section["questions"]:
            db.add(FormTemplateQuestion(
                clinic_id=version.clinic_id,
                template_version_id=version.id,
                question_id=question["id"],
                section_id=section["id"],
                position=position,
                question_type=question["type"],
                definition=question,
            ))
            position += 1


async def _version(db: AsyncSession, user: User, version_id: UUID) -> FormTemplateVersion:
    version = await db.scalar(select(FormTemplateVersion).where(FormTemplateVersion.id == version_id, FormTemplateVersion.clinic_id == user.clinic_id))
    if not version:
        raise HTTPException(404, "Form version not found")
    return version


async def _form(db: AsyncSession, user: User, form_id: UUID) -> PatientForm:
    form = await db.scalar(select(PatientForm).where(PatientForm.id == form_id, PatientForm.clinic_id == user.clinic_id))
    if not form:
        raise HTTPException(404, "Patient form not found")
    return form


@router.get("/templates")
async def list_templates(db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*STAFF_ROLES))):
    query = select(FormTemplate, FormTemplateVersion).join(FormTemplateVersion, FormTemplateVersion.template_id == FormTemplate.id).where(FormTemplate.clinic_id == user.clinic_id, FormTemplateVersion.clinic_id == user.clinic_id)
    if user.role not in MANAGERS:
        query = query.where(FormTemplateVersion.status == "published")
    rows = (await db.execute(query.order_by(FormTemplate.created_at, FormTemplateVersion.version_number))).all()
    result = []
    for template, version in rows:
        item = {"id": str(template.id), "name": template.name, "version_id": str(version.id), "version_number": version.version_number, "status": version.status}
        if user.role in MANAGERS:
            item["definition"] = version.definition
        result.append(item)
    return result


@router.post("/templates", status_code=201)
async def create_template(data: TemplateCreate, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    template = FormTemplate(clinic_id=user.clinic_id, name=data.name.strip(), created_by_id=user.id)
    db.add(template)
    await db.flush()
    version = FormTemplateVersion(clinic_id=user.clinic_id, template_id=template.id, version_number=1, status="draft", definition={"sections": []}, created_by_id=user.id)
    db.add(version)
    await db.commit()
    return {"id": str(template.id), "name": template.name, "version_id": str(version.id), "version_number": 1}


@router.post("/templates/{template_id}/versions", status_code=201)
async def create_version(template_id: UUID, data: VersionCreate, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    template = await db.scalar(select(FormTemplate).where(FormTemplate.id == template_id, FormTemplate.clinic_id == user.clinic_id).with_for_update())
    if not template:
        raise HTTPException(404, "Template not found")
    number = (await db.scalar(select(func.max(FormTemplateVersion.version_number)).where(FormTemplateVersion.template_id == template.id)) or 0) + 1
    version = FormTemplateVersion(clinic_id=user.clinic_id, template_id=template.id, version_number=number, status="draft", definition=data.definition.model_dump(), created_by_id=user.id)
    db.add(version)
    await db.flush()
    await _replace_question_rows(db, version, version.definition)
    await db.commit()
    return {"id": str(version.id), "version_number": number, "status": version.status, "definition": version.definition}


@router.put("/versions/{version_id}")
async def update_version(version_id: UUID, data: VersionCreate, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    version = await _version(db, user, version_id)
    if version.status != "draft":
        raise HTTPException(409, "Published versions are immutable; create a new version")
    version.definition = data.definition.model_dump()
    await _replace_question_rows(db, version, version.definition)
    await db.commit()
    return {"id": str(version.id), "version_number": version.version_number, "status": version.status, "definition": version.definition}


@router.post("/versions/{version_id}/publish")
async def publish_version(version_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    version = await db.scalar(select(FormTemplateVersion).where(FormTemplateVersion.id == version_id, FormTemplateVersion.clinic_id == user.clinic_id).with_for_update())
    if not version:
        raise HTTPException(404, "Form version not found")
    if version.status != "draft" or not version.definition.get("sections"):
        raise HTTPException(409, "Version must be a non-empty draft")
    version.status, version.published_by_id, version.published_at = "published", user.id, utcnow()
    await db.commit()
    return {"id": str(version.id), "status": version.status, "published_at": version.published_at.isoformat()}


@router.get("/patients/{patient_id}")
async def patient_forms(patient_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*STAFF_ROLES))):
    patient = await db.scalar(select(Patient).where(Patient.id == patient_id, Patient.clinic_id == user.clinic_id))
    if not patient:
        raise HTTPException(404, "Patient not found")
    rows = (await db.execute(select(PatientForm, FormTemplateVersion, FormTemplate).join(FormTemplateVersion, PatientForm.template_version_id == FormTemplateVersion.id).join(FormTemplate, FormTemplateVersion.template_id == FormTemplate.id).where(PatientForm.clinic_id == user.clinic_id, PatientForm.patient_id == patient.id).order_by(PatientForm.created_at.desc()))).all()
    return [{**_form_payload(form, version, include_answers=False), "template_name": template.name} for form, version, template in rows]


@router.post("/patients/{patient_id}/{version_id}", status_code=201)
async def create_patient_form(patient_id: UUID, version_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*STAFF_ROLES))):
    patient = await db.scalar(select(Patient).where(Patient.id == patient_id, Patient.clinic_id == user.clinic_id))
    version = await _version(db, user, version_id)
    if not patient or version.status != "published":
        raise HTTPException(404, "Patient or published form version not found")
    form = PatientForm(clinic_id=user.clinic_id, patient_id=patient.id, template_version_id=version.id, source="staff", status="draft", answers={}, created_by_id=user.id)
    db.add(form)
    await db.commit()
    return _form_payload(form, version, include_definition=True, include_answers=user.role in DOCTORS)


@router.get("/{form_id}")
async def get_patient_form(form_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*STAFF_ROLES))):
    form = await _form(db, user, form_id)
    version = await _version(db, user, form.template_version_id)
    template = await db.scalar(select(FormTemplate).where(FormTemplate.id == version.template_id, FormTemplate.clinic_id == user.clinic_id))
    payload = {**_form_payload(form, version, include_definition=True, include_answers=user.role in DOCTORS), "template_name": template.name if template else None}
    if user.role in DOCTORS and form.source == "patient":
        rows = (await db.execute(
            select(PortalFormSubmission, PortalAccount.full_name)
            .join(PortalAccount, PortalAccount.id == PortalFormSubmission.account_id)
            .where(PortalFormSubmission.patient_form_id == form.id, PortalFormSubmission.clinic_id == user.clinic_id)
            .order_by(PortalFormSubmission.revision_number)
        )).all()
        payload["portal_submissions"] = [{
            "revision_number": submission.revision_number,
            "submitted_at": submission.submitted_at.isoformat(),
            "author": author,
            "source": "patient",
            "template_version_id": str(form.template_version_id),
            "answers": submission.answers,
        } for submission, author in rows]
    return payload


@router.put("/{form_id}/answers")
async def update_answers(form_id: UUID, data: AnswersUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*STAFF_ROLES))):
    form = await _form(db, user, form_id)
    if form.status not in {"draft", "needs_clarification"}:
        raise HTTPException(409, "Only draft or clarification forms can be edited")
    version = await _version(db, user, form.template_version_id)
    _validate_answers(version.definition, data.answers, submitting=False)
    form.answers = data.answers
    await db.commit()
    return _form_payload(form, version, include_definition=True, include_answers=user.role in DOCTORS)


@router.post("/{form_id}/submit")
async def submit_form(form_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*STAFF_ROLES))):
    form = await _form(db, user, form_id)
    if form.status not in {"draft", "needs_clarification"}:
        raise HTTPException(409, "Form cannot be submitted from its current state")
    version = await _version(db, user, form.template_version_id)
    _validate_answers(version.definition, form.answers, submitting=True)
    form.status, form.submitted_at, form.clarification_reason = "submitted", utcnow(), None
    await db.commit()
    return _form_payload(form, version, include_definition=True, include_answers=user.role in DOCTORS)


@router.post("/{form_id}/review")
async def review_form(form_id: UUID, data: ReviewInput, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*DOCTORS))):
    form = await _form(db, user, form_id)
    if form.status != "submitted":
        raise HTTPException(409, "Only submitted forms can be reviewed")
    if data.decision == "needs_clarification" and not (data.reason and data.reason.strip()):
        raise HTTPException(422, "Clarification reason is required")
    if data.decision == "validated":
        form.status, form.validated_by_id, form.validated_at, form.clarification_reason = "validated", user.id, utcnow(), None
    else:
        form.status, form.clarification_reason = "needs_clarification", data.reason.strip()
    version = await _version(db, user, form.template_version_id)
    await db.commit()
    return _form_payload(form, version, include_definition=True, include_answers=True)


@router.post("/{form_id}/corrections")
async def correct_validated_form(form_id: UUID, data: CorrectionInput, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*DOCTORS))):
    form = await _form(db, user, form_id)
    if form.status != "validated":
        raise HTTPException(409, "Only validated forms require a historical correction")
    version = await _version(db, user, form.template_version_id)
    _validate_answers(version.definition, data.answers, submitting=True)
    revision_number = (await db.scalar(select(func.max(PatientFormRevision.revision_number)).where(PatientFormRevision.patient_form_id == form.id)) or 0) + 1
    db.add(PatientFormRevision(clinic_id=user.clinic_id, patient_form_id=form.id, revision_number=revision_number, answers=form.answers, status=form.status, reason=data.reason, actor_id=user.id, validated_by_id=form.validated_by_id, validated_at=form.validated_at))
    form.answers, form.status, form.submitted_at = data.answers, "submitted", utcnow()
    form.validated_by_id, form.validated_at = None, None
    await db.commit()
    return {**_form_payload(form, version, include_definition=True, include_answers=True), "revision_number": revision_number}


@router.get("/{form_id}/revisions")
async def form_revisions(form_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*DOCTORS))):
    form = await _form(db, user, form_id)
    revisions = (await db.execute(select(PatientFormRevision, User.full_name).join(
        User, User.id == PatientFormRevision.actor_id
    ).where(
        PatientFormRevision.patient_form_id == form.id,
        PatientFormRevision.clinic_id == user.clinic_id,
    ).order_by(PatientFormRevision.revision_number))).all()
    return [{
        "revision_number": revision.revision_number,
        "answers": revision.answers,
        "status": revision.status,
        "reason": revision.reason,
        "actor_id": str(revision.actor_id),
        "actor_name": actor_name,
        "validated_by": str(revision.validated_by_id) if revision.validated_by_id else None,
        "validated_at": revision.validated_at.isoformat() if revision.validated_at else None,
        "created_at": revision.created_at.isoformat(),
    } for revision, actor_name in revisions]
