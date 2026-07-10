from dataclasses import dataclass


@dataclass
class KnowledgeRevisionConflict(Exception):
    current_revision: int
    expected_revision: int

    def __str__(self) -> str:
        return (
            "knowledge revision conflict: "
            f"expected {self.expected_revision}, current {self.current_revision}"
        )
