from abc import ABC, abstractmethod

from src.database.models import Job


class BaseScraper(ABC):
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def fetch_jobs(self, keywords: list[str], locations: list[str], limit: int = 20) -> list[Job]:
        """
        Extrae vacantes de la fuente específica.

        Args:
            keywords: Lista de palabras clave a buscar (ej: ["Data Engineer"]).
            locations: Lista de ubicaciones a filtrar (ej: ["Remote"]).
            limit: Límite aproximado de vacantes a retornar.

        Returns:
            List[Job]: Lista de objetos Job listos para su persistencia.
        """
