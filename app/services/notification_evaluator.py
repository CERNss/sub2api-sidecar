from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Iterable

from app.models.notification import (
    CollectorSample,
    NotificationOperator,
    NotificationRoutingPolicy,
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


def parse_clock(text: str, fallback: time) -> time:
    parts = (text or "").split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        return time(hour=max(0, min(23, hour)), minute=max(0, min(59, minute)))
    except (TypeError, ValueError, IndexError):
        return fallback


def is_quiet_hours(policy: NotificationRoutingPolicy, now: datetime) -> bool:
    if not policy.quiet_hours_enabled:
        return False
    start = parse_clock(policy.quiet_hours_start, time(22, 0))
    end = parse_clock(policy.quiet_hours_end, time(8, 0))
    current = now.time().replace(second=0, microsecond=0)
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


def evaluate_rule(
    rule: NotificationRule,
    sample: CollectorSample | None,
    prior_state: NotificationRuleState | None,
    now: datetime,
    *,
    in_quiet_hours: bool = False,
    no_data_reason: str | None = None,
) -> RuleDecision:
    state = _clone_state(prior_state, rule.id)
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
    recovery_threshold = _parse_threshold(rule.recovery_threshold)

    breaching = threshold is not None and _compare(rule.operator, sample.value, threshold)

    if state.is_firing:
        recovered = recovery_threshold is not None and _compare(
            _inverse_operator(rule.operator), sample.value, recovery_threshold
        )
        if recovered:
            state.is_firing = False
            state.breach_started_at = None
            action = (
                NotificationRuleAction.recover
                if rule.include_resolved
                else NotificationRuleAction.hold
            )
            return RuleDecision(
                action=action,
                reason=f"value={sample.value} crossed recovery threshold={recovery_threshold}",
                sample=sample,
                next_state=state,
            )
        if not breaching:
            return RuleDecision(
                action=NotificationRuleAction.hold,
                reason=f"value={sample.value} not breaching but no recovery threshold met",
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
        if in_quiet_hours:
            state.last_alert_at = now
            return RuleDecision(
                action=NotificationRuleAction.suppress,
                reason="quiet hours suppress firing alert",
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
    if in_quiet_hours:
        state.last_alert_at = now
        return RuleDecision(
            action=NotificationRuleAction.suppress,
            reason="quiet hours suppress initial fire",
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


def _inverse_operator(operator: NotificationOperator) -> NotificationOperator:
    inverse = {
        NotificationOperator.gt: NotificationOperator.lte,
        NotificationOperator.gte: NotificationOperator.lt,
        NotificationOperator.lt: NotificationOperator.gte,
        NotificationOperator.lte: NotificationOperator.gt,
        NotificationOperator.eq: NotificationOperator.neq,
        NotificationOperator.neq: NotificationOperator.eq,
    }
    return inverse[operator]


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


# Late import for typing — keeps a one-way dependency from collectors/evaluator into models
from app.models.notification import NotificationWebhook  # noqa: E402
