from abc import ABC, abstractmethod
from typing import Any

class Connector(ABC):
    name: str

    @abstractmethod
    async def health(self) -> dict[str, Any]:
        raise NotImplementedError
