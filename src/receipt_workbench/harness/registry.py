from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import metadata

POINTS = ("invoice_parsers", "page_readers", "validators", "exports")
BUILTINS = {"invoice_parsers": ("cii",), "page_readers": ("pdf_text",),
            "validators": ("schema", "rules", "xrechnung"), "exports": ("json",)}


@dataclass
class Part:
    point: str
    name: str
    distribution: str
    version: str
    implementation: object = field(default=None, repr=False)
    error: str | None = None
    active: bool = False

    def provenance(self) -> dict:
        return {"name": self.name, "distribution": self.distribution, "version": self.version}


class Registry:
    def __init__(self) -> None:
        self.parts: list[Part] = []
        for point in POINTS:
            entries = metadata.entry_points(group=f"receipt_workbench.{point}")
            for entry in sorted(entries, key=lambda item: (
                    item.dist.metadata["Name"].lower().replace("_", "-") != "receipt-workbench",
                    item.name, item.dist.metadata["Name"])):
                part = Part(point, entry.name, entry.dist.metadata["Name"], entry.dist.version)
                try:
                    implementation = entry.load()
                    if isinstance(implementation, type):
                        implementation = implementation()
                    self._add(part, implementation)
                except Exception as error:  # noqa: BLE001 - a broken extension must not hide the other parts
                    part.error = f"{type(error).__name__}: {error}"
                    self.parts.append(part)
                    logging.getLogger(__name__).warning("Cannot load %s.%s (%s %s): %s", point, part.name,
                                                         part.distribution, part.version, part.error)

    def _add(self, part: Part, implementation: object) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", part.name):
            raise ValueError("Part names must contain only letters, digits, underscores or hyphens")
        if not callable(implementation):
            raise TypeError("A part must be callable")
        if part.point == "invoice_parsers" and not getattr(implementation, "flavors", ()):
            raise TypeError("An invoice parser must declare its flavors")
        if any(other.point == part.point and other.name == part.name and not other.error for other in self.parts):
            raise ValueError(f"Duplicate part name: {part.name}")
        part.implementation = implementation
        selected = os.environ.get(f"RW_{part.point.upper()}")
        allowed = None if selected is None else {name.strip() for name in selected.split(",")}
        disabled = {name.strip() for name in os.environ.get(f"RW_DISABLE_{part.point.upper()}", "").split(",")}
        part.active = (allowed is None or part.name in allowed) and part.name not in disabled
        self.parts.append(part)

    def register(self, point: str, name: str, implementation: object, *, distribution: str, version: str) -> None:
        if point not in POINTS:
            raise ValueError(f"Unknown connection point: {point}")
        self._add(Part(point, name, distribution, version), implementation)

    def connected(self, point: str) -> list[Part]:
        def order(part: Part) -> tuple:
            builtin = part.distribution.lower().replace("_", "-") == "receipt-workbench" and part.name in BUILTINS[point]
            return (0, BUILTINS[point].index(part.name)) if builtin else (1, part.name, part.distribution)

        return sorted((part for part in self.parts if part.point == point), key=order)

    def active(self, point: str) -> list[Part]:
        return [part for part in self.connected(point) if part.active and not part.error]

    def describe(self) -> dict:
        return {point: [{**part.provenance(), "active": part.active, "error": part.error}
                        for part in self.connected(point)] for point in POINTS}


@lru_cache(maxsize=1)
def get_registry() -> Registry:
    return Registry()
