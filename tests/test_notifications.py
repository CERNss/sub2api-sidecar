from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import requests

import app.main as main
from app.models.notification import (
    NotificationDeliveryStatus,
    NotificationDeliveryTrigger,
    NotificationMessage,
    NotificationSeverity,
    NotificationSettings,
    NotificationWebhook,
    WebhookMethod,
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


def _webhook_body(**overrides: Any) -> dict[str, Any]:
    body = {
        "id": "ops",
        "name": "Ops",
        "enabled": True,
        "provider": "generic",
        "method": "POST",
        "url": "https://hooks.example.com/incoming",
        "secret": "",
    }
    body.update(overrides)
    return body


def _rule_body(**overrides: Any) -> dict[str, Any]:
    body = {
        "id": "rule-account-invalid",
        "name": "Account invalid",
        "enabled": True,
        "signalKey": "account_invalid",
        "severity": "critical",
        "operator": "gte",
        "threshold": "1",
        "thresholdUnit": "accounts",
        "readIntervalMinutes": 5,
        "forMinutes": 5,
        "cooldownMinutes": 30,
        "targetWebhookIds": ["ops"],
        "includeResolved": True,
        "includeSnapshot": True,
    }
    body.update(overrides)
    return body


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
    assert payload["rules"] == []
    assert "policy" not in payload
    assert "mentionOnFailure" not in payload["webhooks"][0]


def test_notification_config_round_trip_redacts_secret(client) -> None:
    login(client)
    body = {
        "webhooks": [_webhook_body(secret="shh")],
        "rules": [_rule_body()],
    }
    put_response = client.put("/notifications/config", json=body)
    assert put_response.status_code == 200
    assert put_response.json()["webhooks"][0]["secret"] == "[redacted]"

    get_response = client.get("/notifications/config")
    assert get_response.status_code == 200
    payload = get_response.json()
    assert payload["webhooks"][0]["url"] == "https://hooks.example.com/incoming"
    assert payload["webhooks"][0]["method"] == "POST"
    assert payload["webhooks"][0]["payloadFields"] == [
        "rule_id",
        "rule_name",
        "signal_key",
        "severity",
        "summary",
        "trigger",
        "snapshot",
        "occurred_at",
    ]
    assert payload["webhooks"][0]["secret"] == "[redacted]"
    assert payload["rules"][0]["targetWebhookIds"] == ["ops"]
    assert "recoveryThreshold" not in payload["rules"][0]
    assert "evaluationWindowMinutes" not in payload["rules"][0]
    assert "aggregation" not in payload["rules"][0]
    assert "policy" not in payload


def test_notification_config_preserves_redacted_secret_on_writeback(client) -> None:
    login(client)
    body = {
        "webhooks": [_webhook_body(secret="shh")],
        "rules": [_rule_body()],
    }
    response = client.put("/notifications/config", json=body)
    assert response.status_code == 200

    redacted = client.get("/notifications/config").json()
    redacted["webhooks"][0]["name"] = "Ops Renamed"
    redacted["rules"][0]["cooldownMinutes"] = 45

    writeback = client.put("/notifications/config", json=redacted)

    assert writeback.status_code == 200
    assert writeback.json()["webhooks"][0]["secret"] == "[redacted]"
    stored = main.get_flow_store().get_notification_settings()
    assert stored is not None
    assert stored.webhooks[0].name == "Ops Renamed"
    assert stored.webhooks[0].secret == "shh"
    assert stored.rules[0].cooldown_minutes == 45


def test_notification_config_persists_generic_get_method(client) -> None:
    login(client)
    body = {
        "webhooks": [_webhook_body(method="GET")],
        "rules": [_rule_body()],
    }

    response = client.put("/notifications/config", json=body)

    assert response.status_code == 200
    assert response.json()["webhooks"][0]["method"] == "GET"
    stored = main.get_flow_store().get_notification_settings()
    assert stored is not None
    assert stored.webhooks[0].method == WebhookMethod.get


def test_notification_config_persists_generic_payload_fields(client) -> None:
    login(client)
    body = {
        "webhooks": [_webhook_body(payloadFields=["name", "severity", "threshold"])],
        "rules": [_rule_body()],
    }

    response = client.put("/notifications/config", json=body)

    assert response.status_code == 200
    assert response.json()["webhooks"][0]["payloadFields"] == ["name", "severity", "threshold"]
    stored = main.get_flow_store().get_notification_settings()
    assert stored is not None
    assert stored.webhooks[0].payload_fields == ["name", "severity", "threshold"]


def test_notification_config_forces_non_generic_webhook_method_to_post(client) -> None:
    login(client)
    body = {
        "webhooks": [_webhook_body(provider="slack", method="GET")],
        "rules": [_rule_body()],
    }

    response = client.put("/notifications/config", json=body)

    assert response.status_code == 200
    assert response.json()["webhooks"][0]["method"] == "POST"
    stored = main.get_flow_store().get_notification_settings()
    assert stored is not None
    assert stored.webhooks[0].method == WebhookMethod.post


def test_notification_config_allows_clearing_secret(client) -> None:
    login(client)
    body = {
        "webhooks": [_webhook_body(secret="shh")],
        "rules": [_rule_body()],
    }
    response = client.put("/notifications/config", json=body)
    assert response.status_code == 200

    redacted = client.get("/notifications/config").json()
    redacted["webhooks"][0]["secret"] = ""
    writeback = client.put("/notifications/config", json=redacted)

    assert writeback.status_code == 200
    assert writeback.json()["webhooks"][0]["secret"] == ""
    stored = main.get_flow_store().get_notification_settings()
    assert stored is not None
    assert stored.webhooks[0].secret == ""


def test_notification_config_rejects_unknown_target_id(client) -> None:
    login(client)
    body = {
        "webhooks": [_webhook_body()],
        "rules": [_rule_body(targetWebhookIds=["does-not-exist"])],
    }
    response = client.put("/notifications/config", json=body)
    assert response.status_code == 422
    assert "unknown receiver ids" in response.json()["detail"]


def test_notification_config_rejects_legacy_policy_block(client) -> None:
    login(client)
    body = {
        "webhooks": [_webhook_body()],
        "rules": [_rule_body()],
        "policy": {"groupBy": "severity"},
    }
    response = client.put("/notifications/config", json=body)
    assert response.status_code == 422
    assert "policy" in response.json()["detail"]


def test_notification_config_rejects_legacy_rule_fields(client) -> None:
    login(client)
    body = {
        "webhooks": [_webhook_body()],
        "rules": [_rule_body(recoveryThreshold="0")],
    }
    response = client.put("/notifications/config", json=body)
    assert response.status_code == 422
    assert "recoveryThreshold" in response.json()["detail"]


def test_notification_config_rejects_legacy_webhook_fields(client) -> None:
    login(client)
    body = {
        "webhooks": [_webhook_body(mentionOnFailure=True)],
        "rules": [_rule_body()],
    }
    response = client.put("/notifications/config", json=body)
    assert response.status_code == 422
    assert "mentionOnFailure" in response.json()["detail"]


def test_notification_config_tolerates_legacy_persisted_keys_on_read(client) -> None:
    """A document saved before the simplify-alert-center change still loads;
    removed keys are dropped silently."""
    login(client)
    legacy_raw = {
        "webhooks": [_webhook_body(secret="kept", mentionOnFailure=True)],
        "rules": [
            _rule_body(
                recoveryThreshold="0",
                warningThreshold="1",
                aggregation="sum",
                evaluationWindowMinutes=10,
            )
        ],
        "policy": {"groupBy": "severity", "quietHoursEnabled": True},
    }
    # Use model_validate (which respects extra="ignore") to simulate hydrating from store.
    settings = NotificationSettings.model_validate(legacy_raw)
    main.get_flow_store().save_notification_settings(settings)

    response = client.get("/notifications/config")
    assert response.status_code == 200
    payload = response.json()
    assert "policy" not in payload
    assert "mentionOnFailure" not in payload["webhooks"][0]
    assert "recoveryThreshold" not in payload["rules"][0]
    assert "aggregation" not in payload["rules"][0]
    assert payload["webhooks"][0]["secret"] == "[redacted]"


def test_notification_legacy_receiver_only_returns_empty_rules(client) -> None:
    """Previously the system synthesised default rules for legacy receiver-only docs.
    After simplify-alert-center we return the saved shape without injecting rules."""
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
    assert payload["rules"] == []


def test_notification_test_endpoint_rejects_unknown_rule(client) -> None:
    login(client)
    response = client.post("/notifications/test", json={"rule_id": "nope"})
    assert response.status_code == 400
    assert "No rule found" in response.json()["detail"]


def test_notification_test_endpoint_rejects_no_sendable_receiver(client) -> None:
    login(client)
    settings = NotificationSettings.model_validate(
        {
            "webhooks": [_webhook_body(id="off", enabled=False)],
            "rules": [_rule_body(id="r1", name="r1", targetWebhookIds=["off"])],
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
            "webhooks": [_webhook_body(secret="shh")],
            "rules": [_rule_body(id="r1", name="Account invalid")],
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


def test_provider_generic_get_uses_empty_body() -> None:
    receiver = NotificationWebhook(
        id="g",
        enabled=True,
        provider=WebhookProvider.generic,
        method=WebhookMethod.get,
        url="https://example.com/hook?token=abc",
        payloadFields=[],
    )

    prepared = build_request(receiver, _message())

    assert prepared.method == "GET"
    qs = parse_qs(urlparse(prepared.url).query)
    assert qs["token"] == ["abc"]
    assert "rule_id" not in qs
    assert "rule_name" not in qs
    assert prepared.headers == {}
    assert prepared.body == b""


def test_provider_generic_post_uses_selected_json_fields() -> None:
    receiver = NotificationWebhook(
        id="g",
        enabled=True,
        provider=WebhookProvider.generic,
        url="https://example.com/hook",
        payloadFields=["name", "severity", "threshold"],
    )
    message = _message()
    message.rule_config = {
        "name": "限流/过载",
        "enabled": True,
        "signalKey": "account_rate_limited",
        "severity": "warning",
        "operator": "gte",
        "threshold": "1",
        "thresholdUnit": "accounts",
        "readIntervalMinutes": 1,
        "forMinutes": 5,
        "cooldownMinutes": 5,
        "includeResolved": True,
        "includeSnapshot": True,
    }

    prepared = build_request(receiver, message)
    body = json.loads(prepared.body)

    assert body == {
        "name": "限流/过载",
        "severity": "warning",
        "threshold": "1",
    }


def test_provider_generic_get_uses_selected_query_fields() -> None:
    receiver = NotificationWebhook(
        id="g",
        enabled=True,
        provider=WebhookProvider.generic,
        method=WebhookMethod.get,
        url="https://example.com/hook",
        payloadFields=["name", "severity", "threshold"],
    )
    message = _message()
    message.rule_config = {"name": "Account invalid", "signalKey": "account_invalid", "threshold": "1"}

    prepared = build_request(receiver, message)
    qs = parse_qs(urlparse(prepared.url).query)

    assert prepared.method == "GET"
    assert prepared.body == b""
    assert qs["name"] == ["Account invalid"]
    assert qs["severity"] == ["warning"]
    assert qs["threshold"] == ["1"]


def test_provider_generic_get_renders_url_template_fields() -> None:
    receiver = NotificationWebhook(
        id="g",
        enabled=True,
        provider=WebhookProvider.generic,
        method=WebhookMethod.get,
        url="https://example.com/hook?name=$name&severity=$severity&custom=$threshold",
        payloadFields=["name", "severity", "threshold"],
    )
    message = _message()
    message.rule_config = {"name": "限流/过载", "threshold": "1"}

    prepared = build_request(receiver, message)
    qs = parse_qs(urlparse(prepared.url).query)

    assert prepared.method == "GET"
    assert prepared.body == b""
    assert qs["name"] == ["限流/过载"]
    assert qs["severity"] == ["warning"]
    assert qs["custom"] == ["1"]


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
