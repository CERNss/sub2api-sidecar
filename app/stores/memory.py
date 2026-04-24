from __future__ import annotations

from threading import RLock

from app.models.flow import ProvisionFlow
from app.stores.base import FlowStore


class InMemoryFlowStore(FlowStore):
    """Optional in-memory store kept for lightweight experiments and comparisons."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._flows: dict[str, ProvisionFlow] = {}
        self._flow_ids_by_state: dict[str, str] = {}

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

    def update(self, flow: ProvisionFlow) -> ProvisionFlow:
        return self.save(flow)
