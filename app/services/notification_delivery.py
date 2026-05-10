from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Callable

import requests

from app.models.notification import (
    NotificationDeliveryRecord,
    NotificationDeliveryStatus,
    NotificationDeliveryTrigger,
    NotificationMessage,
    NotificationSeverity,
    NotificationWebhook,
    WebhookProvider,
)
from app.stores.sqlite import SQLiteFlowStore

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (1.0, 2.0, 4.0)
RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


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
    return {
        "rule_id": message.rule_id,
        "rule_name": message.rule_name,
        "signal_key": message.signal_key,
        "severity": message.severity.value,
        "summary": message.summary,
        "trigger": message.trigger.value,
        "snapshot": message.snapshot,
        "occurred_at": message.occurred_at.isoformat(),
    }


def _adapter_generic(receiver: NotificationWebhook, message: NotificationMessage) -> PreparedRequest:
    body = json.dumps(_build_generic_payload(message), ensure_ascii=False).encode("utf-8")
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
    payload: dict[str, object] = {
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
                    data=prepared.body,
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
