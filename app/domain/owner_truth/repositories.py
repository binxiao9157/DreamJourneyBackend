"""Repository ports for later Owner Truth slices.

No adapter is registered in WI-S1-01-01.  This keeps the legacy compatibility
stores on their existing write path until the CreateSource facade is gated.
"""

from __future__ import annotations

from typing import Optional, Protocol, Sequence

from .contracts import OwnerTruthMemoryRecord, OwnerTruthMemoryVersion, OwnerTruthSource


class OwnerTruthReadRepository(Protocol):
    def get_source(self, *, vault_id: str, source_id: str) -> Optional[OwnerTruthSource]:
        ...

    def get_memory(self, *, vault_id: str, memory_id: str) -> Optional[OwnerTruthMemoryRecord]:
        ...

    def list_memory_versions(
        self,
        *,
        vault_id: str,
        memory_id: str,
    ) -> Sequence[OwnerTruthMemoryVersion]:
        ...
