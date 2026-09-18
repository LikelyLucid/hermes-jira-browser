"""Jira and Git services for the native Hermes Jira Browser plugin.

The module accepts a small Jira CLI-compatible config shape. Credentials never
leave this process.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

from hermes_cli._subprocess_compat import noninteractive_git_env
from hermes_cli.worktree_ops import _ensure_worktrees_gitignored
from hermes_constants import get_hermes_home


SEARCH_FIELDS = (
    "summary,status,priority,issuetype,assignee,reporter,project,labels,"
    "created,updated,resolutiondate,description"
)
DETAIL_FIELDS = f"{SEARCH_FIELDS},parent,subtasks,fixVersions,components,attachment"
MAX_JIRA_JSON_BYTES = 16 * 1024 * 1024
MAX_ATTACHMENT_PREVIEW_BYTES = 4 * 1024 * 1024
MAX_ISSUE_COMMENTS = 1_000
SAFE_ATTACHMENT_PREVIEW_TYPES = frozenset({
    "image/avif",
    "image/bmp",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
})
DEFAULT_SETTINGS: dict[str, Any] = {
    "version": 1,
    "defaultView": "assigned",
    "pageSize": 50,
    "baseRef": "HEAD",
    "groupByStatus": True,
    "views": [
        {
            "id": "assigned",
            "label": "Assigned to me",
            "jql": "assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC",
        },
        {
            "id": "reported",
            "label": "Reported by me",
            "jql": "reporter = currentUser() AND statusCategory != Done ORDER BY updated DESC",
        },
        {
            "id": "recent",
            "label": "Recently updated",
            "jql": "updated >= -14d ORDER BY updated DESC",
        },
    ],
}


class JiraConfig(NamedTuple):
    base_url: str
    email: str
    api_token: str


def default_config_path() -> Path:
    """Return the Jira CLI-compatible config path, with an explicit service override."""
    override = os.environ.get("HERMES_JIRA_CONFIG_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / "jira-config" / "config.json"


def load_jira_config(path: str | Path | None = None) -> JiraConfig:
    config_path = Path(path).expanduser() if path is not None else default_config_path()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raw = {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Jira config at {config_path} is not valid JSON.") from exc
    if not isinstance(raw, Mapping):
        raise ValueError("Jira config must be a JSON object.")

    base_url = str(os.environ.get("JIRA_BASE_URL") or os.environ.get("JIRA_SITE") or raw.get("baseUrl") or "").strip().rstrip("/")
    if base_url and "://" not in base_url:
        base_url = f"https://{base_url}"
    email = str(os.environ.get("JIRA_EMAIL") or raw.get("email") or "").strip()
    api_token = str(os.environ.get("JIRA_API_TOKEN") or raw.get("token") or raw.get("apiToken") or "").strip()
    missing = [name for name, value in (("baseUrl", base_url), ("email", email), ("token", api_token)) if not value]
    if missing:
        raise ValueError(f"Jira config is missing: {', '.join(missing)}.")
    if not base_url.startswith("https://"):
        raise ValueError("Jira baseUrl must use https:// so credentials are not sent in plaintext.")
    return JiraConfig(base_url=base_url, email=email, api_token=api_token)


def config_status(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path).expanduser() if path is not None else default_config_path()
    try:
        config = load_jira_config(config_path)
    except ValueError as exc:
        return {
            "configured": False,
            "path": str(config_path),
            "base_url": None,
            "email": None,
            "error": str(exc),
        }
    return {
        "configured": True,
        "path": str(config_path),
        "base_url": config.base_url,
        "email": config.email,
        "error": None,
    }


def _flatten_adf(node: Any, *, list_depth: int = 0) -> str:
    if isinstance(node, str):
        return node
    if not isinstance(node, Mapping):
        return ""
    node_type = str(node.get("type") or "")
    if node_type == "text":
        text = str(node.get("text") or "")
        marks = node.get("marks") if isinstance(node.get("marks"), list) else []
        link = next(
            (
                mark.get("attrs", {}).get("href")
                for mark in marks
                if isinstance(mark, Mapping)
                and mark.get("type") == "link"
                and isinstance(mark.get("attrs"), Mapping)
            ),
            None,
        )
        return f"{text} ({link})" if link and link not in text else text
    if node_type == "hardBreak":
        return "\n"

    children = node.get("content") if isinstance(node.get("content"), list) else []
    if node_type in {"bulletList", "orderedList"}:
        lines: list[str] = []
        for index, child in enumerate(children, start=1):
            value = _flatten_adf(child, list_depth=list_depth + 1).strip()
            marker = f"{index}." if node_type == "orderedList" else "•"
            if value:
                lines.append(f"{'  ' * list_depth}{marker} {value}")
        return "\n".join(lines)
    if node_type == "listItem":
        return " ".join(filter(None, (_flatten_adf(child, list_depth=list_depth).strip() for child in children)))

    text = "".join(_flatten_adf(child, list_depth=list_depth) for child in children)
    if node_type in {"paragraph", "heading", "blockquote", "codeBlock", "panel"}:
        return text.strip() + "\n\n"
    return text


def adf_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    text = _flatten_adf(value)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _name(value: Any, key: str = "name") -> str | None:
    return str(value.get(key)) if isinstance(value, Mapping) and value.get(key) is not None else None


def _normalise_attachment(attachment: Mapping[str, Any]) -> dict[str, Any]:
    mime_type = str(attachment.get("mimeType") or "application/octet-stream").strip().lower()
    return {
        "id": str(attachment.get("id") or ""),
        "filename": str(attachment.get("filename") or "Attachment"),
        "mime_type": mime_type,
        "size": max(0, int(attachment.get("size") or 0)),
        "created": attachment.get("created"),
        "author": _name(attachment.get("author"), "displayName"),
        "is_image": mime_type.startswith("image/"),
    }


def _adf_attachment_references(node: Any) -> tuple[set[str], set[str]]:
    ids: set[str] = set()
    filenames: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for child in value:
                visit(child)
            return
        if not isinstance(value, Mapping):
            return
        node_type = str(value.get("type") or "")
        attrs = value.get("attrs") if isinstance(value.get("attrs"), Mapping) else {}
        if node_type == "media":
            filename = str(attrs.get("alt") or "").strip().casefold()
            if filename:
                filenames.add(filename)
        urls: list[str] = []
        if node_type in {"inlineCard", "blockCard"} and attrs.get("url"):
            urls.append(str(attrs["url"]))
        if node_type == "text":
            for mark in value.get("marks") if isinstance(value.get("marks"), list) else []:
                mark_attrs = mark.get("attrs") if isinstance(mark, Mapping) and isinstance(mark.get("attrs"), Mapping) else {}
                if mark.get("type") == "link" and mark_attrs.get("href"):
                    urls.append(str(mark_attrs["href"]))
        for url in urls:
            match = re.search(r"/attachment/(?:content/)?(\d+)(?:/|$|[?#])", url)
            if match:
                ids.add(match.group(1))
        for child in value.get("content") if isinstance(value.get("content"), list) else []:
            visit(child)

    visit(node)
    return ids, filenames


def _normalise_comment(
    comment: Mapping[str, Any],
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    body = comment.get("body")
    attachment_ids, filenames = _adf_attachment_references(body)
    attachments_by_filename: dict[str, list[dict[str, Any]]] = {}
    for attachment in attachments or []:
        filename = str(attachment.get("filename") or "").casefold()
        if filename:
            attachments_by_filename.setdefault(filename, []).append(attachment)
    uniquely_named_ids = {
        str(matches[0].get("id") or "")
        for filename, matches in attachments_by_filename.items()
        if filename in filenames and len(matches) == 1
    }
    linked_attachments = [
        attachment
        for attachment in (attachments or [])
        if attachment.get("id") in attachment_ids
        or str(attachment.get("id") or "") in uniquely_named_ids
    ]
    return {
        "id": str(comment.get("id") or ""),
        "author": _name(comment.get("author"), "displayName"),
        "created": comment.get("created"),
        "updated": comment.get("updated"),
        "body": adf_to_text(body),
        "attachments": linked_attachments,
    }


def _normalise_issue(issue: Mapping[str, Any], *, detail: bool = False) -> dict[str, Any]:
    fields = issue.get("fields") if isinstance(issue.get("fields"), Mapping) else {}
    status = fields.get("status") if isinstance(fields.get("status"), Mapping) else {}
    status_category = status.get("statusCategory") if isinstance(status.get("statusCategory"), Mapping) else {}
    project = fields.get("project") if isinstance(fields.get("project"), Mapping) else {}
    assignee = fields.get("assignee") if isinstance(fields.get("assignee"), Mapping) else {}
    reporter = fields.get("reporter") if isinstance(fields.get("reporter"), Mapping) else {}
    result: dict[str, Any] = {
        "id": str(issue.get("id") or ""),
        "key": str(issue.get("key") or ""),
        "summary": str(fields.get("summary") or ""),
        "status": _name(status),
        "status_category": _name(status_category, "key"),
        "priority": _name(fields.get("priority")),
        "issue_type": _name(fields.get("issuetype")),
        "assignee": _name(assignee, "displayName"),
        "reporter": _name(reporter, "displayName"),
        "project_key": _name(project, "key"),
        "project_name": _name(project),
        "labels": [str(value) for value in fields.get("labels", []) if value] if isinstance(fields.get("labels"), list) else [],
        "created": fields.get("created"),
        "updated": fields.get("updated"),
        "resolution_date": fields.get("resolutiondate"),
        "description": adf_to_text(fields.get("description")),
    }
    if detail:
        attachments = [
            _normalise_attachment(attachment)
            for attachment in (fields.get("attachment") if isinstance(fields.get("attachment"), list) else [])
            if isinstance(attachment, Mapping) and attachment.get("id")
        ]
        result["attachments"] = attachments
        comments_container = fields.get("comment") if isinstance(fields.get("comment"), Mapping) else {}
        comments = comments_container.get("comments") if isinstance(comments_container.get("comments"), list) else []
        result["comments"] = [
            _normalise_comment(comment, attachments)
            for comment in comments
            if isinstance(comment, Mapping)
        ]
        result["components"] = [
            name
            for value in (fields.get("components") if isinstance(fields.get("components"), list) else [])
            if (name := _name(value))
        ]
        result["fix_versions"] = [
            name
            for value in (fields.get("fixVersions") if isinstance(fields.get("fixVersions"), list) else [])
            if (name := _name(value))
        ]
        parent = fields.get("parent") if isinstance(fields.get("parent"), Mapping) else None
        result["parent"] = _normalise_issue(parent) if parent else None
        result["subtasks"] = [
            _normalise_issue(value)
            for value in (fields.get("subtasks") if isinstance(fields.get("subtasks"), list) else [])
            if isinstance(value, Mapping)
        ]
    return result


def normalise_search(payload: Mapping[str, Any]) -> dict[str, Any]:
    issues = payload.get("issues") if isinstance(payload.get("issues"), list) else []
    return {
        "issues": [_normalise_issue(issue) for issue in issues if isinstance(issue, Mapping)],
        "next_page_token": payload.get("nextPageToken"),
        "is_last": bool(payload.get("isLast", not payload.get("nextPageToken"))),
    }


def default_settings_path() -> Path:
    return get_hermes_home() / "jira-browser" / "settings.json"


def validate_settings(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("Jira Browser settings must be a JSON object.")
    raw_views = value.get("views")
    if not isinstance(raw_views, list) or not raw_views:
        raise ValueError("settings.views must contain at least one saved view.")
    if len(raw_views) > 20:
        raise ValueError("settings.views supports at most 20 saved views.")

    views: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_views:
        if not isinstance(raw, Mapping):
            raise ValueError("Each saved view must be a JSON object.")
        view_id = str(raw.get("id") or "").strip()
        label = str(raw.get("label") or "").strip()
        jql = str(raw.get("jql") or "").strip()
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", view_id):
            raise ValueError("Each saved view id must use letters, numbers, '_' or '-'.")
        if view_id in seen:
            raise ValueError(f"Saved view id is duplicated: {view_id}")
        if not label or len(label) > 100:
            raise ValueError("Each saved view label must be 1-100 characters.")
        if not jql or len(jql) > 4_000:
            raise ValueError("Each saved view JQL must be 1-4000 characters.")
        seen.add(view_id)
        views.append({"id": view_id, "label": label, "jql": jql})

    default_view = str(value.get("defaultView") or "").strip()
    if default_view not in seen:
        raise ValueError("settings.defaultView must match one of settings.views[].id.")
    try:
        page_size = int(value.get("pageSize", 50))
    except (TypeError, ValueError) as exc:
        raise ValueError("settings.pageSize must be an integer from 1 to 100.") from exc
    if not 1 <= page_size <= 100:
        raise ValueError("settings.pageSize must be an integer from 1 to 100.")
    base_ref = str(value.get("baseRef") or "HEAD").strip()
    if not base_ref or len(base_ref) > 200 or base_ref.startswith("-"):
        raise ValueError("settings.baseRef must be a Git ref and cannot start with '-'.")
    group_by_status = value.get("groupByStatus", True)
    if not isinstance(group_by_status, bool):
        raise ValueError("settings.groupByStatus must be true or false.")
    return {
        "version": 1,
        "defaultView": default_view,
        "pageSize": page_size,
        "baseRef": base_ref,
        "groupByStatus": group_by_status,
        "views": views,
    }


def load_settings(path: str | Path | None = None) -> dict[str, Any]:
    settings_path = Path(path).expanduser() if path is not None else default_settings_path()
    try:
        raw = json.loads(settings_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return save_settings(DEFAULT_SETTINGS, settings_path)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Jira Browser settings at {settings_path} are not valid JSON.") from exc
    settings = validate_settings(raw)
    if settings != raw:
        return save_settings(settings, settings_path)
    return settings


def save_settings(value: Mapping[str, Any], path: str | Path | None = None) -> dict[str, Any]:
    settings_path = Path(path).expanduser() if path is not None else default_settings_path()
    settings = validate_settings(value)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{settings_path.name}.", dir=settings_path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(settings, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, settings_path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        Path(temp_name).unlink(missing_ok=True)
        raise
    return settings


class NoJiraRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise RuntimeError("Jira redirect refused to protect the configured credentials.")


class JiraClient:
    def __init__(self, config: JiraConfig, *, timeout: int = 20):
        self.config = config
        self.timeout = timeout

    def _request(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        method: str = "GET",
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        query = urllib.parse.urlencode({key: value for key, value in (params or {}).items() if value is not None})
        url = f"{self.config.base_url}{path}{'?' + query if query else ''}"
        credentials = base64.b64encode(f"{self.config.email}:{self.config.api_token}".encode("utf-8")).decode("ascii")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Basic {credentials}",
            "User-Agent": "Hermes-Jira-Browser/0.1",
        }
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            opener = urllib.request.build_opener(NoJiraRedirects())
            with opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_JIRA_JSON_BYTES + 1)
                if len(raw) > MAX_JIRA_JSON_BYTES:
                    raise RuntimeError("Jira JSON response is too large.")
                payload = json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise RuntimeError("Jira rejected the configured credentials.") from exc
            if exc.code == 429:
                raise RuntimeError("Jira rate limit reached; wait before refreshing.") from exc
            raise RuntimeError(f"Jira returned HTTP {exc.code}.") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError("Could not reach Jira.") from exc
        except (TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError("Jira returned an invalid or timed-out response.") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Jira returned an invalid response.")
        return payload

    def _request_bytes(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        max_bytes: int,
    ) -> tuple[bytes, str]:
        query = urllib.parse.urlencode({key: value for key, value in (params or {}).items() if value is not None})
        url = f"{self.config.base_url}{path}{'?' + query if query else ''}"
        credentials = base64.b64encode(f"{self.config.email}:{self.config.api_token}".encode("utf-8")).decode("ascii")
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "*/*",
                "Authorization": f"Basic {credentials}",
                "User-Agent": "Hermes-Jira-Browser/0.1",
            },
            method="GET",
        )
        try:
            opener = urllib.request.build_opener(NoJiraRedirects())
            with opener.open(request, timeout=self.timeout) as response:
                raw = response.read(max_bytes + 1)
                content_type = str(response.headers.get_content_type() or "application/octet-stream").lower()
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise RuntimeError("Jira rejected the configured credentials.") from exc
            if exc.code == 429:
                raise RuntimeError("Jira rate limit reached; wait before refreshing.") from exc
            raise RuntimeError(f"Jira returned HTTP {exc.code} for this attachment.") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError("Could not reach Jira.") from exc
        except TimeoutError as exc:
            raise RuntimeError("Jira attachment preview timed out.") from exc
        if len(raw) > max_bytes:
            raise ValueError("Jira attachment preview is too large to display inline.")
        return raw, content_type

    def search(self, jql: str, *, max_results: int = 50, next_page_token: str | None = None) -> dict[str, Any]:
        max_results = max(1, min(100, int(max_results)))
        payload = self._request(
            "/rest/api/3/search/jql",
            {
                "jql": jql.strip() or "assignee = currentUser() ORDER BY updated DESC",
                "maxResults": max_results,
                "fields": SEARCH_FIELDS,
                "nextPageToken": next_page_token,
            },
        )
        return normalise_search(payload)

    def issue(self, issue_key: str) -> dict[str, Any]:
        safe_key = urllib.parse.quote(issue_key.strip(), safe="-")
        if not safe_key:
            raise ValueError("issue_key is required")
        payload = self._request(f"/rest/api/3/issue/{safe_key}", {"fields": DETAIL_FIELDS})
        result = _normalise_issue(payload, detail=True)
        attachments = result.get("attachments") if isinstance(result.get("attachments"), list) else []
        comments: list[dict[str, Any]] = []
        start_at = 0
        total = 0
        while len(comments) < MAX_ISSUE_COMMENTS:
            page = self._request(
                f"/rest/api/3/issue/{safe_key}/comment",
                {"startAt": start_at, "maxResults": min(100, MAX_ISSUE_COMMENTS - len(comments))},
            )
            raw_comments = page.get("comments") if isinstance(page.get("comments"), list) else []
            total = max(total, int(page.get("total") or 0))
            comments.extend(
                _normalise_comment(comment, attachments)
                for comment in raw_comments
                if isinstance(comment, Mapping)
            )
            if not raw_comments or start_at + len(raw_comments) >= total:
                break
            start_at += len(raw_comments)
        result["comments"] = comments
        result["comments_truncated"] = total > len(comments)
        return result

    def attachments(self, issue_key: str) -> list[dict[str, Any]]:
        safe_key = urllib.parse.quote(issue_key.strip(), safe="-")
        if not safe_key:
            raise ValueError("issue_key is required")
        payload = self._request(f"/rest/api/3/issue/{safe_key}", {"fields": "attachment"})
        fields = payload.get("fields") if isinstance(payload.get("fields"), Mapping) else {}
        return [
            _normalise_attachment(attachment)
            for attachment in (fields.get("attachment") if isinstance(fields.get("attachment"), list) else [])
            if isinstance(attachment, Mapping) and attachment.get("id")
        ]

    def attachment_preview(self, issue_key: str, attachment_id: str) -> dict[str, Any]:
        requested_id = attachment_id.strip()
        if not requested_id:
            raise ValueError("attachment_id is required")
        attachment = next(
            (
                candidate
                for candidate in self.attachments(issue_key)
                if isinstance(candidate, Mapping) and candidate.get("id") == requested_id
            ),
            None,
        )
        if attachment is None:
            raise ValueError("Attachment does not belong to this Jira issue.")
        if not attachment.get("is_image"):
            raise ValueError("Only image attachments can be previewed inline.")
        safe_id = urllib.parse.quote(requested_id, safe="")
        raw, content_type = self._request_bytes(
            f"/rest/api/3/attachment/thumbnail/{safe_id}",
            {
                "redirect": "false",
                "fallbackToDefault": "false",
                "width": 1200,
                "height": 900,
            },
            max_bytes=MAX_ATTACHMENT_PREVIEW_BYTES,
        )
        if content_type not in SAFE_ATTACHMENT_PREVIEW_TYPES:
            raise RuntimeError("Jira did not return an image preview for this attachment.")
        return {
            "id": requested_id,
            "filename": attachment.get("filename"),
            "mime_type": content_type,
            "data_url": f"data:{content_type};base64,{base64.b64encode(raw).decode('ascii')}",
        }

    def add_comment(self, issue_key: str, text: str) -> dict[str, Any]:
        safe_key = urllib.parse.quote(issue_key.strip(), safe="-")
        comment = text.strip()
        if not safe_key:
            raise ValueError("issue_key is required")
        if not comment:
            raise ValueError("Comment text is required.")
        if len(comment) > 32_000:
            raise ValueError("Comment text is too long.")
        payload = self._request(
            f"/rest/api/3/issue/{safe_key}/comment",
            method="POST",
            body={
                "body": {
                    "type": "doc",
                    "version": 1,
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": comment}]}],
                }
            },
        )
        return _normalise_comment(payload)

    def transitions(self, issue_key: str) -> list[dict[str, str]]:
        safe_key = urllib.parse.quote(issue_key.strip(), safe="-")
        if not safe_key:
            raise ValueError("issue_key is required")
        payload = self._request(f"/rest/api/3/issue/{safe_key}/transitions")
        raw_transitions = payload.get("transitions") if isinstance(payload.get("transitions"), list) else []
        result: list[dict[str, str]] = []
        for transition in raw_transitions:
            if not isinstance(transition, Mapping):
                continue
            transition_id = str(transition.get("id") or "").strip()
            name = str(transition.get("name") or "").strip()
            target = transition.get("to") if isinstance(transition.get("to"), Mapping) else {}
            category = target.get("statusCategory") if isinstance(target.get("statusCategory"), Mapping) else {}
            if transition_id and name:
                result.append({
                    "id": transition_id,
                    "name": name,
                    "to": str(target.get("name") or name),
                    "category": str(category.get("key") or "new"),
                })
        return result

    def transition_issue(self, issue_key: str, transition_id: str) -> dict[str, str]:
        safe_key = urllib.parse.quote(issue_key.strip(), safe="-")
        transition = transition_id.strip()
        if not safe_key:
            raise ValueError("issue_key is required")
        if not transition or len(transition) > 100:
            raise ValueError("A valid transition id is required.")
        self._request(
            f"/rest/api/3/issue/{safe_key}/transitions",
            method="POST",
            body={"transition": {"id": transition}},
        )
        return {"transition_id": transition}


def branch_name(issue_key: str, summary: str = "") -> str:
    key = re.sub(r"[^A-Za-z0-9-]+", "-", issue_key.strip()).strip("-").upper()
    if not key:
        raise ValueError("A valid Jira issue key is required.")
    return f"jira/{key}"


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = noninteractive_git_env()
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    try:
        return subprocess.run(
            ["git", "-c", f"core.hooksPath={os.devnull}", *args],
            cwd=cwd,
            check=check,
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Git operation timed out.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "Git operation failed.").strip()
        raise RuntimeError(detail) from exc


def _repo_root(path: str | Path) -> Path:
    candidate = Path(path).expanduser().resolve()
    if not candidate.is_dir():
        raise ValueError(f"Project repository does not exist: {candidate}")
    result = _git(candidate, "rev-parse", "--show-toplevel")
    root = Path(result.stdout.strip()).resolve()
    if root != candidate:
        candidate = root
    return candidate


def _worktree_branch(path: Path) -> str:
    return _git(path, "branch", "--show-current").stdout.strip()


_WORKTREE_LOCKS_GUARD = threading.Lock()
_WORKTREE_LOCKS: dict[str, threading.Lock] = {}


def _worktree_lock(repo: Path, branch: str) -> threading.Lock:
    key = f"{repo}:{branch}"
    with _WORKTREE_LOCKS_GUARD:
        return _WORKTREE_LOCKS.setdefault(key, threading.Lock())


def _lock_git_worktree(repo: Path, worktree: Path, issue_key: str) -> None:
    result = _git(
        repo,
        "worktree",
        "lock",
        "--reason",
        f"Hermes Jira Browser {issue_key.strip().upper()}",
        str(worktree),
        check=False,
    )
    if result.returncode != 0 and "already locked" not in (result.stderr or "").lower():
        detail = (result.stderr or result.stdout or "Git could not lock the worktree.").strip()
        raise RuntimeError(detail)


def create_worktree(
    *,
    repo_path: str | Path,
    issue_key: str,
    summary: str,
    base_ref: str = "HEAD",
) -> dict[str, Any]:
    repo = _repo_root(repo_path)
    branch = branch_name(issue_key, summary)
    directory_name = branch.replace("/", "-")
    worktree = repo / ".worktrees" / directory_name
    requested_ref = base_ref.strip() or "HEAD"
    if requested_ref.startswith("-"):
        raise ValueError("base_ref must be a Git ref, not an option.")
    resolved_ref = _git(repo, "rev-parse", "--verify", f"{requested_ref}^{{commit}}", check=False)
    if resolved_ref.returncode != 0:
        raise ValueError(f"Git base ref does not resolve to a commit: {requested_ref}")
    base_commit = resolved_ref.stdout.strip()

    with _worktree_lock(repo, branch):
        _ensure_worktrees_gitignored(repo)
        worktree.parent.mkdir(parents=True, exist_ok=True)

        if worktree.exists():
            if not (worktree / ".git").exists():
                raise ValueError(f"Worktree destination already exists and is not a Git worktree: {worktree}")
            actual_branch = _worktree_branch(worktree)
            if actual_branch != branch:
                raise ValueError(f"Worktree destination is already on {actual_branch or 'a detached HEAD'}.")
            _lock_git_worktree(repo, worktree, issue_key)
            return {
                "created": False,
                "path": str(worktree),
                "branch": branch,
                "repo_path": str(repo),
                "base_ref": requested_ref,
            }

        branch_exists = _git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False).returncode == 0
        if branch_exists:
            result = _git(repo, "worktree", "add", str(worktree), branch, check=False)
        else:
            result = _git(repo, "worktree", "add", "-b", branch, str(worktree), base_commit, check=False)
        if result.returncode != 0:
            # A second gateway process may have won the race. Re-read the exact
            # target and return it only when it is the expected worktree.
            if worktree.exists() and (worktree / ".git").exists() and _worktree_branch(worktree) == branch:
                _lock_git_worktree(repo, worktree, issue_key)
                return {
                    "created": False,
                    "path": str(worktree),
                    "branch": branch,
                    "repo_path": str(repo),
                    "base_ref": requested_ref,
                }
            detail = (result.stderr or result.stdout or "Git could not create the worktree.").strip()
            raise ValueError(detail)
        if _worktree_branch(worktree) != branch:
            raise RuntimeError("Git created the worktree on an unexpected branch.")
        _lock_git_worktree(repo, worktree, issue_key)
        return {
            "created": True,
            "path": str(worktree),
            "branch": branch,
            "repo_path": str(repo),
            "base_ref": requested_ref,
        }


def cleanup_worktree(*, repo_path: str | Path, issue_key: str) -> dict[str, Any]:
    """Remove an untouched Jira worktree after a failed chat handoff."""
    repo = _repo_root(repo_path)
    branch = branch_name(issue_key)
    worktree = repo / ".worktrees" / branch.replace("/", "-")
    with _worktree_lock(repo, branch):
        if not worktree.exists():
            return {"removed": False, "path": str(worktree), "branch": branch}
        if not (worktree / ".git").exists() or _worktree_branch(worktree) != branch:
            raise ValueError("Refusing to clean up an unexpected worktree.")
        if _git(worktree, "status", "--porcelain").stdout.strip():
            raise ValueError("Refusing to remove a worktree with uncommitted changes.")
        _git(repo, "worktree", "unlock", str(worktree), check=False)
        _git(repo, "worktree", "remove", str(worktree))
        _git(repo, "branch", "-D", branch, check=False)
        return {"removed": True, "path": str(worktree), "branch": branch}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class JiraStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _migrate(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS project_mappings (
                    jira_project_key TEXT PRIMARY KEY,
                    hermes_project_id TEXT NOT NULL,
                    hermes_project_label TEXT NOT NULL,
                    repo_path TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS session_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    issue_id TEXT NOT NULL,
                    issue_key TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    project_id TEXT,
                    worktree_path TEXT,
                    branch TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(issue_id, session_id)
                );
                CREATE INDEX IF NOT EXISTS session_links_issue_idx ON session_links(issue_id, created_at DESC);
                """
            )

    def set_project_mapping(
        self,
        *,
        jira_project_key: str,
        hermes_project_id: str,
        hermes_project_label: str,
        repo_path: str,
    ) -> dict[str, Any]:
        key = jira_project_key.strip().upper()
        if not all((key, hermes_project_id.strip(), hermes_project_label.strip(), repo_path.strip())):
            raise ValueError("Jira project, Hermes project, and repository are required.")
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO project_mappings
                    (jira_project_key, hermes_project_id, hermes_project_label, repo_path, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(jira_project_key) DO UPDATE SET
                    hermes_project_id=excluded.hermes_project_id,
                    hermes_project_label=excluded.hermes_project_label,
                    repo_path=excluded.repo_path,
                    updated_at=excluded.updated_at
                """,
                (key, hermes_project_id.strip(), hermes_project_label.strip(), repo_path.strip(), _utc_now()),
            )
        return self.get_project_mapping(key) or {}

    def get_project_mapping(self, jira_project_key: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM project_mappings WHERE jira_project_key = ?",
                (jira_project_key.strip().upper(),),
            ).fetchone()
        return dict(row) if row else None

    def all_project_mappings(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM project_mappings ORDER BY jira_project_key").fetchall()
        return [dict(row) for row in rows]

    def link_session(
        self,
        *,
        issue_id: str,
        issue_key: str,
        session_id: str,
        project_id: str | None = None,
        worktree_path: str | None = None,
        branch: str | None = None,
    ) -> dict[str, Any]:
        if not all((issue_id.strip(), issue_key.strip(), session_id.strip())):
            raise ValueError("Issue id, issue key, and session id are required.")
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO session_links
                    (issue_id, issue_key, session_id, project_id, worktree_path, branch, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(issue_id, session_id) DO UPDATE SET
                    issue_key=excluded.issue_key,
                    project_id=excluded.project_id,
                    worktree_path=COALESCE(excluded.worktree_path, session_links.worktree_path),
                    branch=COALESCE(excluded.branch, session_links.branch)
                """,
                (
                    issue_id.strip(),
                    issue_key.strip().upper(),
                    session_id.strip(),
                    project_id.strip() if project_id else None,
                    worktree_path.strip() if worktree_path else None,
                    branch.strip() if branch else None,
                    _utc_now(),
                ),
            )
            row = db.execute(
                "SELECT * FROM session_links WHERE issue_id = ? AND session_id = ?",
                (issue_id.strip(), session_id.strip()),
            ).fetchone()
        return dict(row) if row else {}

    def links_for_issue(self, issue_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM session_links WHERE issue_id = ? ORDER BY created_at DESC",
                (issue_id.strip(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def unlink_session(self, *, issue_id: str, session_id: str) -> bool:
        issue = issue_id.strip()
        session = session_id.strip()
        if not issue or not session:
            raise ValueError("Issue id and session id are required.")
        with self._connect() as db:
            cursor = db.execute(
                "DELETE FROM session_links WHERE issue_id = ? AND session_id = ?",
                (issue, session),
            )
        return cursor.rowcount > 0


def enrich_session_links(links: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Attach live chat titles/availability without hiding archived sessions."""
    from hermes_state import SessionDB

    session_db = SessionDB(read_only=True)
    enriched: list[dict[str, Any]] = []
    try:
        for value in links:
            link = dict(value)
            try:
                session = session_db.get_session(str(link.get("session_id") or "").strip())
            except Exception:
                session = None
            link["chat_title"] = str(session.get("title") or "").strip() if session else None
            link["archived"] = bool(session.get("archived")) if session else None
            link["available"] = session is not None
            enriched.append(link)
    finally:
        session_db.close()
    return enriched


def list_linkable_sessions(repo_path: str | Path, *, batch_size: int = 500) -> list[dict[str, Any]]:
    """Return every user chat rooted in a mapped repository, including archived chats.

    SessionDB is paged rather than windowed so an old worktree chat cannot disappear
    merely because the profile has more than 500 sessions. Only renderer-safe metadata
    is returned; prompts, message bodies, and credentials never leave the backend.
    """
    from hermes_state import SessionDB

    root = Path(repo_path).expanduser().resolve()
    page_size = max(1, min(1_000, int(batch_size)))
    session_db = SessionDB(read_only=True)
    sessions: list[dict[str, Any]] = []
    offset = 0
    try:
        while True:
            rows = session_db.list_sessions_rich(
                limit=page_size,
                offset=offset,
                order_by_last_active=True,
                min_message_count=0,
                include_archived=True,
                include_children=False,
                compact_rows=True,
            )
            if not rows:
                break
            for row in rows:
                if str(row.get("source") or "").strip().lower() in {"cron", "kanban", "tool"}:
                    continue
                raw_path = row.get("git_repo_root") or row.get("cwd") or row.get("workspace_path")
                if not raw_path:
                    continue
                try:
                    session_path = Path(str(raw_path)).expanduser().resolve()
                except OSError:
                    continue
                if session_path != root and root not in session_path.parents:
                    continue
                sessions.append(
                    {
                        "id": str(row.get("id") or ""),
                        "title": str(row.get("title") or ""),
                        "preview": str(row.get("preview") or ""),
                        "cwd": str(row.get("cwd") or row.get("workspace_path") or ""),
                        "git_repo_root": str(row.get("git_repo_root") or raw_path),
                        "git_branch": str(row.get("git_branch") or ""),
                        "last_active": int(row.get("last_active") or row.get("started_at") or 0),
                        "archived": bool(row.get("archived")),
                    }
                )
            if len(rows) < page_size:
                break
            offset += len(rows)
    finally:
        session_db.close()
    return sessions


def validated_link_metadata(store: JiraStore, *, issue_key: str, session_id: str) -> dict[str, Any]:
    """Validate the session in the active profile and derive link metadata server-side."""
    from hermes_state import SessionDB

    session_db = SessionDB(read_only=True)
    try:
        session = session_db.get_session(session_id.strip())
    finally:
        session_db.close()
    if not session:
        raise ValueError("The Hermes session does not exist in the active profile.")

    jira_project_key = issue_key.strip().upper().rsplit("-", 1)[0]
    mapping = store.get_project_mapping(jira_project_key)
    project_id = mapping["hermes_project_id"] if mapping else None
    worktree_path = None
    branch = None
    if mapping:
        expected_branch = branch_name(issue_key)
        expected_path = Path(mapping["repo_path"]).resolve() / ".worktrees" / expected_branch.replace("/", "-")
        session_cwd = session.get("cwd") or session.get("workspace_path")
        if session_cwd:
            try:
                cwd = Path(str(session_cwd)).expanduser().resolve()
            except OSError:
                cwd = None
            if cwd == expected_path and expected_path.exists() and _worktree_branch(expected_path) == expected_branch:
                worktree_path = str(expected_path)
                branch = expected_branch
    return {
        "project_id": project_id,
        "worktree_path": worktree_path,
        "branch": branch,
    }


def default_store_path() -> Path:
    return get_hermes_home() / "jira-browser" / "state.sqlite3"
