"""The intermediate recipe shape every extractor fills in."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Recipe:
    title: str = ""
    description: str = ""
    ingredients: list[str] = field(default_factory=list)  # "# Heading" lines start a group
    instructions: list[str] = field(default_factory=list)
    notes: str = ""
    yield_: str = ""
    prep_time: str = ""
    cook_time: str = ""
    total_time: str = ""
    nutrition: str = ""
    link: str = ""
    image_urls: list[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return bool(self.ingredients) and bool(self.instructions)
