## 1. Persistence

- [x] 1.1 Add `NotificationRuleState` Pydantic model with `rule_id`, `last_evaluated_at`, `last_value`, `breach_started_at`, `last_alert_at`, `is_firing`, `last_error`, timestamps
- [x] 1.2 Add SQLite table `notification_rule_states` keyed by `rule_id` with upsert and read methods on the store

## 2. Collector registry

- [x] 2.1 Define `SignalCollector` protocol returning `CollectorSample | None` and a registry keyed by `signalKey`
- [x] 2.2 Add a stub collector for every `signalKey` from the UI catalog returning `None` and recording an "unimplemented" reason
- [x] 2.3 Expose a way to register real collectors so each signal can be filled in incrementally without changing the scheduler

## 3. Evaluator

- [x] 3.1 Implement `evaluate_rule(rule, sample, prior_state, now, in_quiet_hours)` returning a `RuleDecision` and next state
- [x] 3.2 Honor aggregation/operator/threshold for the `fire` decision and `recoveryThreshold` for `recover`
- [x] 3.3 Enforce `forMinutes` sustained breach requirement before firing
- [x] 3.4 Enforce `cooldownMinutes` repeat suppression after a fire
- [x] 3.5 Honor `includeResolved` and `includeSnapshot` when building the outbound message
- [x] 3.6 Treat `None` collector samples as `no_data` and surface a `last_error` on the rule state

## 4. Scheduler and dispatcher

- [x] 4.1 Add `NotificationScheduler` thread modeled on `AutoRotationScheduler` with configurable tick seconds and lifespan integration
- [x] 4.2 On each tick, list enabled rules whose `readIntervalMinutes` window has elapsed and evaluate each one
- [x] 4.3 Apply quiet-hours suppression based on the saved policy and write `skipped` audit rows with `trigger='rule'`
- [x] 4.4 Dispatch firing/recovering rules through the existing `NotificationDeliveryService` to all enabled target receivers
- [x] 4.5 Update rule state on every tick, regardless of decision

## 5. On-demand evaluation API

- [x] 5.1 Add `POST /notifications/evaluate` accepting `rule_id` and returning the rule decision plus per-receiver outcomes
- [x] 5.2 Reject unknown rule ids and rules without sendable receivers with a client error response

## 6. Verification

- [x] 6.1 Add tests for the collector registry default-stub behavior and registration of a real collector
- [x] 6.2 Add tests for the evaluator covering fire, recover, hold, no_data, and suppress paths including sustained-for and cooldown
- [x] 6.3 Add tests for the scheduler tick that exercise the read-interval, dispatch, and rule-state persistence
- [x] 6.4 Add tests for quiet-hours suppression that verify audit rows but no outbound HTTP
- [x] 6.5 Add tests for `POST /notifications/evaluate` covering success, missing rule, and unsendable rule
