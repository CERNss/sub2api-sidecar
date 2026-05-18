from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote, urlencode, urlparse, urlunparse

import requests

from app.models.notification import (
    NotificationDeliveryRecord,
    NotificationDeliveryStatus,
    NotificationDeliveryTrigger,
    NotificationMessage,
    NotificationSeverity,
    NotificationWebhook,
    WebhookMethod,
    WebhookProvider,
)
from app.stores.sqlite import SQLiteFlowStore

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (1.0, 2.0, 4.0)
RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
URL_TEMPLATE_VARIABLE_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
CARD_TEMPLATE_VARIABLE_RE = re.compile(
    r"\$\{([A-Za-z_][A-Za-z0-9_.-]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)"
)
FULL_CARD_TEMPLATE_VARIABLE_RE = re.compile(
    r"^\s*(?:\$\{([A-Za-z_][A-Za-z0-9_.-]*)\}|\$([A-Za-z_][A-Za-z0-9_]*))\s*$"
)


@dataclass
class PreparedRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes


@dataclass
class DeliveryOutcome:
    receiver_id: str
    provider: WebhookProvider
    status: NotificationDeliveryStatus
    attempt_count: int
    response_status: int | None
    error_message: str | None


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _severity_tag(severity: NotificationSeverity) -> str:
    return f"[{severity.value.upper()}]"


def _format_text(message: NotificationMessage) -> str:
    header = f"{_severity_tag(message.severity)} {message.rule_name}"
    body = message.summary
    return f"{header}\n{body}\nsignal={message.signal_key}"


def _build_generic_payload(message: NotificationMessage) -> dict[str, object]:
    payload: dict[str, object] = {
        "rule_id": message.rule_id,
        "rule_name": message.rule_name,
        "signal_key": message.signal_key,
        "severity": message.severity.value,
        "summary": message.summary,
        "trigger": message.trigger.value,
        "snapshot": message.snapshot,
        "occurred_at": message.occurred_at.isoformat(),
    }
    if message.rule_config:
        payload.update(message.rule_config)
    return payload


def _select_payload_fields(receiver: NotificationWebhook, message: NotificationMessage) -> dict[str, object]:
    payload = _build_generic_payload(message)
    return {field: payload[field] for field in receiver.payload_fields if field in payload}


def _append_query_fields(url: str, fields: dict[str, object]) -> str:
    query_items: list[tuple[str, str]] = []
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            query_items.append((key, json.dumps(value, ensure_ascii=False, separators=(",", ":"))))
            continue
        if isinstance(value, bool):
            query_items.append((key, "true" if value else "false"))
            continue
        query_items.append((key, str(value)))
    if not query_items:
        return url
    parsed = urlparse(url)
    extra = urlencode(query_items)
    query = f"{parsed.query}&{extra}" if parsed.query else extra
    return urlunparse(parsed._replace(query=query))


def _stringify_url_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _stringify_template_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _render_url_template(url: str, fields: dict[str, object]) -> tuple[str, bool]:
    matched = False

    def replace(match: re.Match[str]) -> str:
        nonlocal matched
        field = match.group(1)
        if field not in fields:
            return match.group(0)
        matched = True
        return quote(_stringify_url_value(fields[field]), safe="")

    return URL_TEMPLATE_VARIABLE_RE.sub(replace, url), matched


def _template_context(message: NotificationMessage) -> dict[str, object]:
    context = _build_generic_payload(message)
    snapshot = message.snapshot if isinstance(message.snapshot, dict) else {}
    if snapshot:
        context["snapshot"] = snapshot
        if "value" in snapshot:
            context["value"] = snapshot["value"]
        data = snapshot.get("data")
        if isinstance(data, dict):
            context["data"] = data
            for key, value in data.items():
                context.setdefault(key, value)
    return context


def _lookup_template_value(context: dict[str, object], path: str) -> object | None:
    current: object = context
    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
            continue
        if isinstance(current, list) and part.isdigit():
            index = int(part)
            if index >= len(current):
                return None
            current = current[index]
            continue
        return None
    return current


def _render_template_string(value: str, context: dict[str, object]) -> str | object:
    full = FULL_CARD_TEMPLATE_VARIABLE_RE.match(value)
    if full:
        path = full.group(1) or full.group(2)
        resolved = _lookup_template_value(context, path)
        if resolved is not None:
            return resolved
        return ""

    def replace(match: re.Match[str]) -> str:
        path = match.group(1) or match.group(2)
        resolved = _lookup_template_value(context, path)
        if resolved is None:
            return ""
        return _stringify_template_value(resolved)

    return CARD_TEMPLATE_VARIABLE_RE.sub(replace, value)


def _render_template_payload(value: object, context: dict[str, object]) -> object:
    if isinstance(value, str):
        return _render_template_string(value, context)
    if isinstance(value, list):
        return [_render_template_payload(item, context) for item in value]
    if isinstance(value, dict):
        return {
            key: _render_template_payload(item, context)
            for key, item in value.items()
        }
    return value


def _render_feishu_card_template(
    template: dict[str, object], message: NotificationMessage
) -> dict[str, object]:
    rendered = _render_template_payload(template, _template_context(message))
    if not isinstance(rendered, dict):
        raise TypeError("feishuCardTemplate must render to a JSON object")
    return rendered


def _adapter_generic(receiver: NotificationWebhook, message: NotificationMessage) -> PreparedRequest:
    selected_payload = _select_payload_fields(receiver, message)
    if receiver.method == WebhookMethod.get:
        rendered_url, has_template = _render_url_template(receiver.url, _build_generic_payload(message))
        url = rendered_url if has_template else _append_query_fields(receiver.url, selected_payload)
        return PreparedRequest("GET", url, {}, b"")
    body = json.dumps(selected_payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if receiver.secret:
        signature = hmac.new(
            receiver.secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        headers["X-Signature"] = f"sha256={signature}"
    return PreparedRequest("POST", receiver.url, headers, body)


def _adapter_slack(receiver: NotificationWebhook, message: NotificationMessage) -> PreparedRequest:
    body = json.dumps({"text": _format_text(message)}, ensure_ascii=False).encode("utf-8")
    return PreparedRequest("POST", receiver.url, {"Content-Type": "application/json"}, body)


def _adapter_discord(receiver: NotificationWebhook, message: NotificationMessage) -> PreparedRequest:
    body = json.dumps({"content": _format_text(message)}, ensure_ascii=False).encode("utf-8")
    return PreparedRequest("POST", receiver.url, {"Content-Type": "application/json"}, body)


def _adapter_wecom(receiver: NotificationWebhook, message: NotificationMessage) -> PreparedRequest:
    payload = {"msgtype": "text", "text": {"content": _format_text(message)}}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return PreparedRequest("POST", receiver.url, {"Content-Type": "application/json"}, body)


def _adapter_feishu(receiver: NotificationWebhook, message: NotificationMessage) -> PreparedRequest:
    if receiver.feishu_card_template is not None:
        payload: dict[str, object] = {
            "msg_type": "interactive",
            "card": _render_feishu_card_template(receiver.feishu_card_template, message),
        }
    else:
        payload = {
            "msg_type": "text",
            "content": {"text": _format_text(message)},
        }
    if receiver.secret:
        timestamp = str(int(time.time()))
        sign_string = f"{timestamp}\n{receiver.secret}"
        signature = base64.b64encode(
            hmac.new(sign_string.encode("utf-8"), b"", hashlib.sha256).digest()
        ).decode("utf-8")
        payload["timestamp"] = timestamp
        payload["sign"] = signature
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return PreparedRequest("POST", receiver.url, {"Content-Type": "application/json"}, body)


def _adapter_dingtalk(receiver: NotificationWebhook, message: NotificationMessage) -> PreparedRequest:
    url = receiver.url
    if receiver.secret:
        timestamp = str(int(time.time() * 1000))
        sign_string = f"{timestamp}\n{receiver.secret}"
        signature = base64.b64encode(
            hmac.new(receiver.secret.encode("utf-8"), sign_string.encode("utf-8"), hashlib.sha256).digest()
        ).decode("utf-8")
        from urllib.parse import quote, urlencode, urlparse, urlunparse

        parsed = urlparse(receiver.url)
        existing_qs = parsed.query
        extra = urlencode({"timestamp": timestamp, "sign": signature}, quote_via=quote)
        new_qs = f"{existing_qs}&{extra}" if existing_qs else extra
        url = urlunparse(parsed._replace(query=new_qs))
    payload = {"msgtype": "text", "text": {"content": _format_text(message)}}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return PreparedRequest("POST", url, {"Content-Type": "application/json"}, body)


_ADAPTERS: dict[WebhookProvider, Callable[[NotificationWebhook, NotificationMessage], PreparedRequest]] = {
    WebhookProvider.generic: _adapter_generic,
    WebhookProvider.slack: _adapter_slack,
    WebhookProvider.discord: _adapter_discord,
    WebhookProvider.wecom: _adapter_wecom,
    WebhookProvider.feishu: _adapter_feishu,
    WebhookProvider.dingtalk: _adapter_dingtalk,
}


def build_request(
    receiver: NotificationWebhook, message: NotificationMessage
) -> PreparedRequest:
    adapter = _ADAPTERS[receiver.provider]
    return adapter(receiver, message)


class NotificationDeliveryService:
    def __init__(
        self,
        store: SQLiteFlowStore,
        session: requests.Session | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.store = store
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds

    def deliver(
        self,
        receiver: NotificationWebhook,
        message: NotificationMessage,
    ) -> DeliveryOutcome:
        if not receiver.enabled or not receiver.url.strip():
            self._record(
                receiver=receiver,
                message=message,
                status=NotificationDeliveryStatus.skipped,
                attempt_index=0,
                response_status=None,
                error="receiver disabled or has empty URL",
                payload_digest="",
            )
            return DeliveryOutcome(
                receiver_id=receiver.id,
                provider=receiver.provider,
                status=NotificationDeliveryStatus.skipped,
                attempt_count=0,
                response_status=None,
                error_message="receiver disabled or has empty URL",
            )

        prepared = build_request(receiver, message)
        digest = _digest(prepared.body)
        last_status: int | None = None
        last_error: str | None = None
        attempt_count = 0
        for attempt in range(MAX_ATTEMPTS):
            attempt_count = attempt + 1
            try:
                response = self.session.request(
                    prepared.method,
                    prepared.url,
                    data=prepared.body or None,
                    headers=prepared.headers,
                    timeout=self.timeout_seconds,
                )
                last_status = response.status_code
                if 200 <= response.status_code < 300:
                    self._record(
                        receiver=receiver,
                        message=message,
                        status=NotificationDeliveryStatus.succeeded,
                        attempt_index=attempt_count,
                        response_status=last_status,
                        error=None,
                        payload_digest=digest,
                    )
                    return DeliveryOutcome(
                        receiver_id=receiver.id,
                        provider=receiver.provider,
                        status=NotificationDeliveryStatus.succeeded,
                        attempt_count=attempt_count,
                        response_status=last_status,
                        error_message=None,
                    )
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                if response.status_code not in RETRYABLE_STATUS_CODES:
                    break
            except requests.RequestException as exc:
                last_error = str(exc)
                logger.warning(
                    "Notification delivery attempt failed | receiver=%s | attempt=%d | error=%s",
                    receiver.id,
                    attempt_count,
                    exc,
                )
            if attempt_count < MAX_ATTEMPTS:
                self._sleep(BACKOFF_SECONDS[attempt])

        self._record(
            receiver=receiver,
            message=message,
            status=NotificationDeliveryStatus.failed,
            attempt_index=attempt_count,
            response_status=last_status,
            error=last_error,
            payload_digest=digest,
        )
        return DeliveryOutcome(
            receiver_id=receiver.id,
            provider=receiver.provider,
            status=NotificationDeliveryStatus.failed,
            attempt_count=attempt_count,
            response_status=last_status,
            error_message=last_error,
        )

    def _sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def _record(
        self,
        *,
        receiver: NotificationWebhook,
        message: NotificationMessage,
        status: NotificationDeliveryStatus,
        attempt_index: int,
        response_status: int | None,
        error: str | None,
        payload_digest: str,
    ) -> None:
        record = NotificationDeliveryRecord(
            receiver_id=receiver.id,
            rule_id=message.rule_id,
            provider=receiver.provider,
            severity=message.severity,
            trigger=message.trigger,
            status=status,
            attempt_index=attempt_index,
            response_status=response_status,
            error_message=error,
            payload_digest=payload_digest,
        )
        self.store.save_notification_delivery(record)
