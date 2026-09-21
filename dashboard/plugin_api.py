"""FastAPI backend for the native Hermes Jira Browser plugin."""

from __future__ import annotations

import asyncio
import importlib.util
import re
import urllib.parse
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator


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


MAX_BATCH_ISSUES = 50
MAX_BATCH_CONCURRENCY = 8
MAX_CHAT_CONTEXT_SESSION_ID_CHARS = 256
MAX_CHAT_CONTEXT_PROFILE_CHARS = 200
MAX_CHAT_CONTEXT_CONNECTION_CHARS = 200
MAX_CHAT_CONTEXT_SUMMARY_CHARS = 400
MAX_CHAT_CONTEXT_STATUS_CHARS = 120
MAX_CHAT_CONTEXT_COMMENT_CHARS = 6_000
MAX_CHAT_CONTEXT_ATTACHMENT_NAME_CHARS = 160
MAX_CHAT_CONTEXT_ATTACHMENT_MIME_CHARS = 80
MAX_CHAT_CONTEXT_COMMENTS = 3
MAX_CHAT_CONTEXT_ATTACHMENTS = 20


class ProjectMappingRequest(BaseModel):
    hermes_project_id: str = Field(min_length=1)
    hermes_project_label: str = Field(min_length=1)
    repo_path: str = Field(min_length=1)


class WorktreeRequest(BaseModel):
    jira_project_key: str = Field(min_length=1)
    issue_key: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    base_ref: str = "HEAD"


class IssueBatchRequest(BaseModel):
    issue_keys: list[str] = Field(min_length=1, max_length=MAX_BATCH_ISSUES)
    include_transitions: bool = True
    include_links: bool = True
    include_details: bool = False
    connection_id: str = Field(min_length=1, max_length=200)
    profile_name: str = Field(min_length=1, max_length=200)
    target_profile: str = Field(min_length=1, max_length=200)

    @field_validator("connection_id", "profile_name", "target_profile", mode="before")
    @classmethod
    def _validate_batch_owner(cls, value: Any, info):
        return SERVICE.validate_owner_field(value, field_name=info.field_name)


class SessionLinkRequest(BaseModel):
    issue_id: str = Field(min_length=1)
    issue_key: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    connection_id: str = Field(min_length=1, max_length=200)
    profile_name: str = Field(min_length=1, max_length=200)
    target_profile: str = Field(min_length=1, max_length=200)
    clear_detachment: bool = False

    @field_validator("connection_id", "profile_name", "target_profile", mode="before")
    @classmethod
    def _validate_owner_field(cls, value: Any, info):
        return SERVICE.validate_owner_field(value, field_name=info.field_name)


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
    return isinstance(exc, (SERVICE.JiraPreRequestError, SERVICE.JiraDefinitiveRejectionError))


async def _run_mutation(
    *,
    action: str,
    issue_key: str,
    idempotency_key: str,
    payload: dict[str, Any],
    operation,
) -> dict[str, Any]:
    # Reserve before loading Jira configuration so a completed replay is
    # answerable from durable local state even when Jira is currently offline.
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
        client = await asyncio.to_thread(_client)
    except Exception:
        # No Jira request can have been sent while constructing the client.
        await asyncio.to_thread(store.release_mutation, idempotency_key)
        raise
    try:
        result = await asyncio.to_thread(operation, client)
    except Exception as exc:
        # Only explicit pre-request and definitive-rejection classifications
        # prove that a retry cannot duplicate a Jira write. All other failures
        # retain the pending receipt as an ambiguous outcome.
        if _mutation_failure_releases_claim(exc):
            await asyncio.to_thread(store.release_mutation, idempotency_key)
        raise
    await asyncio.to_thread(store.complete_mutation, idempotency_key, result)
    return result


def _validate_active_owner(
    profile_name: str,
    connection_id: str,
    target_profile: str,
) -> tuple[str, str, str]:
    profile = SERVICE.validate_owner_field(profile_name, field_name="profile_name")
    connection = SERVICE.validate_owner_field(connection_id, field_name="connection_id")
    active_profile = SERVICE.active_profile_name()
    if connection != "local" or active_profile is None or profile != active_profile:
        raise ValueError("Session owner is not registered to this backend.")
    target = SERVICE.validate_owner_field(target_profile, field_name="target_profile")
    if target != active_profile:
        raise ValueError("Target profile is not registered to this backend.")
    return profile, connection, target


def _owner_qualified_rows(
    rows: list[dict[str, Any]],
    *,
    jira_origin: str,
    connection_id: str,
    profile_name: str,
    target_profile: str,
) -> list[dict[str, Any]]:
    """Keep only rows whose complete owner key matches this request."""
    expected = SERVICE.JiraStore._normalize_read_owner(
        jira_origin,
        connection_id,
        profile_name,
        target_profile,
    )
    qualified: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            actual = SERVICE.JiraStore._normalize_read_owner(
                row.get("jira_origin"),
                row.get("connection_id"),
                row.get("profile_name"),
                row.get("target_profile"),
            )
        except ValueError:
            continue
        if actual == expected:
            qualified.append(row)
    return qualified


async def _load_issue_batch_item(
    client: Any,
    store: Any,
    issue_key: str,
    *,
    include_transitions: bool,
    include_links: bool,
    include_details: bool,
    owner: tuple[str, str, str],
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    async with semaphore:
        try:
            issue = await asyncio.to_thread(
                client.issue if include_details else client.issue_summary,
                issue_key,
            )
            transitions = (
                await asyncio.to_thread(client.transitions, issue_key)
                if include_transitions
                else []
            )
            links: list[dict[str, Any]] = []
            if include_links and store is not None:
                origin = SERVICE.JiraStore._normalize_jira_origin(client.config.base_url)
                raw_links = await asyncio.to_thread(
                    store.links_for_issue,
                    str(issue.get("id") or ""),
                    jira_origin=origin,
                    # _validate_active_owner returns (profile, connection, target).
                    connection_id=owner[1],
                    profile_name=owner[0],
                    target_profile=owner[2],
                )
                links = await asyncio.to_thread(SERVICE.enrich_session_links, raw_links)
            return {"issue": issue, "transitions": transitions, "links": links}
        except Exception:
            return {"issue_key": issue_key, "error": "Could not load issue data."}


@router.post("/issues/batch")
async def issue_batch(payload: IssueBatchRequest) -> dict[str, Any]:
    """Load a bounded set of issues with one scoped, concurrency-limited call."""
    keys = [str(key).strip().upper() for key in payload.issue_keys]
    if any(not re.fullmatch(r"[A-Z][A-Z0-9]+-\d+", key) for key in keys):
        raise _safe_http_error(ValueError("issue_keys must contain valid Jira issue keys."))
    keys = list(dict.fromkeys(keys))
    if not keys or len(keys) > MAX_BATCH_ISSUES:
        raise _safe_http_error(ValueError(f"issue_keys must contain between 1 and {MAX_BATCH_ISSUES} issue keys."))
    try:
        owner = _validate_active_owner(
            payload.profile_name,
            payload.connection_id,
            payload.target_profile,
        )
        client = _client()
        store = _store() if payload.include_links else None
        semaphore = asyncio.Semaphore(MAX_BATCH_CONCURRENCY)
        items = await asyncio.gather(*(
            _load_issue_batch_item(
                client,
                store,
                issue_key,
                include_transitions=payload.include_transitions,
                include_links=payload.include_links,
                include_details=payload.include_details,
                owner=owner,
                semaphore=semaphore,
            )
            for issue_key in keys
        ))
        return {
            "items": items,
            "bounded": True,
            "max_items": MAX_BATCH_ISSUES,
            "requested_items": len(keys),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise _safe_http_error(exc, status_code=502) from exc


def _bounded_context_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _bounded_context_size(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _bounded_chat_context(issue: dict[str, Any], link: dict[str, Any]) -> dict[str, Any]:
    comments_raw: list[Any] = []
    attachments_raw: list[Any] = []
    raw_comments = issue.get("comments")
    raw_attachments = issue.get("attachments")
    if isinstance(raw_comments, list):
        comments_raw = raw_comments
    if isinstance(raw_attachments, list):
        attachments_raw = raw_attachments
    comments = [comment for comment in comments_raw if isinstance(comment, dict)][:MAX_CHAT_CONTEXT_COMMENTS]
    attachments = [attachment for attachment in attachments_raw if isinstance(attachment, dict)][:MAX_CHAT_CONTEXT_ATTACHMENTS]
    return {
        "session_id": _bounded_context_text(link.get("session_id"), MAX_CHAT_CONTEXT_SESSION_ID_CHARS),
        "issue_id": _bounded_context_text(link.get("issue_id"), 200),
        "issue_key": _bounded_context_text(issue.get("key") or link.get("issue_key"), 100),
        "title": _bounded_context_text(issue.get("summary"), MAX_CHAT_CONTEXT_SUMMARY_CHARS),
        "summary": _bounded_context_text(issue.get("summary"), MAX_CHAT_CONTEXT_SUMMARY_CHARS),
        "status": _bounded_context_text(issue.get("status"), MAX_CHAT_CONTEXT_STATUS_CHARS),
        "status_category": _bounded_context_text(issue.get("status_category"), MAX_CHAT_CONTEXT_STATUS_CHARS),
        "comments": [
            {
                "id": _bounded_context_text(comment.get("id"), 100),
                "body": _bounded_context_text(comment.get("body"), MAX_CHAT_CONTEXT_COMMENT_CHARS),
            }
            for comment in comments[:MAX_CHAT_CONTEXT_COMMENTS]
            if isinstance(comment, dict)
        ],
        "attachments": [
            {
                "id": _bounded_context_text(attachment.get("id"), 100),
                "filename": _bounded_context_text(attachment.get("filename"), MAX_CHAT_CONTEXT_ATTACHMENT_NAME_CHARS),
                "mime_type": _bounded_context_text(attachment.get("mime_type"), MAX_CHAT_CONTEXT_ATTACHMENT_MIME_CHARS),
                "size": _bounded_context_size(attachment.get("size")),
            }
            for attachment in attachments[:MAX_CHAT_CONTEXT_ATTACHMENTS]
            if isinstance(attachment, dict)
        ],
    }


@router.get("/links/session/{session_id}/context")
async def focused_session_context(
    session_id: str,
    profile_name: str = "",
    connection_id: str = "",
    target_profile: str = "",
) -> dict[str, Any]:
    """Read one exact owner-qualified Jira link for native chat context."""
    values = (session_id.strip(), profile_name.strip(), connection_id.strip(), target_profile.strip())
    limits = (
        MAX_CHAT_CONTEXT_SESSION_ID_CHARS,
        MAX_CHAT_CONTEXT_PROFILE_CHARS,
        MAX_CHAT_CONTEXT_CONNECTION_CHARS,
        MAX_CHAT_CONTEXT_PROFILE_CHARS,
    )
    if not all(values) or any(len(value) > limit for value, limit in zip(values, limits)):
        return {"available": False, "reason": "owner_required"}
    try:
        _validate_active_owner(profile_name, connection_id, target_profile)
    except ValueError:
        return {"available": False, "reason": "owner_unavailable"}
    try:
        client = await asyncio.to_thread(_client)
        origin = SERVICE.JiraStore._normalize_jira_origin(client.config.base_url)
        store = _store()
        link = await asyncio.to_thread(
            store.link_for_session_owner,
            session_id=session_id,
            jira_origin=origin,
            connection_id=connection_id,
            profile_name=profile_name,
            target_profile=target_profile,
        )
        if not isinstance(link, dict):
            return {"available": False, "reason": "no_exact_link"}
        issue_key = str(link.get("issue_key") or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9]+-\d+", issue_key):
            return {"available": False, "reason": "invalid_link"}
        issue = await asyncio.to_thread(client.issue, issue_key)
        if (
            str(issue.get("id") or "") != str(link.get("issue_id") or "")
            or str(issue.get("key") or "").strip().upper() != issue_key
        ):
            return {"available": False, "reason": "issue_mismatch"}
        context = _bounded_chat_context(issue, link)
        context["issue_url"] = f"{origin}/browse/{urllib.parse.quote(issue_key, safe='-')}"
        return {"available": True, "context": context}
    except Exception as exc:
        raise _safe_http_error(exc, status_code=502) from exc


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
async def issue_links(
    issue_id: str,
    connection_id: str,
    profile_name: str,
    target_profile: str,
) -> dict[str, Any]:
    try:
        profile_name, connection_id, target_profile = _validate_active_owner(
            profile_name,
            connection_id,
            target_profile,
        )
        target_profile = SERVICE.validate_owner_field(
            target_profile,
            field_name="target_profile",
        )
        store = _store()
        client = _client()
        jira_origin = SERVICE.JiraStore._normalize_jira_origin(client.config.base_url)
        links = await asyncio.to_thread(
            store.links_for_issue,
            issue_id,
            jira_origin=jira_origin,
            connection_id=connection_id,
            profile_name=profile_name,
            target_profile=target_profile,
        )
        detached = await asyncio.to_thread(
            store.detached_links_for_issue,
            issue_id,
            jira_origin=jira_origin,
            connection_id=connection_id,
            profile_name=profile_name,
            target_profile=target_profile,
        )
        links = _owner_qualified_rows(
            links,
            jira_origin=jira_origin,
            connection_id=connection_id,
            profile_name=profile_name,
            target_profile=target_profile,
        )
        detached = _owner_qualified_rows(
            detached,
            jira_origin=jira_origin,
            connection_id=connection_id,
            profile_name=profile_name,
            target_profile=target_profile,
        )
        links = await asyncio.to_thread(SERVICE.enrich_session_links, links)
        return {"links": links, "detached": detached}
    except Exception as exc:
        raise _safe_http_error(exc) from exc


@router.delete("/links/{issue_id}/{session_id}")
async def unlink_session(
    issue_id: str,
    session_id: str,
    connection_id: str,
    profile_name: str,
    target_profile: str,
) -> dict[str, bool]:
    try:
        profile_name, connection_id, target_profile = _validate_active_owner(
            profile_name,
            connection_id,
            target_profile,
        )
        target_profile = SERVICE.validate_owner_field(
            target_profile,
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
        profile_name, connection_id, target_profile = _validate_active_owner(
            payload.profile_name,
            payload.connection_id,
            payload.target_profile,
        )
        target_profile = SERVICE.validate_owner_field(
            payload.target_profile,
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
