"""ConfluenceExecutor — the `confluence` node type.

Fully deterministic (no LLM) — operation selector: create_or_update_page,
get_page, get_property, set_property. Uses Confluence Cloud REST API v1
(`/wiki/rest/api/content`), Basic auth (email + API token), same
encrypted-per-node credential pattern as the `jira` node — but the generic
encrypt_marked/decrypt_marked helpers (src/security/encryption.py) rather
than Jira's older bespoke ones, matching how vector-db's secret fields are
handled.
"""

import base64
from typing import Any

import httpx

from src.engine.state import WorkflowStateDict
from src.engine.workflow import ConfluenceNode
from src.executors.base import register_executor
from src.security.encryption import decrypt_marked
from src.variable_substitution import substitute, substitute_in_value


class ConfluenceConfigError(RuntimeError):
    """Raised when required fields for the node's operation are missing."""


class ConfluenceHttpError(RuntimeError):
    """Raised when a Confluence REST call fails unexpectedly (non-2xx, not
    a recognized 'not found' case)."""


def _headers(email: str, api_token: str) -> dict[str, str]:
    encoded = base64.b64encode(f"{email}:{api_token}".encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _content_url(domain: str, path: str = "") -> str:
    base = f"https://{domain}/wiki/rest/api/content"
    return f"{base}/{path}" if path else base


def _raise_for_unexpected_status(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        raise ConfluenceHttpError(
            f"Confluence API call failed (HTTP {resp.status_code}): {resp.text[:500]}"
        )


@register_executor("confluence")
class ConfluenceExecutor:
    def __init__(self, node: ConfluenceNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        domain = self.node.data.domain or ""
        email = self.node.data.email or ""
        api_token = decrypt_marked(self.node.data.api_token or "")
        if not all([domain, email, api_token]):
            raise ConfluenceConfigError(
                f"confluence node {self.node.id!r}: domain, email, and apiToken are all required."
            )
        headers = _headers(email, api_token)
        operation = self.node.data.operation

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
            if operation == "create_or_update_page":
                output = await self._create_or_update_page(client, domain, headers, state)
            elif operation == "get_page":
                output = await self._get_page(client, domain, headers, state)
            elif operation == "get_property":
                output = await self._get_property(client, domain, headers, state)
            else:
                output = await self._set_property(client, domain, headers, state)

        return {
            "variables": {"lastOutput": output},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {"operation": operation},
                    "output": output,
                }
            },
        }

    async def _find_page(
        self,
        client: httpx.AsyncClient,
        domain: str,
        headers: dict[str, str],
        space_key: str,
        title: str,
    ) -> dict[str, Any] | None:
        resp = await client.get(
            _content_url(domain),
            headers=headers,
            params={"spaceKey": space_key, "title": title, "expand": "body.storage,version"},
        )
        _raise_for_unexpected_status(resp)
        results = resp.json().get("results", [])
        return results[0] if results else None

    async def _create_or_update_page(
        self,
        client: httpx.AsyncClient,
        domain: str,
        headers: dict[str, str],
        state: WorkflowStateDict,
    ) -> dict[str, Any]:
        data = self.node.data
        space_key = substitute(data.space_key or "", state)
        title = substitute(data.title or "", state)
        body_html = substitute(data.body_storage_html or "", state)
        parent_page_id = substitute(data.parent_page_id, state) if data.parent_page_id else None
        labels = [substitute(label, state) for label in (data.labels or [])]
        if not all([space_key, title]):
            raise ConfluenceConfigError(
                f"confluence node {self.node.id!r}: create_or_update_page requires "
                "spaceKey and title."
            )

        existing = await self._find_page(client, domain, headers, space_key, title)
        base_body: dict[str, Any] = {
            "type": "page",
            "title": title,
            "space": {"key": space_key},
            "body": {"storage": {"value": body_html, "representation": "storage"}},
        }
        if existing is None:
            body = {
                **base_body,
                **({"ancestors": [{"id": parent_page_id}]} if parent_page_id else {}),
            }
            resp = await client.post(_content_url(domain), headers=headers, json=body)
            was_created = True
        else:
            page_id = existing["id"]
            body = {
                **base_body,
                "id": page_id,
                "version": {"number": existing["version"]["number"] + 1},
            }
            resp = await client.put(_content_url(domain, page_id), headers=headers, json=body)
            was_created = False
        _raise_for_unexpected_status(resp)
        result = resp.json()
        page_id, version = result["id"], result["version"]["number"]

        # A newly created page cannot already have labels — skip the
        # avoidable GET round-trip and go straight to adding the desired
        # set. The update branch doesn't know the current set, so it
        # still fetches before diffing.
        await self._reconcile_labels(
            client, domain, headers, page_id, labels, known_current=set() if was_created else None
        )

        return {
            "pageId": page_id,
            "version": version,
            "url": f"https://{domain}/wiki/spaces/{space_key}/pages/{page_id}",
            "created": was_created,
        }

    async def _reconcile_labels(
        self,
        client: httpx.AsyncClient,
        domain: str,
        headers: dict[str, str],
        page_id: str,
        labels: list[str],
        known_current: set[str] | None = None,
    ) -> None:
        if known_current is not None:
            current = known_current
        else:
            resp = await client.get(_content_url(domain, f"{page_id}/label"), headers=headers)
            _raise_for_unexpected_status(resp)
            current = {entry["name"] for entry in resp.json().get("results", [])}
        desired = set(labels)

        for name in current - desired:
            del_resp = await client.delete(
                _content_url(domain, f"{page_id}/label/{name}"), headers=headers
            )
            _raise_for_unexpected_status(del_resp)

        to_add = desired - current
        if to_add:
            add_resp = await client.post(
                _content_url(domain, f"{page_id}/label"),
                headers=headers,
                json=[{"prefix": "global", "name": name} for name in to_add],
            )
            _raise_for_unexpected_status(add_resp)

    async def _get_page(
        self,
        client: httpx.AsyncClient,
        domain: str,
        headers: dict[str, str],
        state: WorkflowStateDict,
    ) -> dict[str, Any]:
        data = self.node.data
        space_key = substitute(data.space_key or "", state)
        title = substitute(data.title or "", state)
        if not all([space_key, title]):
            raise ConfluenceConfigError(
                f"confluence node {self.node.id!r}: get_page requires spaceKey and title."
            )
        page = await self._find_page(client, domain, headers, space_key, title)
        if page is None:
            return {"found": False, "pageId": None, "bodyStorageHtml": None, "version": None}
        return {
            "found": True,
            "pageId": page["id"],
            "bodyStorageHtml": page.get("body", {}).get("storage", {}).get("value", ""),
            "version": page["version"]["number"],
        }

    async def _get_property(
        self,
        client: httpx.AsyncClient,
        domain: str,
        headers: dict[str, str],
        state: WorkflowStateDict,
    ) -> dict[str, Any]:
        data = self.node.data
        page_id = substitute(data.page_id or "", state)
        property_key = substitute(data.property_key or "", state)
        if not all([page_id, property_key]):
            raise ConfluenceConfigError(
                f"confluence node {self.node.id!r}: get_property requires pageId and propertyKey."
            )
        resp = await client.get(
            _content_url(domain, f"{page_id}/property/{property_key}"), headers=headers
        )
        if resp.status_code == 404:
            return {"found": False, "value": None}
        _raise_for_unexpected_status(resp)
        return {"found": True, "value": resp.json().get("value")}

    async def _set_property(
        self,
        client: httpx.AsyncClient,
        domain: str,
        headers: dict[str, str],
        state: WorkflowStateDict,
    ) -> dict[str, Any]:
        data = self.node.data
        page_id = substitute(data.page_id or "", state)
        property_key = substitute(data.property_key or "", state)
        if not all([page_id, property_key]):
            raise ConfluenceConfigError(
                f"confluence node {self.node.id!r}: set_property requires pageId and propertyKey."
            )
        value = substitute_in_value(data.property_value, state)

        existing = await client.get(
            _content_url(domain, f"{page_id}/property/{property_key}"), headers=headers
        )
        if existing.status_code == 404:
            resp = await client.post(
                _content_url(domain, f"{page_id}/property"),
                headers=headers,
                json={"key": property_key, "value": value},
            )
        else:
            _raise_for_unexpected_status(existing)
            next_version = existing.json()["version"]["number"] + 1
            resp = await client.put(
                _content_url(domain, f"{page_id}/property/{property_key}"),
                headers=headers,
                json={"key": property_key, "value": value, "version": {"number": next_version}},
            )
        _raise_for_unexpected_status(resp)
        result = resp.json()
        return {"key": property_key, "updatedAt": result.get("version", {}).get("when")}


__all__ = [
    "ConfluenceConfigError",
    "ConfluenceExecutor",
    "ConfluenceHttpError",
]
