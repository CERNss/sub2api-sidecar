from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
import requests

import app.main as main
from app.models.notification import (
    NotificationDeliveryStatus,
    NotificationDeliveryTrigger,
    NotificationMessage,
    NotificationSeverity,
    NotificationSettings,
    NotificationWebhook,
    WebhookProvider,
)
from app.services.notification_delivery import (
    NotificationDeliveryService,
    build_request,
)


AUTH_PAYLOAD = {"username": "admin", "password": "test-admin-pass"}


def login(client) -> None:
    response = client.post("/auth/login", json=AUTH_PAYLOAD)
    assert response.status_code == 200


def _message(severity: NotificationSeverity = NotificationSeverity.warning) -> NotificationMessage:
    return NotificationMessage(
        rule_id="rule-1",
        rule_name="Account invalid",
        signal_key="account_invalid",
        severity=severity,
        summary="Test alert",
        trigger=NotificationDeliveryTrigger.test,
    )


def test_notification_config_requires_auth(client) -> None:
    assert client.get("/notifications/config").status_code == 401
    assert client.put("/notifications/config", json={}).status_code == 401


def test_notification_config_returns_default_when_unset(client) -> None:
    login(client)
    response = client.get("/notifications/config")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["webhooks"]) == 1
    assert payload["webhooks"][0]["enabled"] is False
    assert len(payload["rules"]) >= 4
    rule_signals = {rule["signalKey"] for rule in payload["rules"]}
    assert "account_invalid" in rule_signals


def test_notification_config_round_trip_redacts_secret(client) -> None:
    login(client)
    body = {
        "webhooks": [
            {
                "id": "ops",
                "name": "Ops",
                "enabled": True,
                "provider": "generic",
                "url": "https://hooks.example.com/incoming",
                "secret": "shh",
                "mentionOnFailure": False,
            }
        ],
        "rules": [
            {
                "id": "rule-account-invalid",
                "name": "Account invalid",
                "enabled": True,
                "signalKey": "account_invalid",
                "severity": "critical",
                "operator": "gte",
                "threshold": "1",
                "warningThreshold": "1",
                "recoveryThreshold": "",
                "thresholdUnit": "accounts",
                "aggregation": "sum",
                "readIntervalMinutes": 5,
                "evaluationWindowMinutes": 10,
                "forMinutes": 5,
                "cooldownMinutes": 30,
                "targetWebhookIds": ["ops"],
                "includeResolved": True,
                "includeSnapshot": True,
            }
        ],
        "policy": {
            "groupBy": "severity",
            "groupWaitMinutes": 1,
            "repeatIntervalMinutes": 60,
            "quietHoursEnabled": False,
            "quietHoursStart": "22:00",
            "quietHoursEnd": "08:00",
        },
    }
    put_response = client.put("/notifications/config", json=body)
    assert put_response.status_code == 200
    assert put_response.json()["webhooks"][0]["secret"] == "[redacted]"

    get_response = client.get("/notifications/config")
    assert get_response.status_code == 200
    payload = get_response.json()
    assert payload["webhooks"][0]["url"] == "https://hooks.example.com/incoming"
    assert payload["webhooks"][0]["secret"] == "[redacted]"
    assert payload["rules"][0]["targetWebhookIds"] == ["ops"]


def test_notification_config_rejects_unknown_target_id(client) -> None:
    login(client)
    body = {
        "webhooks": [
            {"id": "ops", "name": "Ops", "enabled": True, "provider": "generic", "url": "https://x", "secret": "", "mentionOnFailure": False}
        ],
        "rules": [
            {
                "id": "r1",
                "name": "r1",
                "enabled": True,
                "signalKey": "account_invalid",
                "severity": "warning",
                "operator": "gte",
                "threshold": "1",
                "warningThreshold": "1",
                "recoveryThreshold": "",
                "thresholdUnit": "",
                "aggregation": "latest",
                "readIntervalMinutes": 5,
                "evaluationWindowMinutes": 10,
                "forMinutes": 0,
                "cooldownMinutes": 0,
                "targetWebhookIds": ["does-not-exist"],
                "includeResolved": True,
                "includeSnapshot": False,
            }
        ],
        "policy": {
            "groupBy": "severity",
            "groupWaitMinutes": 1,
            "repeatIntervalMinutes": 60,
            "quietHoursEnabled": False,
            "quietHoursStart": "22:00",
            "quietHoursEnd": "08:00",
        },
    }
    response = client.put("/notifications/config", json=body)
    assert response.status_code == 422
    assert "unknown receiver ids" in response.json()["detail"]


def test_notification_legacy_receiver_only_synthesizes_rules(client) -> None:
    login(client)
    legacy = NotificationSettings(
        webhooks=[
            NotificationWebhook(
                id="legacy",
                name="Legacy",
                enabled=True,
                provider=WebhookProvider.generic,
                url="https://legacy.example.com/hook",
            )
        ]
    )
    main.get_flow_store().save_notification_settings(legacy)

    response = client.get("/notifications/config")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["webhooks"]) == 1
    assert payload["webhooks"][0]["id"] == "legacy"
    assert len(payload["rules"]) >= 4
    for rule in payload["rules"]:
        assert rule["targetWebhookIds"] == ["legacy"]


def test_notification_test_endpoint_rejects_unknown_rule(client) -> None:
    login(client)
    response = client.post("/notifications/test", json={"rule_id": "nope"})
    assert response.status_code == 400
    assert "No rule found" in response.json()["detail"]


def test_notification_test_endpoint_rejects_no_sendable_receiver(client) -> None:
    login(client)
    settings = NotificationSettings.model_validate(
        {
            "webhooks": [
                {"id": "off", "name": "Off", "enabled": False, "provider": "generic", "url": "https://x", "secret": "", "mentionOnFailure": False}
            ],
            "rules": [
                {
                    "id": "r1",
                    "name": "r1",
                    "enabled": True,
                    "signalKey": "account_invalid",
                    "severity": "warning",
                    "operator": "gte",
                    "threshold": "1",
                    "warningThreshold": "1",
                    "recoveryThreshold": "",
                    "thresholdUnit": "",
                    "aggregation": "latest",
                    "readIntervalMinutes": 5,
                    "evaluationWindowMinutes": 10,
                    "forMinutes": 0,
                    "cooldownMinutes": 0,
                    "targetWebhookIds": ["off"],
                    "includeResolved": True,
                    "includeSnapshot": False,
                }
            ],
        }
    )
    main.get_flow_store().save_notification_settings(settings)

    response = client.post("/notifications/test", json={"rule_id": "r1"})
    assert response.status_code == 400
    assert "no enabled receivers" in response.json()["detail"]


def test_notification_test_endpoint_delivers_and_audits(client) -> None:
    login(client)
    settings = NotificationSettings.model_validate(
        {
            "webhooks": [
                {"id": "ops", "name": "Ops", "enabled": True, "provider": "generic", "url": "https://hooks.example.com/incoming", "secret": "shh", "mentionOnFailure": False}
            ],
            "rules": [
                {
                    "id": "r1",
                    "name": "Account invalid",
                    "enabled": True,
                    "signalKey": "account_invalid",
                    "severity": "critical",
                    "operator": "gte",
                    "threshold": "1",
                    "warningThreshold": "1",
                    "recoveryThreshold": "",
                    "thresholdUnit": "",
                    "aggregation": "sum",
                    "readIntervalMinutes": 5,
                    "evaluationWindowMinutes": 10,
                    "forMinutes": 0,
                    "cooldownMinutes": 0,
                    "targetWebhookIds": ["ops"],
                    "includeResolved": True,
                    "includeSnapshot": True,
                }
            ],
        }
    )
    main.get_flow_store().save_notification_settings(settings)

    captured: list[dict[str, Any]] = []

    class _OkResp:
        status_code = 200
        text = "ok"

    def fake_request(self, method, url, data=None, headers=None, timeout=None):
        captured.append({"method": method, "url": url, "headers": dict(headers or {}), "body": data})
        return _OkResp()

    with patch.object(requests.Session, "request", new=fake_request):
        response = client.post("/notifications/test", json={"rule_id": "r1"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["rule_id"] == "r1"
    assert len(payload["outcomes"]) == 1
    outcome = payload["outcomes"][0]
    assert outcome["status"] == "succeeded"
    assert outcome["attempt_count"] == 1
    assert outcome["response_status"] == 200

    assert len(captured) == 1
    sent = captured[0]
    body = json.loads(sent["body"])
    assert body["rule_id"] == "r1"
    assert body["signal_key"] == "account_invalid"
    assert body["trigger"] == "test"
    assert sent["headers"]["X-Signature"].startswith("sha256=")

    deliveries_response = client.get("/notifications/deliveries")
    assert deliveries_response.status_code == 200
    items = deliveries_response.json()["items"]
    assert items[0]["status"] == "succeeded"
    assert items[0]["receiver_id"] == "ops"
    assert items[0]["trigger"] == "test"


def test_notification_delivery_skips_disabled_and_records_audit(app_env) -> None:
    main.get_flow_store.cache_clear()
    store = main.get_flow_store()
    delivery = NotificationDeliveryService(store=store)
    receiver = NotificationWebhook(id="off", enabled=False, provider=WebhookProvider.generic, url="https://example")
    outcome = delivery.deliver(receiver, _message())

    assert outcome.status == NotificationDeliveryStatus.skipped
    audit = store.list_notification_deliveries()
    assert audit[0].status == NotificationDeliveryStatus.skipped
    assert audit[0].attempt_index == 0


def test_notification_delivery_retries_transient_then_fails(app_env) -> None:
    main.get_flow_store.cache_clear()
    store = main.get_flow_store()
    delivery = NotificationDeliveryService(store=store)

    class _Resp:
        status_code = 503
        text = "unavailable"

    def fake_request(self, method, url, data=None, headers=None, timeout=None):
        return _Resp()

    receiver = NotificationWebhook(
        id="ops",
        enabled=True,
        provider=WebhookProvider.generic,
        url="https://hooks.example.com/incoming",
    )

    with patch.object(requests.Session, "request", new=fake_request), patch.object(
        NotificationDeliveryService, "_sleep", lambda self, _seconds: None
    ):
        outcome = delivery.deliver(receiver, _message())

    assert outcome.status == NotificationDeliveryStatus.failed
    assert outcome.attempt_count == 3
    assert outcome.response_status == 503
    audit = store.list_notification_deliveries()
    assert audit[0].status == NotificationDeliveryStatus.failed
    assert audit[0].attempt_index == 3


# --- Provider adapter payload/signing shape ---


def test_provider_generic_signs_with_hmac_when_secret_present() -> None:
    receiver = NotificationWebhook(
        id="g",
        enabled=True,
        provider=WebhookProvider.generic,
        url="https://example.com/hook",
        secret="topsecret",
    )
    prepared = build_request(receiver, _message())
    expected = hmac.new(b"topsecret", prepared.body, hashlib.sha256).hexdigest()
    assert prepared.headers["X-Signature"] == f"sha256={expected}"


def test_provider_slack_uses_text_field() -> None:
    receiver = NotificationWebhook(id="s", enabled=True, provider=WebhookProvider.slack, url="https://hooks.slack.com/x")
    prepared = build_request(receiver, _message())
    body = json.loads(prepared.body)
    assert "text" in body
    assert "[WARNING]" in body["text"]


def test_provider_discord_uses_content_field() -> None:
    receiver = NotificationWebhook(id="d", enabled=True, provider=WebhookProvider.discord, url="https://discord.com/api/webhooks/1/x")
    prepared = build_request(receiver, _message())
    body = json.loads(prepared.body)
    assert "content" in body


def test_provider_wecom_uses_msgtype_text() -> None:
    receiver = NotificationWebhook(id="w", enabled=True, provider=WebhookProvider.wecom, url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x")
    prepared = build_request(receiver, _message())
    body = json.loads(prepared.body)
    assert body["msgtype"] == "text"
    assert "content" in body["text"]


def test_provider_feishu_includes_sign_when_secret_present() -> None:
    receiver = NotificationWebhook(
        id="f",
        enabled=True,
        provider=WebhookProvider.feishu,
        url="https://open.feishu.cn/open-apis/bot/v2/hook/x",
        secret="lark-secret",
    )
    prepared = build_request(receiver, _message())
    body = json.loads(prepared.body)
    assert body["msg_type"] == "text"
    assert "timestamp" in body
    assert "sign" in body
    expected = base64.b64encode(
        hmac.new(f"{body['timestamp']}\nlark-secret".encode(), b"", hashlib.sha256).digest()
    ).decode()
    assert body["sign"] == expected


def test_provider_dingtalk_appends_sign_to_url() -> None:
    receiver = NotificationWebhook(
        id="d",
        enabled=True,
        provider=WebhookProvider.dingtalk,
        url="https://oapi.dingtalk.com/robot/send?access_token=x",
        secret="ding-secret",
    )
    prepared = build_request(receiver, _message())
    qs = parse_qs(urlparse(prepared.url).query)
    assert "timestamp" in qs and "sign" in qs
    timestamp = qs["timestamp"][0]
    expected = base64.b64encode(
        hmac.new(b"ding-secret", f"{timestamp}\nding-secret".encode(), hashlib.sha256).digest()
    ).decode()
    assert qs["sign"][0] == expected
    body = json.loads(prepared.body)
    assert body["msgtype"] == "text"
