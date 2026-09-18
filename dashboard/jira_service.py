"""Jira and Git services for the native Hermes Jira Browser plugin.

The module accepts a small Jira CLI-compatible config shape. Credentials never
leave this process.
"""

from __future__ import annotations

import base64
import errno
import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NamedTuple, cast

from hermes_cli._subprocess_compat import noninteractive_git_env
from hermes_cli.worktree_ops import _ensure_worktrees_gitignored
from hermes_constants import get_hermes_home, profile_name_for_home


SEARCH_FIELDS = (
    "summary,status,priority,issuetype,assignee,reporter,project,labels,"
    "created,updated,resolutiondate,description"
)
DETAIL_FIELDS = f"{SEARCH_FIELDS},parent,subtasks,fixVersions,components,attachment"
MAX_JIRA_JSON_BYTES = 16 * 1024 * 1024
MAX_JIRA_CONFIG_BYTES = 1 * 1024 * 1024
MAX_JIRA_COMMENT_ID_BYTES = 256
MAX_JIRA_COMMENT_BODY_BYTES = 64 * 1024
MAX_ATTACHMENT_PREVIEW_BYTES = 4 * 1024 * 1024
MAX_ISSUE_COMMENTS = 1_000
MAX_ADF_DEPTH = 64
MAX_ADF_NODES = 10_000
MAX_ADF_OUTPUT_CHARS = 100_000
MAX_ADF_REFERENCE_CHARS = 4_096
MAX_ADF_REFERENCE_COUNT = 1_000
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


def active_profile_name() -> str | None:
    """Return the profile owning the active backend home when it is discoverable."""
    return profile_name_for_home(get_hermes_home())


def validate_owner_field(value: Any, *, field_name: str) -> str:
    """Normalize bounded owner metadata without pretending connection IDs are identities."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string.")
    normalized = value.strip()
    if not normalized or len(normalized) > 200 or any(character.isspace() for character in normalized):
        raise ValueError(f"{field_name} must be 1-200 non-whitespace characters.")
    return normalized


def default_config_path() -> Path:
    """Return the Jira CLI-compatible config path, with an explicit service override."""
    override = os.environ.get("HERMES_JIRA_CONFIG_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / "jira-config" / "config.json"


def _validate_jira_config_metadata(metadata: os.stat_result) -> None:
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError("Jira config file must not be a symlink.")
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("Jira config file must be a regular file.")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValueError("Jira config file permissions must be owner-only.")
    if metadata.st_uid != os.getuid():
        raise ValueError("Jira config file must be owned by the current user.")


def _require_safe_jira_config_open() -> tuple[int, int]:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    supports_dir_fd = getattr(os, "supports_dir_fd", ())
    if (
        not isinstance(nofollow, int)
        or nofollow == 0
        or not isinstance(directory, int)
        or directory == 0
        or not callable(getattr(os, "getuid", None))
        or os.open not in supports_dir_fd
    ):
        raise ValueError(
            "File-based Jira credentials cannot be opened safely on this platform; "
            "set JIRA_BASE_URL, JIRA_EMAIL, and JIRA_API_TOKEN environment variables instead."
        )
    return nofollow, directory


def _read_jira_config_file(path: Path) -> dict[str, Any]:
    nofollow, directory = _require_safe_jira_config_open()
    components = list(path.parts)
    if any(component == ".." for component in components):
        raise ValueError(
            "Jira config path traversal is not allowed; set JIRA_BASE_URL, JIRA_EMAIL, and JIRA_API_TOKEN instead."
        )
    if path.is_absolute():
        root = os.open("/", os.O_RDONLY | directory | nofollow)
        components = components[1:]
    else:
        root = os.open(".", os.O_RDONLY | directory | nofollow)
    parent_descriptor = root
    try:
        if not components:
            raise ValueError("Jira config path must name a file.")
        for component in components[:-1]:
            if component == ".":
                continue
            try:
                next_descriptor = os.open(
                    component,
                    os.O_RDONLY | directory | nofollow | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=parent_descriptor,
                )
            except FileNotFoundError:
                return {}
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise ValueError("Jira config parent must not be a symlink.") from exc
                if exc.errno == errno.ENOTDIR:
                    try:
                        metadata = os.stat(component, dir_fd=parent_descriptor, follow_symlinks=False)
                    except OSError:
                        metadata = None
                    if metadata is not None and stat.S_ISLNK(metadata.st_mode):
                        raise ValueError("Jira config parent must not be a symlink.") from exc
                raise ValueError(f"Jira config at {path} cannot be inspected safely.") from exc
            os.close(parent_descriptor)
            parent_descriptor = next_descriptor

        filename = components[-1]
        if filename == ".":
            raise ValueError("Jira config path must name a file.")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | nofollow
        try:
            descriptor = os.open(filename, flags, dir_fd=parent_descriptor)
        except FileNotFoundError:
            return {}
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ValueError("Jira config file must not be a symlink.") from exc
            raise ValueError(f"Jira config at {path} cannot be inspected safely.") from exc
    finally:
        os.close(parent_descriptor)

    try:
        metadata = os.fstat(descriptor)
        _validate_jira_config_metadata(metadata)
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            raw_bytes = handle.read(MAX_JIRA_CONFIG_BYTES + 1)
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError(f"Jira config at {path} cannot be read safely.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    if len(raw_bytes) > MAX_JIRA_CONFIG_BYTES:
        raise ValueError("Jira config file is too large.")
    try:
        raw = json.loads(raw_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"Jira config at {path} is not valid JSON.") from exc
    if not isinstance(raw, Mapping):
        raise ValueError("Jira config must be a JSON object.")
    return dict(raw)


def _clean_jira_base_url(value: str) -> str:
    base_url = value.strip()
    if not base_url:
        return ""
    if "://" not in base_url:
        base_url = f"https://{base_url}"
    try:
        parsed = urllib.parse.urlsplit(base_url)
        hostname = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise ValueError("Jira baseUrl must be a clean https:// origin with a hostname.") from exc
    if (
        parsed.scheme.casefold() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or "@" in parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.path.strip("/")
    ):
        raise ValueError("Jira baseUrl must be a clean https:// origin with a hostname.")
    return f"https://{parsed.netloc}".rstrip("/")


def load_jira_config(path: str | Path | None = None) -> JiraConfig:
    config_path = Path(path).expanduser() if path is not None else default_config_path()
    env_base_url = os.environ.get("JIRA_BASE_URL") or os.environ.get("JIRA_SITE") or ""
    env_email = os.environ.get("JIRA_EMAIL") or ""
    env_api_token = os.environ.get("JIRA_API_TOKEN") or ""
    if all(value.strip() for value in (env_base_url, env_email, env_api_token)):
        return JiraConfig(
            base_url=_clean_jira_base_url(env_base_url),
            email=env_email.strip(),
            api_token=env_api_token.strip(),
        )

    raw = _read_jira_config_file(config_path)

    base_url = _clean_jira_base_url(
        str(os.environ.get("JIRA_BASE_URL") or os.environ.get("JIRA_SITE") or raw.get("baseUrl") or "")
    )
    email = str(os.environ.get("JIRA_EMAIL") or raw.get("email") or "").strip()
    api_token = str(os.environ.get("JIRA_API_TOKEN") or raw.get("token") or raw.get("apiToken") or "").strip()
    missing = [name for name, value in (("baseUrl", base_url), ("email", email), ("token", api_token)) if not value]
    if missing:
        raise ValueError(f"Jira config is missing: {', '.join(missing)}.")
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


def _append_adf_output(current: str, addition: str) -> str:
    remaining = MAX_ADF_OUTPUT_CHARS - len(current)
    if remaining <= 0:
        return current
    return current + addition[:remaining]


def _flatten_adf(
    node: Any,
    *,
    list_depth: int = 0,
    _depth: int = 0,
    _state: dict[str, int] | None = None,
) -> str:
    state = _state if _state is not None else {"nodes": 0}
    if _depth >= MAX_ADF_DEPTH or state["nodes"] >= MAX_ADF_NODES:
        return ""
    state["nodes"] += 1
    if isinstance(node, str):
        return node[:MAX_ADF_OUTPUT_CHARS]
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
        return (f"{text} ({link})" if link and link not in text else text)[:MAX_ADF_OUTPUT_CHARS]
    if node_type == "hardBreak":
        return "\n"

    children = node.get("content") if isinstance(node.get("content"), list) else []
    if node_type in {"bulletList", "orderedList"}:
        output = ""
        for index, child in enumerate(children, start=1):
            if state["nodes"] >= MAX_ADF_NODES:
                break
            value = _flatten_adf(child, list_depth=list_depth + 1, _depth=_depth + 1, _state=state).strip()
            marker = f"{index}." if node_type == "orderedList" else "•"
            if value:
                output = _append_adf_output(output, f"{'  ' * list_depth}{marker} {value}\n")
            if len(output) >= MAX_ADF_OUTPUT_CHARS:
                break
        return output.rstrip("\n")
    if node_type == "listItem":
        output = ""
        for child in children:
            if state["nodes"] >= MAX_ADF_NODES:
                break
            output = _append_adf_output(
                output,
                _flatten_adf(child, list_depth=list_depth, _depth=_depth + 1, _state=state).strip() + " ",
            )
            if len(output) >= MAX_ADF_OUTPUT_CHARS:
                break
        return output.strip()

    output = ""
    for child in children:
        if state["nodes"] >= MAX_ADF_NODES:
            break
        output = _append_adf_output(
            output,
            _flatten_adf(child, list_depth=list_depth, _depth=_depth + 1, _state=state),
        )
        if len(output) >= MAX_ADF_OUTPUT_CHARS:
            break
    if node_type in {"paragraph", "heading", "blockquote", "codeBlock", "panel"}:
        return (output.strip() + "\n\n")[:MAX_ADF_OUTPUT_CHARS]
    return output


def adf_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()[:MAX_ADF_OUTPUT_CHARS]
    text = _flatten_adf(value)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:MAX_ADF_OUTPUT_CHARS]


def _name(value: Any, key: str = "name") -> str | None:
    return str(value.get(key)) if isinstance(value, Mapping) and value.get(key) is not None else None


def _normalise_attachment(attachment: Mapping[str, Any]) -> dict[str, Any]:
    mime_type = str(attachment.get("mimeType") or "application/octet-stream").strip().lower()
    try:
        size = max(0, int(attachment.get("size") or 0))
    except (TypeError, ValueError):
        size = 0
    return {
        "id": str(attachment.get("id") or ""),
        "filename": str(attachment.get("filename") or "Attachment"),
        "mime_type": mime_type,
        "size": size,
        "created": attachment.get("created"),
        "author": _name(attachment.get("author"), "displayName"),
        "is_image": mime_type.startswith("image/"),
    }


def _adf_attachment_references(node: Any) -> tuple[set[str], set[str]]:
    ids: set[str] = set()
    filenames: set[str] = set()
    state = {"nodes": 0, "references": 0}

    def add_reference(target: set[str], value: str) -> None:
        if state["references"] >= MAX_ADF_REFERENCE_COUNT:
            return
        bounded = value[:MAX_ADF_REFERENCE_CHARS]
        if bounded:
            target.add(bounded)
            state["references"] += 1

    def visit(value: Any, depth: int) -> None:
        if depth >= MAX_ADF_DEPTH or state["nodes"] >= MAX_ADF_NODES:
            return
        state["nodes"] += 1
        if isinstance(value, list):
            for child in value:
                if state["nodes"] >= MAX_ADF_NODES:
                    break
                visit(child, depth + 1)
            return
        if not isinstance(value, Mapping):
            return
        node_type = str(value.get("type") or "")
        attrs_value = value.get("attrs")
        attrs: Mapping[str, Any] = cast(Mapping[str, Any], attrs_value) if isinstance(attrs_value, Mapping) else {}
        if node_type == "media":
            filename = str(attrs.get("alt") or "").strip().casefold()
            if filename:
                add_reference(filenames, filename)
        urls: list[str] = []
        url = attrs.get("url")
        if node_type in {"inlineCard", "blockCard"} and url:
            urls.append(str(url))
        if node_type == "text":
            for mark in value.get("marks") if isinstance(value.get("marks"), list) else []:
                if not isinstance(mark, Mapping):
                    continue
                mark_attrs = mark.get("attrs") if isinstance(mark.get("attrs"), Mapping) else {}
                href = mark_attrs.get("href")
                if mark.get("type") == "link" and href:
                    urls.append(str(href))
        for url in urls:
            match = re.search(r"/attachment/(?:content/)?(\d+)(?:/|$|[?#])", url)
            if match:
                add_reference(ids, match.group(1))
        for child in value.get("content") if isinstance(value.get("content"), list) else []:
            if state["nodes"] >= MAX_ADF_NODES:
                break
            visit(child, depth + 1)

    visit(node, 0)
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


def _adf_node_has_content(node: Any) -> bool:
    if isinstance(node, str):
        return bool(node.strip())
    if not isinstance(node, Mapping):
        return False
    node_type = str(node.get("type") or "").strip()
    if node_type == "text":
        return bool(str(node.get("text") or "").strip())
    if node_type in {"media", "inlineCard", "blockCard"}:
        attrs = node.get("attrs")
        return isinstance(attrs, Mapping) and any(str(value or "").strip() for value in attrs.values())
    children = node.get("content")
    return isinstance(children, list) and any(_adf_node_has_content(child) for child in children)


def _validate_comment_response(payload: Any) -> Mapping[str, Any]:
    invalid_response = "Jira returned an invalid comment response; the write outcome is unknown."
    try:
        if not isinstance(payload, Mapping):
            raise JiraAmbiguousError(invalid_response)
        comment_id = str(payload.get("id") or "").strip()
        comment_id_bytes = len(comment_id.encode("utf-8"))
        body = payload.get("body")
        if isinstance(body, str):
            valid_body = bool(body.strip())
        elif isinstance(body, Mapping):
            content = body.get("content")
            valid_body = (
                str(body.get("type") or "").strip() == "doc"
                and isinstance(content, list)
                and any(_adf_node_has_content(node) for node in content)
            )
        else:
            valid_body = False
        try:
            body_bytes = len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        except UnicodeEncodeError:
            raise
        except (TypeError, ValueError):
            body_bytes = MAX_JIRA_COMMENT_BODY_BYTES + 1
        if (
            not comment_id
            or comment_id_bytes > MAX_JIRA_COMMENT_ID_BYTES
            or not valid_body
            or body_bytes > MAX_JIRA_COMMENT_BODY_BYTES
        ):
            raise JiraAmbiguousError(invalid_response)
        return payload
    except UnicodeEncodeError as exc:
        raise JiraAmbiguousError(invalid_response) from exc


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


class JiraRequestError(RuntimeError):
    """A Jira request failure with an explicit send/outcome classification."""

    classification = "ambiguous"


class JiraPreRequestError(JiraRequestError):
    """The request was rejected locally before any bytes were sent."""

    classification = "pre_request"


class JiraDefinitiveRejectionError(JiraRequestError):
    """Jira definitively rejected the request with a client-error response."""

    classification = "definitive_rejection"


class JiraAmbiguousError(JiraRequestError):
    """The request may have been accepted but its outcome is unknown."""

    classification = "ambiguous"


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
        try:
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
        except Exception as exc:
            raise JiraPreRequestError("Jira request could not be constructed locally.") from exc
        try:
            opener = urllib.request.build_opener(NoJiraRedirects())
        except Exception as exc:
            raise JiraPreRequestError("Jira request could not be prepared locally.") from exc
        try:
            with opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_JIRA_JSON_BYTES + 1)
                if len(raw) > MAX_JIRA_JSON_BYTES:
                    raise JiraAmbiguousError("Jira returned a response that is too large.")
                payload = json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500:
                if exc.code in (401, 403):
                    message = "Jira rejected the configured credentials."
                elif exc.code == 429:
                    message = "Jira rate limit rejected the request; wait before retrying."
                else:
                    message = f"Jira rejected the request with HTTP {exc.code}."
                raise JiraDefinitiveRejectionError(message) from exc
            raise JiraAmbiguousError(f"Jira returned HTTP {exc.code}; the write outcome is unknown.") from exc
        except urllib.error.URLError as exc:
            raise JiraAmbiguousError("Could not determine whether Jira accepted the request.") from exc
        except (TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise JiraAmbiguousError("Jira returned an invalid or timed-out response; the write outcome is unknown.") from exc
        if not isinstance(payload, dict):
            raise JiraAmbiguousError("Jira returned an invalid response; the write outcome is unknown.")
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
            raw_comments = cast(list[Any], page.get("comments")) if isinstance(page.get("comments"), list) else []
            total = max(total, int(page.get("total") or 0))
            remaining = MAX_ISSUE_COMMENTS - len(comments)
            comments.extend(
                _normalise_comment(comment, attachments)
                for comment in raw_comments[:remaining]
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
        _validate_comment_response(payload)
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


def _validate_worktree_paths(repo: Path, worktree: Path) -> None:
    canonical_repo = repo.resolve(strict=True)
    worktrees = worktree.parent
    for candidate, label in ((worktrees, ".worktrees directory"), (worktree, "worktree destination")):
        try:
            metadata = os.lstat(candidate)
        except FileNotFoundError:
            metadata = None
        if metadata is not None and stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"Worktree {label} must not be a symlink.")
        if metadata is not None and not stat.S_ISDIR(metadata.st_mode):
            raise ValueError(f"Worktree {label} must be a directory.")
        canonical_target = candidate.resolve(strict=False)
        try:
            canonical_target.relative_to(canonical_repo)
        except ValueError as exc:
            raise ValueError(f"Worktree {label} must remain under the project repository.") from exc


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
    _validate_worktree_paths(repo, worktree)
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
        _validate_worktree_paths(repo, worktree)

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
        _validate_worktree_paths(repo, worktree)
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
            "created": not branch_exists,
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
    _validate_worktree_paths(repo, worktree)
    with _worktree_lock(repo, branch):
        _validate_worktree_paths(repo, worktree)
        if not worktree.exists():
            return {"removed": False, "path": str(worktree), "branch": branch}
        if not (worktree / ".git").exists() or _worktree_branch(worktree) != branch:
            raise ValueError("Refusing to clean up an unexpected worktree.")
        if _git(worktree, "status", "--porcelain").stdout.strip():
            raise ValueError("Refusing to remove a worktree with uncommitted changes.")
        _git(repo, "worktree", "unlock", str(worktree), check=False)
        _git(repo, "worktree", "remove", str(worktree))
        return {"removed": True, "path": str(worktree), "branch": branch}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


MAX_MUTATION_KEY_LENGTH = 200
MAX_MUTATION_RESULT_BYTES = 64 * 1024
MAX_COMPLETED_MUTATION_RECEIPTS = 1_000
MAX_PENDING_MUTATION_RECEIPTS = 1_000
MUTATION_RECEIPT_RETENTION_DAYS = 30
_MUTATION_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,199}$")


class MutationPendingError(RuntimeError):
    """An identical mutation is already in flight or unresolved."""


class MutationConflictError(ValueError):
    """An idempotency key was reused for a different mutation."""


def validate_mutation_key(value: str) -> str:
    key = str(value or "").strip()
    if not (16 <= len(key) <= MAX_MUTATION_KEY_LENGTH) or not _MUTATION_KEY_PATTERN.fullmatch(key):
        raise ValueError("idempotency_key must be 16-200 safe characters.")
    return key


def _mutation_payload_hash(payload: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Mutation payload is not JSON serializable.") from exc
    return hashlib.sha256(encoded).hexdigest()


class JiraStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().absolute()
        self._harden_storage()
        self._migrate()

    def _harden_storage(self) -> None:
        parent = self.path.parent
        current = Path(parent.anchor)
        for part in parent.parts[1:]:
            candidate = current / part
            try:
                metadata = os.lstat(candidate)
            except FileNotFoundError:
                try:
                    os.mkdir(candidate, 0o700)
                except FileExistsError:
                    pass
                try:
                    metadata = os.lstat(candidate)
                except OSError as exc:
                    raise ValueError("Jira store parent cannot be inspected safely.") from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise ValueError("Jira store parent must not contain symlinks.")
            if not stat.S_ISDIR(metadata.st_mode):
                raise ValueError("Jira store parent must be a directory.")
            current = candidate

        if parent != Path(parent.anchor):
            try:
                os.chmod(parent, 0o700, follow_symlinks=False)
                parent_metadata = os.lstat(parent)
            except OSError as exc:
                raise ValueError("Jira store parent cannot be secured.") from exc
            if stat.S_IMODE(parent_metadata.st_mode) != 0o700:
                raise ValueError("Jira store parent must be owner-only.")

        database_metadata: os.stat_result | None = None
        try:
            database_metadata = os.lstat(self.path)
        except FileNotFoundError:
            flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(self.path, flags, 0o600)
            except FileExistsError:
                descriptor = -1
            else:
                try:
                    database_metadata = os.fstat(descriptor)
                    if stat.S_ISLNK(database_metadata.st_mode) or not stat.S_ISREG(database_metadata.st_mode):
                        raise ValueError("Jira store database must be a regular file.")
                    os.fchmod(descriptor, 0o600)
                finally:
                    os.close(descriptor)
            if descriptor < 0:
                try:
                    database_metadata = os.lstat(self.path)
                except OSError as exc:
                    raise ValueError("Jira store database cannot be inspected safely.") from exc

        if database_metadata is None:
            raise ValueError("Jira store database cannot be inspected safely.")
        if stat.S_ISLNK(database_metadata.st_mode):
            raise ValueError("Jira store database must not be a symlink.")
        if not stat.S_ISREG(database_metadata.st_mode):
            raise ValueError("Jira store database must be a regular file.")
        try:
            os.chmod(self.path, 0o600, follow_symlinks=False)
            database_metadata = os.lstat(self.path)
        except OSError as exc:
            raise ValueError("Jira store database cannot be secured.") from exc
        if stat.S_IMODE(database_metadata.st_mode) != 0o600:
            raise ValueError("Jira store database must be owner-only.")

    def _connect(self) -> sqlite3.Connection:
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if not isinstance(nofollow, int) or nofollow == 0:
            raise ValueError("Jira store cannot be opened safely on this platform.")
        self._harden_storage()
        flags = os.O_RDWR | nofollow | getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor = os.open(self.path, flags)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ValueError("Jira store database must not be a symlink.") from exc
            raise ValueError("Jira store database cannot be opened safely.") from exc

        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("Jira store database must be a regular file.")
            if stat.S_IMODE(before.st_mode) != 0o600:
                os.fchmod(descriptor, 0o600)
                before = os.fstat(descriptor)
            if stat.S_IMODE(before.st_mode) != 0o600:
                raise ValueError("Jira store database must be owner-only.")
            uri = f"file:{urllib.parse.quote(str(self.path), safe='/')}?nofollow=1"
            try:
                connection = sqlite3.connect(uri, uri=True)
            except sqlite3.Error as exc:
                raise ValueError("Jira store database cannot be opened safely.") from exc
            try:
                after = os.stat(self.path, follow_symlinks=False)
                if (
                    not stat.S_ISREG(after.st_mode)
                    or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
                ):
                    raise ValueError("Jira store database changed while it was being opened.")
            except BaseException:
                connection.close()
                raise
        finally:
            os.close(descriptor)

        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _migrate(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS project_mappings (
                    jira_project_key TEXT PRIMARY KEY,
                    hermes_project_id TEXT NOT NULL,
                    hermes_project_label TEXT NOT NULL,
                    repo_path TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {
                str(row[1])
                for row in db.execute("PRAGMA table_info(session_links)").fetchall()
            }
            required = {
                "jira_origin",
                "connection_id",
                "profile_name",
                "target_profile",
                "detached",
            }
            table_row = db.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'session_links'"
            ).fetchone()
            table_sql = "".join(str(table_row[0] or "").lower().split()) if table_row else ""
            target_qualified_identity = (
                "unique(jira_origin,issue_id,connection_id,profile_name,target_profile,session_id)"
            )
            needs_rebuild = bool(columns) and (
                not required.issubset(columns) or target_qualified_identity not in table_sql
            )
            if needs_rebuild:
                db.execute("ALTER TABLE session_links RENAME TO session_links_legacy")
                self._create_session_links_table(db)
                if required.issubset(columns):
                    db.execute(
                        """
                        INSERT INTO session_links
                            (issue_id, issue_key, jira_origin, connection_id, profile_name,
                             target_profile, session_id, project_id, worktree_path, branch,
                             detached, created_at)
                        SELECT issue_id, issue_key, jira_origin, connection_id, profile_name,
                               target_profile, session_id, project_id, worktree_path, branch,
                               detached, created_at
                        FROM session_links_legacy
                        """
                    )
                else:
                    db.execute(
                        """
                        INSERT INTO session_links
                            (issue_id, issue_key, jira_origin, connection_id, profile_name,
                             target_profile, session_id, project_id, worktree_path, branch,
                             detached, created_at)
                        SELECT issue_id, issue_key, '', 'local', 'default', 'default',
                               session_id, project_id, worktree_path, branch, 0, created_at
                        FROM session_links_legacy
                        """
                    )
                db.execute("DROP TABLE session_links_legacy")
            elif not columns:
                self._create_session_links_table(db)
            db.execute(
                "CREATE INDEX IF NOT EXISTS session_links_issue_idx "
                "ON session_links(jira_origin, issue_id, detached, created_at DESC)"
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS mutation_receipts (
                    idempotency_key TEXT PRIMARY KEY CHECK(length(idempotency_key) BETWEEN 16 AND 200),
                    action TEXT NOT NULL,
                    issue_key TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    claimed INTEGER NOT NULL CHECK (claimed IN (0, 1)),
                    completed INTEGER NOT NULL CHECK (completed IN (0, 1)),
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    claimed_at TEXT NOT NULL,
                    completed_at TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS mutation_receipts_pending_idx "
                "ON mutation_receipts(completed, updated_at)"
            )
            self._prune_mutation_receipts_db(
                db,
                max_completed=MAX_COMPLETED_MUTATION_RECEIPTS,
                retention_days=MUTATION_RECEIPT_RETENTION_DAYS,
            )

    def claim_legacy_origin(self, configured_origin: str) -> int:
        """Claim only unqualified legacy rows; later config changes cannot re-home them."""
        origin = configured_origin.strip().rstrip("/")
        if not origin:
            raise ValueError("Jira origin is required to claim legacy links.")
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE session_links SET jira_origin = ? WHERE jira_origin = ''",
                (origin,),
            )
        return cursor.rowcount

    @staticmethod
    def _create_session_links_table(db: sqlite3.Connection) -> None:
        db.execute(
            """
                CREATE TABLE IF NOT EXISTS session_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    issue_id TEXT NOT NULL,
                    issue_key TEXT NOT NULL,
                    jira_origin TEXT NOT NULL DEFAULT '',
                    connection_id TEXT NOT NULL DEFAULT 'local',
                    profile_name TEXT NOT NULL DEFAULT 'default',
                    target_profile TEXT NOT NULL DEFAULT 'default',
                    session_id TEXT NOT NULL,
                    project_id TEXT,
                    worktree_path TEXT,
                    branch TEXT,
                    detached INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    UNIQUE(jira_origin, issue_id, connection_id, profile_name, target_profile, session_id)
                );
                CREATE INDEX IF NOT EXISTS session_links_issue_idx
                    ON session_links(jira_origin, issue_id, detached, created_at DESC);
                """
            )

    def reserve_mutation(
        self,
        *,
        idempotency_key: str,
        action: str,
        issue_key: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        key = validate_mutation_key(idempotency_key)
        mutation = str(action or "").strip()
        issue = str(issue_key or "").strip().upper()
        if mutation not in {"comment", "transition"}:
            raise ValueError("Unsupported Jira mutation.")
        if not issue:
            raise ValueError("issue_key is required.")
        payload_sha256 = _mutation_payload_hash(payload)
        now = _utc_now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._prune_mutation_receipts_db(
                db,
                max_completed=MAX_COMPLETED_MUTATION_RECEIPTS,
                retention_days=MUTATION_RECEIPT_RETENTION_DAYS,
            )
            row = db.execute(
                "SELECT * FROM mutation_receipts WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            if row is not None:
                if (
                    row["action"] != mutation
                    or row["issue_key"] != issue
                    or row["payload_sha256"] != payload_sha256
                ):
                    raise MutationConflictError("Idempotency key was already used for a different mutation.")
                if row["completed"]:
                    try:
                        result = json.loads(row["result_json"] or "null")
                    except json.JSONDecodeError as exc:
                        raise RuntimeError("Stored Jira mutation result is invalid.") from exc
                    return {"status": "completed", "result": result}
                raise MutationPendingError("An identical Jira mutation is already pending.")
            pending_count = int(db.execute(
                "SELECT COUNT(*) FROM mutation_receipts WHERE completed = 0"
            ).fetchone()[0])
            if pending_count >= MAX_PENDING_MUTATION_RECEIPTS:
                raise MutationPendingError("Too many unresolved Jira mutations are pending.")
            db.execute(
                """
                INSERT INTO mutation_receipts
                    (idempotency_key, action, issue_key, payload_sha256, claimed, completed,
                     result_json, created_at, claimed_at, completed_at, updated_at)
                VALUES (?, ?, ?, ?, 1, 0, NULL, ?, ?, NULL, ?)
                """,
                (key, mutation, issue, payload_sha256, now, now, now),
            )
        return {"status": "claimed"}

    @staticmethod
    def _prune_mutation_receipts_db(
        db: sqlite3.Connection,
        *,
        max_completed: int,
        retention_days: int,
    ) -> int:
        limit = max(0, int(max_completed))
        days = max(0, int(retention_days))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z")
        before = db.total_changes
        db.execute(
            "DELETE FROM mutation_receipts WHERE completed = 1 AND completed_at < ?",
            (cutoff,),
        )
        if limit == 0:
            db.execute("DELETE FROM mutation_receipts WHERE completed = 1")
        else:
            db.execute(
                """
                DELETE FROM mutation_receipts
                WHERE completed = 1 AND idempotency_key NOT IN (
                    SELECT idempotency_key FROM mutation_receipts
                    WHERE completed = 1
                    ORDER BY completed_at DESC, idempotency_key DESC
                    LIMIT ?
                )
                """,
                (limit,),
            )
        return db.total_changes - before

    def prune_mutation_receipts(
        self,
        *,
        max_completed: int = MAX_COMPLETED_MUTATION_RECEIPTS,
        retention_days: int = MUTATION_RECEIPT_RETENTION_DAYS,
    ) -> int:
        with self._connect() as db:
            return self._prune_mutation_receipts_db(
                db,
                max_completed=max_completed,
                retention_days=retention_days,
            )

    def complete_mutation(self, idempotency_key: str, result: Mapping[str, Any]) -> None:
        key = validate_mutation_key(idempotency_key)
        try:
            result_json = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("Jira mutation result is not JSON serializable.") from exc
        if len(result_json.encode("utf-8")) > MAX_MUTATION_RESULT_BYTES:
            raise ValueError("Jira mutation result is too large to store.")
        now = _utc_now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                """
                UPDATE mutation_receipts
                SET completed = 1, result_json = ?, completed_at = ?, updated_at = ?
                WHERE idempotency_key = ? AND claimed = 1 AND completed = 0
                """,
                (result_json, now, now, key),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Jira mutation receipt is no longer pending.")
            self._prune_mutation_receipts_db(
                db,
                max_completed=MAX_COMPLETED_MUTATION_RECEIPTS,
                retention_days=MUTATION_RECEIPT_RETENTION_DAYS,
            )

    def release_mutation(self, idempotency_key: str) -> None:
        key = validate_mutation_key(idempotency_key)
        with self._connect() as db:
            db.execute(
                "DELETE FROM mutation_receipts WHERE idempotency_key = ? AND claimed = 1 AND completed = 0",
                (key,),
            )

    def get_mutation_receipt(self, idempotency_key: str) -> dict[str, Any] | None:
        key = validate_mutation_key(idempotency_key)
        with self._connect() as db:
            self._prune_mutation_receipts_db(
                db,
                max_completed=MAX_COMPLETED_MUTATION_RECEIPTS,
                retention_days=MUTATION_RECEIPT_RETENTION_DAYS,
            )
            row = db.execute(
                "SELECT * FROM mutation_receipts WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
        return dict(row) if row else None

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
        jira_origin: str = "",
        connection_id: str = "local",
        profile_name: str = "default",
        target_profile: str | None = None,
        project_id: str | None = None,
        worktree_path: str | None = None,
        branch: str | None = None,
        clear_detachment: bool = False,
    ) -> dict[str, Any]:
        if not all((issue_id.strip(), issue_key.strip(), session_id.strip())):
            raise ValueError("Issue id, issue key, and session id are required.")
        origin = jira_origin.strip().rstrip("/")
        connection = connection_id.strip() or "local"
        profile = profile_name.strip() or "default"
        target = (target_profile or profile).strip() or profile
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO session_links
                    (issue_id, issue_key, jira_origin, connection_id, profile_name,
                     target_profile, session_id, project_id, worktree_path, branch,
                     detached, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(jira_origin, issue_id, connection_id, profile_name, target_profile, session_id) DO UPDATE SET
                    issue_key=excluded.issue_key,
                    target_profile=excluded.target_profile,
                    project_id=excluded.project_id,
                    worktree_path=COALESCE(excluded.worktree_path, session_links.worktree_path),
                    branch=COALESCE(excluded.branch, session_links.branch),
                    detached=CASE WHEN ? THEN 0 ELSE session_links.detached END
                """,
                (
                    issue_id.strip(),
                    issue_key.strip().upper(),
                    origin,
                    connection,
                    profile,
                    target,
                    session_id.strip(),
                    project_id.strip() if project_id else None,
                    worktree_path.strip() if worktree_path else None,
                    branch.strip() if branch else None,
                    _utc_now(),
                    1 if clear_detachment else 0,
                ),
            )
            row = db.execute(
                """
                SELECT * FROM session_links
                WHERE jira_origin = ? AND issue_id = ? AND connection_id = ?
                  AND profile_name = ? AND target_profile = ? AND session_id = ?
                """,
                (origin, issue_id.strip(), connection, profile, target, session_id.strip()),
            ).fetchone()
        return dict(row) if row else {}

    def links_for_issue(self, issue_id: str, *, jira_origin: str | None = None) -> list[dict[str, Any]]:
        origin = jira_origin.strip().rstrip("/") if jira_origin is not None else ""
        if not origin:
            raise ValueError("Jira origin is required for link reads.")
        clauses = ["issue_id = ?", "detached = 0"]
        params: list[Any] = [issue_id.strip()]
        clauses.append("jira_origin = ?")
        params.append(origin)
        with self._connect() as db:
            rows = db.execute(
                f"SELECT * FROM session_links WHERE {' AND '.join(clauses)} ORDER BY created_at DESC",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def detached_session_ids(
        self,
        issue_id: str,
        *,
        jira_origin: str = "",
        connection_id: str = "local",
        profile_name: str = "default",
        target_profile: str | None = None,
    ) -> set[str]:
        origin = jira_origin.strip().rstrip("/")
        if not origin:
            raise ValueError("Jira origin is required for link reads.")
        profile = profile_name.strip() or "default"
        target = (target_profile or profile).strip() or profile
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT session_id FROM session_links
                WHERE issue_id = ? AND jira_origin = ? AND connection_id = ?
                  AND profile_name = ? AND target_profile = ? AND detached = 1
                """,
                (
                    issue_id.strip(),
                    origin,
                    connection_id.strip() or "local",
                    profile,
                    target,
                ),
            ).fetchall()
        return {str(row["session_id"]) for row in rows}

    def detached_links_for_issue(
        self,
        issue_id: str,
        *,
        jira_origin: str | None = None,
    ) -> list[dict[str, Any]]:
        origin = jira_origin.strip().rstrip("/") if jira_origin is not None else ""
        if not origin:
            raise ValueError("Jira origin is required for link reads.")
        clauses = ["issue_id = ?", "detached = 1"]
        params: list[Any] = [issue_id.strip()]
        clauses.append("jira_origin = ?")
        params.append(origin)
        with self._connect() as db:
            rows = db.execute(
                f"SELECT * FROM session_links WHERE {' AND '.join(clauses)} ORDER BY created_at DESC",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def unlink_session(
        self,
        *,
        issue_id: str,
        session_id: str,
        jira_origin: str = "",
        connection_id: str = "local",
        profile_name: str = "default",
        target_profile: str | None = None,
    ) -> bool:
        issue = issue_id.strip()
        session = session_id.strip()
        profile = profile_name.strip() or "default"
        target = (target_profile or profile).strip() or profile
        if not issue or not session:
            raise ValueError("Issue id and session id are required.")
        with self._connect() as db:
            cursor = db.execute(
                """
                UPDATE session_links SET detached = 1
                WHERE issue_id = ? AND session_id = ? AND jira_origin = ?
                  AND connection_id = ? AND profile_name = ? AND target_profile = ? AND detached = 0
                """,
                (
                    issue,
                    session,
                    jira_origin.strip().rstrip("/"),
                    connection_id.strip() or "local",
                    profile,
                    target,
                ),
            )
        return cursor.rowcount > 0


def enrich_session_links(links: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Attach live chat titles/availability without hiding archived sessions."""
    active_profile = active_profile_name()
    session_db = None
    enriched: list[dict[str, Any]] = []
    try:
        for value in links:
            link = dict(value)
            connection_id = str(link.get("connection_id") or "").strip()
            profile_name = str(link.get("profile_name") or "").strip()
            target_profile = str(link.get("target_profile") or "").strip()
            session_id = str(link.get("session_id") or "").strip()
            if not connection_id or not profile_name or not target_profile or not session_id:
                link["chat_title"] = None
                link["archived"] = None
                link["available"] = False
                enriched.append(link)
                continue
            if (
                active_profile is None
                or connection_id != "local"
                or profile_name != active_profile
                or target_profile != active_profile
            ):
                link["chat_title"] = None
                link["archived"] = None
                link["available"] = None
                enriched.append(link)
                continue
            if session_db is None:
                from hermes_state import SessionDB

                session_db = SessionDB(read_only=True)
            try:
                session = session_db.get_session(session_id)
            except Exception:
                session = None
            link["chat_title"] = str(session.get("title") or "").strip() if session else None
            link["archived"] = bool(session.get("archived")) if session else None
            link["available"] = session is not None
            enriched.append(link)
    finally:
        if session_db is not None:
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
