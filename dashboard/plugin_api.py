"""FastAPI backend for the native Hermes Jira Browser plugin."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


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


class CommentRequest(BaseModel):
    body: str = Field(min_length=1, max_length=32_000)


class TransitionRequest(BaseModel):
    transition_id: str = Field(min_length=1, max_length=100)


def _store():
    return SERVICE.JiraStore(SERVICE.default_store_path())


def _client():
    return SERVICE.JiraClient(SERVICE.load_jira_config())


def _safe_http_error(exc: Exception, *, status_code: int = 500) -> HTTPException:
    if isinstance(exc, ValueError):
        status_code = 400
    return HTTPException(status_code=status_code, detail=str(exc))


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
        comment = await asyncio.to_thread(_client().add_comment, issue_key, payload.body)
        return {"comment": comment}
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
        return await asyncio.to_thread(_client().transition_issue, issue_key, payload.transition_id)
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
        links = await asyncio.to_thread(_store().links_for_issue, issue_id)
        links = await asyncio.to_thread(SERVICE.enrich_session_links, links)
        return {"links": links}
    except Exception as exc:
        raise _safe_http_error(exc) from exc


@router.delete("/links/{issue_id}/{session_id}")
async def unlink_session(issue_id: str, session_id: str) -> dict[str, bool]:
    try:
        unlinked = await asyncio.to_thread(
            _store().unlink_session,
            issue_id=issue_id,
            session_id=session_id,
        )
        return {"unlinked": unlinked}
    except Exception as exc:
        raise _safe_http_error(exc) from exc


@router.post("/links")
async def link_session(payload: SessionLinkRequest) -> dict[str, Any]:
    try:
        store = _store()
        metadata = await asyncio.to_thread(
            SERVICE.validated_link_metadata,
            store,
            issue_key=payload.issue_key,
            session_id=payload.session_id,
        )
        link = await asyncio.to_thread(
            store.link_session,
            issue_id=payload.issue_id,
            issue_key=payload.issue_key,
            session_id=payload.session_id,
            project_id=metadata["project_id"],
            worktree_path=metadata["worktree_path"],
            branch=metadata["branch"],
        )
        return {"link": link}
    except Exception as exc:
        raise _safe_http_error(exc) from exc
