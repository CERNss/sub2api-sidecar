from __future__ import annotations

from threading import RLock

from app.models.flow import AssignmentMode, FlowStatus, ProvisionEvent, ProvisionFlow
from app.stores.base import FlowStore


class InMemoryFlowStore(FlowStore):
    """Optional in-memory store kept for lightweight experiments and comparisons."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._flows: dict[str, ProvisionFlow] = {}
        self._flow_ids_by_state: dict[str, str] = {}
        self._events: list[ProvisionEvent] = []

    def save(self, flow: ProvisionFlow) -> ProvisionFlow:
        with self._lock:
            self._flows[flow.flow_id] = flow
            self._flow_ids_by_state[flow.state] = flow.flow_id
            return flow

    def get_by_flow_id(self, flow_id: str) -> ProvisionFlow | None:
        with self._lock:
            return self._flows.get(flow_id)

    def get_by_state(self, state: str) -> ProvisionFlow | None:
        with self._lock:
            flow_id = self._flow_ids_by_state.get(state)
            if not flow_id:
                return None
            return self._flows.get(flow_id)

    def list_flows(
        self,
        *,
        status: FlowStatus | None = None,
        assignment_mode: AssignmentMode | None = None,
        email: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ProvisionFlow]:
        with self._lock:
            flows = list(self._flows.values())
        flows = self._filter_flows(
            flows,
            status=status,
            assignment_mode=assignment_mode,
            email=email,
        )
        flows.sort(key=lambda item: (item.updated_at, item.created_at), reverse=True)
        return flows[offset : offset + limit]

    def count_flows(
        self,
        *,
        status: FlowStatus | None = None,
        assignment_mode: AssignmentMode | None = None,
        email: str | None = None,
    ) -> int:
        with self._lock:
            flows = list(self._flows.values())
        return len(
            self._filter_flows(
                flows,
                status=status,
                assignment_mode=assignment_mode,
                email=email,
            )
        )

    def update(self, flow: ProvisionFlow) -> ProvisionFlow:
        return self.save(flow)

    def save_provision_event(self, event: ProvisionEvent) -> ProvisionEvent:
        with self._lock:
            self._events.append(event)
        return event

    def list_provision_events(self, flow_id: str) -> list[ProvisionEvent]:
        with self._lock:
            events = [event for event in self._events if event.flow_id == flow_id]
        events.sort(key=lambda item: (item.created_at, item.event_id))
        return events

    def _filter_flows(
        self,
        flows: list[ProvisionFlow],
        *,
        status: FlowStatus | None,
        assignment_mode: AssignmentMode | None,
        email: str | None,
    ) -> list[ProvisionFlow]:
        filtered = flows
        if status is not None:
            filtered = [flow for flow in filtered if flow.status == status]
        if assignment_mode is not None:
            filtered = [
                flow for flow in filtered if flow.assignment_mode == assignment_mode
            ]
        if email:
            email_text = email.lower()
            filtered = [flow for flow in filtered if email_text in flow.email.lower()]
        return filtered
