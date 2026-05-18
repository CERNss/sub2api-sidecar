from __future__ import annotations

from datetime import datetime, timedelta

from app.models.notification import (
    CollectorSample,
    NotificationOperator,
    NotificationRule,
    NotificationRuleAction,
    NotificationRuleState,
    RuleDecision,
)


def _compare(operator: NotificationOperator, value: float, threshold: float) -> bool:
    if operator == NotificationOperator.gt:
        return value > threshold
    if operator == NotificationOperator.gte:
        return value >= threshold
    if operator == NotificationOperator.lt:
        return value < threshold
    if operator == NotificationOperator.lte:
        return value <= threshold
    if operator == NotificationOperator.eq:
        return value == threshold
    return value != threshold


def _parse_threshold(raw: str) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def evaluate_rule(
    rule: NotificationRule,
    sample: CollectorSample | None,
    prior_state: NotificationRuleState | None,
    now: datetime,
    *,
    no_data_reason: str | None = None,
) -> RuleDecision:
    state = _clone_state(prior_state, rule.id)
    if sample is not None:
        state.scope_key = sample.scope_key
        state.scope_label = sample.scope_label
    state.last_evaluated_at = now
    state.updated_at = now

    if sample is None:
        state.last_error = no_data_reason or "no sample available"
        return RuleDecision(
            action=NotificationRuleAction.no_data,
            reason=state.last_error,
            sample=None,
            next_state=state,
        )

    state.last_error = None
    state.last_value = sample.value
    threshold = _parse_threshold(rule.threshold)
    breaching = threshold is not None and _compare(rule.operator, sample.value, threshold)

    if state.is_firing:
        if not breaching:
            state.is_firing = False
            state.breach_started_at = None
            action = (
                NotificationRuleAction.recover
                if rule.include_resolved
                else NotificationRuleAction.hold
            )
            return RuleDecision(
                action=action,
                reason=f"value={sample.value} no longer breaching threshold={threshold}",
                sample=sample,
                next_state=state,
            )
        if rule.cooldown_minutes > 0 and state.last_alert_at is not None:
            earliest_resend = state.last_alert_at + timedelta(minutes=rule.cooldown_minutes)
            if earliest_resend > now:
                return RuleDecision(
                    action=NotificationRuleAction.hold,
                    reason="still within cooldown window",
                    sample=sample,
                    next_state=state,
                )
        state.last_alert_at = now
        return RuleDecision(
            action=NotificationRuleAction.fire,
            reason=f"sustained breach value={sample.value} threshold={threshold}",
            sample=sample,
            next_state=state,
        )

    if not breaching:
        state.breach_started_at = None
        return RuleDecision(
            action=NotificationRuleAction.hold,
            reason=f"value={sample.value} not breaching threshold={threshold}",
            sample=sample,
            next_state=state,
        )

    if state.breach_started_at is None:
        state.breach_started_at = now

    sustained_window = timedelta(minutes=max(0, rule.for_minutes))
    if now - state.breach_started_at < sustained_window:
        return RuleDecision(
            action=NotificationRuleAction.hold,
            reason="breach within sustained window",
            sample=sample,
            next_state=state,
        )

    state.is_firing = True
    state.last_alert_at = now
    return RuleDecision(
        action=NotificationRuleAction.fire,
        reason=f"sustained breach value={sample.value} threshold={threshold}",
        sample=sample,
        next_state=state,
    )


def _clone_state(state: NotificationRuleState | None, rule_id: str) -> NotificationRuleState:
    if state is None:
        return NotificationRuleState(rule_id=rule_id)
    return state.model_copy()


def select_sendable_receivers(
    rule: NotificationRule, receivers_by_id: dict[str, "NotificationWebhook"]
) -> list["NotificationWebhook"]:
    """Return receivers that exist, are enabled, and have a non-empty URL."""
    sendable: list["NotificationWebhook"] = []
    for receiver_id in rule.target_webhook_ids:
        receiver = receivers_by_id.get(receiver_id)
        if receiver is None:
            continue
        if not receiver.enabled or not receiver.url.strip():
            continue
        sendable.append(receiver)
    return sendable


from app.models.notification import NotificationWebhook  # noqa: E402
