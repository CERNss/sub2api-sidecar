from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.flow import AssignmentMode, FlowStatus, ProvisionEvent, ProvisionFlow


class FlowStore(ABC):
    """Abstract flow persistence interface. SQLite is the default implementation."""

    @abstractmethod
    def save(self, flow: ProvisionFlow) -> ProvisionFlow:
        raise NotImplementedError

    @abstractmethod
    def get_by_flow_id(self, flow_id: str) -> ProvisionFlow | None:
        raise NotImplementedError

    @abstractmethod
    def get_by_state(self, state: str) -> ProvisionFlow | None:
        raise NotImplementedError

    @abstractmethod
    def list_flows(
        self,
        *,
        status: FlowStatus | None = None,
        assignment_mode: AssignmentMode | None = None,
        email: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ProvisionFlow]:
        raise NotImplementedError

    @abstractmethod
    def count_flows(
        self,
        *,
        status: FlowStatus | None = None,
        assignment_mode: AssignmentMode | None = None,
        email: str | None = None,
    ) -> int:
        raise NotImplementedError

    @abstractmethod
    def update(self, flow: ProvisionFlow) -> ProvisionFlow:
        raise NotImplementedError

    @abstractmethod
    def save_provision_event(self, event: ProvisionEvent) -> ProvisionEvent:
        raise NotImplementedError

    @abstractmethod
    def list_provision_events(self, flow_id: str) -> list[ProvisionEvent]:
        raise NotImplementedError
