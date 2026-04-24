from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.flow import ProvisionFlow


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
    def update(self, flow: ProvisionFlow) -> ProvisionFlow:
        raise NotImplementedError
