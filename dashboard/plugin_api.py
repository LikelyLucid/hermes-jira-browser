"""FastAPI backend for the native Hermes Jira Browser plugin."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator


_SERVICE_PATH = Path(__file__).with_name("jira_service.py")
_SPEC = importlib.util.spec_from_file_location("hermes_jira_browser_service", _SERVICE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Could not load Jira service from {_SERVICE_PATH}")
SERVICE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(SERVICE)

_CONTEXT_PATH = Path(__file__).with_name("repository_context.py")
_CONTEXT_SPEC = importlib.util.spec_from_file_location("hermes_jira_browser_repository_context", _CONTEXT_PATH)
if _CONTEXT_SPEC is None or _CONTEXT_SPEC.loader is None:
    raise RuntimeError(f"Could not load repository context from {_CONTEXT_PATH}")
REPOSITORY_CONTEXT = importlib.util.module_from_spec(_CONTEXT_SPEC)
_CONTEXT_SPEC.loader.exec_module(REPOSITORY_CONTEXT)

router = APIRouter()


class ProjectMappingRequest(BaseModel):
    hermes_project_id: str = Field(min_length=1)
    hermes_project_label: str = Field(min_length=1)
    repo_path: str = Field(min_length=1)


class WorktreeRequest(BaseModel):
    jira_project_key: str = Field(min_length=1)
    issue_key: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    base_ref: str = "HEAD"


class SessionLinkRequest(BaseModel):
    issue_id: str = Field(min_length=1)
    issue_key: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    connection_id: str = Field(default="local", min_length=1, max_length=200)
    profile_name: str = Field(default="default", min_length=1, max_length=200)
    target_profile: str | None = Field(default=None, min_length=1, max_length=200)
    clear_detachment: bool = False

    @field_validator("connection_id", "profile_name", "target_profile", mode="before")
    @classmethod
    def _validate_owner_field(cls, value: Any, info):
        if value is None and info.field_name == "target_profile":
            return None
        return SERVICE.validate_owner_field(value, field_name=info.field_name)

    @model_validator(mode="after")
    def _default_target_profile(self):
        if self.target_profile is None:
            self.target_profile = self.profile_name
        return self


class CommentRequest(BaseModel):
    body: str = Field(min_length=1, max_length=32_000)
    idempotency_key: str = Field(min_length=16, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,199}$")

    @field_validator("body")
    @classmethod
    def _normalise_body(cls, value: str) -> str:
        body = value.strip()
        if not body:
            raise ValueError("body must not be blank")
        return body


class TransitionRequest(BaseModel):
    transition_id: str = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=16, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,199}$")

    @field_validator("transition_id")
    @classmethod
    def _normalise_transition_id(cls, value: str) -> str:
        transition_id = value.strip()
        if not transition_id:
            raise ValueError("transition_id must not be blank")
        return transition_id


def _store():
    # Legacy rows have no Jira origin and remain quarantined until explicitly
    # adopted; never infer ownership from the currently loaded Jira config.
    return SERVICE.JiraStore(SERVICE.default_store_path())


def _client():
    return SERVICE.JiraClient(SERVICE.load_jira_config())


def _safe_http_error(exc: Exception, *, status_code: int = 500) -> HTTPException:
    if isinstance(exc, (SERVICE.MutationConflictError, SERVICE.MutationPendingError)):
        status_code = 409
    elif isinstance(exc, ValueError):
        status_code = 400
    return HTTPException(status_code=status_code, detail=str(exc))


def _mutation_failure_releases_claim(exc: Exception) -> bool:
    return isinstance(exc, (SERVICE.JiraPreRequestError, SERVICE.JiraDefinitiveRejectionError, ValueError))


async def _run_mutation(
    *,
    action: str,
    issue_key: str,
    idempotency_key: str,
    payload: dict[str, Any],
    operation,
) -> dict[str, Any]:
    # Resolve configuration and endpoint syntax before reserving a mutation key.
    client = await asyncio.to_thread(_client)
    store = _store()
    reservation = await asyncio.to_thread(
        store.reserve_mutation,
        idempotency_key=idempotency_key,
        action=action,
        issue_key=issue_key,
        payload=payload,
    )
    if reservation["status"] == "completed":
        return reservation["result"]
    try:
        result = await asyncio.to_thread(operation, client)
    except Exception as exc:
        if _mutation_failure_releases_claim(exc):
            await asyncio.to_thread(store.release_mutation, idempotency_key)
        # Ambiguous transport/outcome errors retain the pending receipt so a
        # later retry cannot issue a possibly-duplicating Jira write.
        raise
    await asyncio.to_thread(store.complete_mutation, idempotency_key, result)
    return result


def _validate_active_owner(
    profile_name: str,
    connection_id: str,
    target_profile: str | None = None,
) -> tuple[str, str]:
    profile = SERVICE.validate_owner_field(profile_name, field_name="profile_name")
    connection = SERVICE.validate_owner_field(connection_id, field_name="connection_id")
    active_profile = SERVICE.active_profile_name()
    if connection != "local" or active_profile is None or profile != active_profile:
        raise ValueError("Session owner is not registered to this backend.")
    target = SERVICE.validate_owner_field(target_profile or profile, field_name="target_profile")
    if target != active_profile:
        raise ValueError("Target profile is not registered to this backend.")
    return profile, connection


@router.get("/status")
async def status() -> dict[str, Any]:
    return await asyncio.to_thread(SERVICE.config_status)


@router.get("/settings")
async def settings() -> dict[str, Any]:
    try:
        return {"settings": await asyncio.to_thread(SERVICE.load_settings)}
    except Exception as exc:
        raise _safe_http_error(exc) from exc


@router.put("/settings")
async def put_settings(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return {"settings": await asyncio.to_thread(SERVICE.save_settings, payload)}
    except Exception as exc:
        raise _safe_http_error(exc) from exc


@router.get("/issues")
async def search_issues(
    jql: str = "assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC",
    max_results: int = 50,
    next_page_token: str | None = None,
) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            _client().search,
            jql,
            max_results=max_results,
            next_page_token=next_page_token,
        )
    except Exception as exc:
        raise _safe_http_error(exc, status_code=502) from exc


@router.get("/issues/{issue_key}")
async def issue(issue_key: str) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(_client().issue, issue_key)
    except Exception as exc:
        raise _safe_http_error(exc, status_code=502) from exc


@router.get("/issues/{issue_key}/repository-context")
async def issue_repository_context(issue_key: str, base_ref: str = "HEAD") -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            REPOSITORY_CONTEXT.get_repository_context,
            _store(),
            issue_key,
            base_ref=base_ref,
        )
    except Exception:
        return REPOSITORY_CONTEXT.unavailable_context(issue_key)


@router.get("/issues/{issue_key}/attachments/{attachment_id}/preview")
async def attachment_preview(issue_key: str, attachment_id: str) -> dict[str, Any]:
    try:
        attachment = await asyncio.to_thread(_client().attachment_preview, issue_key, attachment_id)
        return {"attachment": attachment}
    except Exception as exc:
        raise _safe_http_error(exc, status_code=502) from exc


@router.post("/issues/{issue_key}/comments")
async def add_comment(issue_key: str, payload: CommentRequest) -> dict[str, Any]:
    try:
        return await _run_mutation(
            action="comment",
            issue_key=issue_key,
            idempotency_key=payload.idempotency_key,
            payload={"body": payload.body},
            operation=lambda client: {"comment": client.add_comment(issue_key, payload.body)},
        )
    except Exception as exc:
        raise _safe_http_error(exc, status_code=502) from exc


@router.get("/issues/{issue_key}/transitions")
async def issue_transitions(issue_key: str) -> dict[str, Any]:
    try:
        return {"transitions": await asyncio.to_thread(_client().transitions, issue_key)}
    except Exception as exc:
        raise _safe_http_error(exc, status_code=502) from exc


@router.post("/issues/{issue_key}/transitions")
async def transition_issue(issue_key: str, payload: TransitionRequest) -> dict[str, Any]:
    try:
        return await _run_mutation(
            action="transition",
            issue_key=issue_key,
            idempotency_key=payload.idempotency_key,
            payload={"transition_id": payload.transition_id},
            operation=lambda client: client.transition_issue(issue_key, payload.transition_id),
        )
    except Exception as exc:
        raise _safe_http_error(exc, status_code=502) from exc


@router.get("/mappings")
async def project_mappings() -> dict[str, Any]:
    try:
        mappings = await asyncio.to_thread(_store().all_project_mappings)
        return {"mappings": mappings}
    except Exception as exc:
        raise _safe_http_error(exc) from exc


@router.get("/mappings/{jira_project_key}")
async def project_mapping(jira_project_key: str) -> dict[str, Any]:
    try:
        mapping = await asyncio.to_thread(_store().get_project_mapping, jira_project_key)
        return {"mapping": mapping}
    except Exception as exc:
        raise _safe_http_error(exc) from exc


@router.get("/sessions/{jira_project_key}")
async def project_sessions(jira_project_key: str) -> dict[str, Any]:
    try:
        mapping = await asyncio.to_thread(_store().get_project_mapping, jira_project_key)
        if not mapping:
            return {"sessions": []}
        sessions = await asyncio.to_thread(SERVICE.list_linkable_sessions, mapping["repo_path"])
        return {"sessions": sessions}
    except Exception as exc:
        raise _safe_http_error(exc) from exc


@router.put("/mappings/{jira_project_key}")
async def put_project_mapping(jira_project_key: str, payload: ProjectMappingRequest) -> dict[str, Any]:
    try:
        # Resolve through Git before persisting. The renderer cannot map a Jira
        # project to a missing directory or an arbitrary non-repository path.
        repo_path = await asyncio.to_thread(SERVICE._repo_root, payload.repo_path)
        mapping = await asyncio.to_thread(
            _store().set_project_mapping,
            jira_project_key=jira_project_key,
            hermes_project_id=payload.hermes_project_id,
            hermes_project_label=payload.hermes_project_label,
            repo_path=str(repo_path),
        )
        return {"mapping": mapping}
    except Exception as exc:
        raise _safe_http_error(exc) from exc


@router.post("/worktrees")
async def create_worktree(payload: WorktreeRequest) -> dict[str, Any]:
    try:
        mapping = await asyncio.to_thread(_store().get_project_mapping, payload.jira_project_key)
        if not mapping:
            raise ValueError(f"Jira project {payload.jira_project_key.upper()} is not linked to a Hermes Project.")
        return await asyncio.to_thread(
            SERVICE.create_worktree,
            repo_path=mapping["repo_path"],
            issue_key=payload.issue_key,
            summary=payload.summary,
            base_ref=payload.base_ref.strip() or "HEAD",
        )
    except Exception as exc:
        raise _safe_http_error(exc) from exc


@router.post("/worktrees/cleanup")
async def cleanup_worktree(payload: WorktreeRequest) -> dict[str, Any]:
    try:
        mapping = await asyncio.to_thread(_store().get_project_mapping, payload.jira_project_key)
        if not mapping:
            raise ValueError(f"Jira project {payload.jira_project_key.upper()} is not linked to a Hermes Project.")
        return await asyncio.to_thread(
            SERVICE.cleanup_worktree,
            repo_path=mapping["repo_path"],
            issue_key=payload.issue_key,
        )
    except Exception as exc:
        raise _safe_http_error(exc) from exc


@router.get("/links/{issue_id}")
async def issue_links(issue_id: str) -> dict[str, Any]:
    try:
        store = _store()
        jira_origin = _client().config.base_url
        links = await asyncio.to_thread(store.links_for_issue, issue_id, jira_origin=jira_origin)
        detached = await asyncio.to_thread(store.detached_links_for_issue, issue_id, jira_origin=jira_origin)
        links = await asyncio.to_thread(SERVICE.enrich_session_links, links)
        return {"links": links, "detached": detached}
    except Exception as exc:
        raise _safe_http_error(exc) from exc


@router.delete("/links/{issue_id}/{session_id}")
async def unlink_session(
    issue_id: str,
    session_id: str,
    connection_id: str = "local",
    profile_name: str = "default",
    target_profile: str | None = None,
) -> dict[str, bool]:
    try:
        profile_name, connection_id = _validate_active_owner(
            profile_name,
            connection_id,
            target_profile,
        )
        target_profile = SERVICE.validate_owner_field(
            target_profile or profile_name,
            field_name="target_profile",
        )
        client = _client()
        unlinked = await asyncio.to_thread(
            _store().unlink_session,
            issue_id=issue_id,
            session_id=session_id,
            jira_origin=client.config.base_url,
            connection_id=connection_id,
            profile_name=profile_name,
            target_profile=target_profile,
        )
        return {"unlinked": unlinked}
    except Exception as exc:
        raise _safe_http_error(exc) from exc


@router.post("/links")
async def link_session(payload: SessionLinkRequest) -> dict[str, Any]:
    try:
        profile_name, connection_id = _validate_active_owner(
            payload.profile_name,
            payload.connection_id,
            payload.target_profile,
        )
        target_profile = SERVICE.validate_owner_field(
            payload.target_profile or profile_name,
            field_name="target_profile",
        )
        store = _store()
        client = _client()
        issue = await asyncio.to_thread(client.issue, payload.issue_key)
        if str(issue.get("id") or "") != payload.issue_id.strip() or str(issue.get("key") or "").upper() != payload.issue_key.strip().upper():
            raise ValueError("The Jira issue id and key do not identify the same issue.")
        active_profile = SERVICE.active_profile_name()
        if (
            connection_id == "local"
            and active_profile is not None
            and profile_name == active_profile
            and target_profile == active_profile
        ):
            metadata = await asyncio.to_thread(
                SERVICE.validated_link_metadata,
                store,
                issue_key=payload.issue_key,
                session_id=payload.session_id,
            )
        else:
            jira_project_key = payload.issue_key.strip().upper().rsplit("-", 1)[0]
            mapping = await asyncio.to_thread(store.get_project_mapping, jira_project_key)
            metadata = {
                "project_id": mapping.get("hermes_project_id") if mapping else None,
                "worktree_path": None,
                "branch": None,
            }
        link = await asyncio.to_thread(
            store.link_session,
            issue_id=payload.issue_id,
            issue_key=payload.issue_key,
            session_id=payload.session_id,
            jira_origin=client.config.base_url,
            connection_id=connection_id,
            profile_name=profile_name,
            target_profile=target_profile,
            project_id=metadata["project_id"],
            worktree_path=metadata["worktree_path"],
            branch=metadata["branch"],
            clear_detachment=payload.clear_detachment,
        )
        return {"link": link}
    except Exception as exc:
        raise _safe_http_error(exc) from exc
