"""Pydantic models aligned with ``agenty/db-example.json`` Mongo shapes."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class UserSession(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    created_at: datetime = Field(alias="createdAt")
    expires_at: datetime = Field(alias="expiresAt")


class User(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    collection: str
    email: str
    roles: list[str] = Field(default_factory=list)
    sessions: list[UserSession] = Field(default_factory=list)
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class MediaDocument(BaseModel):
    """Media collection placeholder — export has no samples yet."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None


class GeoJsonPoint(BaseModel):
    type: Literal["Point"] = "Point"
    coordinates: tuple[float, float]


class GeoJsonPolygon(BaseModel):
    type: Literal["Polygon"] = "Polygon"
    coordinates: list[list[list[float]]]


class MapLocation(BaseModel):
    lat: float
    lng: float
    geojson: GeoJsonPoint


class MapMetadata(BaseModel):
    location: MapLocation
    address: str
    powiat: str
    gmina: str
    voivodeship: str
    shaft: str | None = None
    section: str | None = None
    risk_area: GeoJsonPolygon | None = None


class IncidentUpdate(BaseModel):
    id: str
    external_id: str
    author_role: str
    content: str
    type: str
    created_at: datetime


class IncidentResource(BaseModel):
    id: str
    resource_id: str
    name: str
    type: str
    status: str
    assigned_at: datetime
    released_at: datetime | None = None


class ScenarioActions(BaseModel):
    """Planned actions keyed by horizon (hours)."""

    h2: list[str] = Field(default_factory=list)
    h12: list[str] = Field(default_factory=list)
    h24: list[str] = Field(default_factory=list)


class Scenario(BaseModel):
    id: str
    label: str
    title: str
    type: str
    estimated_cost: str
    time_to_resolve: str
    is_recommended: bool
    risks: list[str] = Field(default_factory=list)
    benefits: list[str] = Field(default_factory=list)
    consequences_of_inaction: str
    actions: ScenarioActions


class Incident(BaseModel):
    id: str
    external_id: str
    title: str
    type: str
    priority: str
    status: str
    description: str
    affected_population: int
    assigned_to: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None
    map_metadata: MapMetadata
    updates: list[IncidentUpdate] = Field(default_factory=list)
    resources: list[IncidentResource] = Field(default_factory=list)
    scenarios: list[Scenario] = Field(default_factory=list)
    latest_orchestration_run_id: str | None = None
    latest_scenario_version_id: str | None = None


class HierarchyNode(BaseModel):
    role: str
    slug: str
    level: int
    permissions: list[str] = Field(default_factory=list)
    summary: str
    activation: str
    escalation: str
    constraints: list[str] = Field(default_factory=list)
    competencies: list[str] = Field(default_factory=list)
    source_file: str
    children: list[HierarchyNode] = Field(default_factory=list)


class Organization(BaseModel):
    """Org documents mix Mongo-style ``createdAt`` / ``updatedAt`` with snake_case fields."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    external_id: str
    name: str
    slug: str
    type: str
    region: str
    is_active: bool
    audit_created_at: datetime = Field(
        alias="createdAt",
        description="Mongo-style document creation time.",
    )
    audit_updated_at: datetime = Field(
        alias="updatedAt",
        description="Mongo-style document update time.",
    )
    created_at: datetime
    hierarchy: HierarchyNode
    incidents: list[Incident] = Field(default_factory=list)


class CollectionsSnapshot(BaseModel):
    users: list[User] = Field(default_factory=list)
    media: list[MediaDocument] = Field(default_factory=list)
    organizations: list[Organization] = Field(default_factory=list)


class DatabaseExport(BaseModel):
    """Full JSON export wrapper including ``collections``."""

    exported_at: datetime
    collections: CollectionsSnapshot
