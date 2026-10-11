"""Pydantic v2 models: CSV row validation (Annex B) and the API contract (Section 6)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --------------------------------------------------------------------------------------
# CSV rows. Column names follow Annex B exactly. Aliases from other naming styles are
# mapped to these names by the loader before validation. Extra columns are allowed.
# --------------------------------------------------------------------------------------

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y", "%m/%d/%Y")


def parse_date(value: Any) -> date:
    """Accept ISO and common day-first formats; always return a date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text[:10] if fmt == "%Y-%m-%d" else text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date '{value}' (use YYYY-MM-DD)")


class _Row(BaseModel):
    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)


class OrderRow(_Row):
    order_id: str = Field(min_length=1)
    product: str = Field(min_length=1)
    qty: int = Field(gt=0)
    destination: str = Field(min_length=1)
    due: date

    @field_validator("due", mode="before")
    @classmethod
    def _parse_due(cls, v: Any) -> date:
        return parse_date(v)


class PlantRow(_Row):
    plant: str = Field(min_length=1)
    product: str = Field(min_length=1)
    weekly_capacity: int = Field(gt=0, description="units per week")
    unit_cost: float = Field(ge=0)
    energy_kwh_per_unit: float = Field(ge=0)
    carbon_kg_per_kwh: float = Field(ge=0, description="kg CO2e per kWh")
    # Optional extras (Section 4.1 mentions an energy price; Annex B has no column for it)
    energy_price_per_kwh: Optional[float] = Field(default=None, ge=0)
    week: Optional[int] = Field(default=None, ge=1, description="1-based planning week, if carbon varies by week")

    @field_validator("weekly_capacity", mode="before")
    @classmethod
    def _int_cap(cls, v: Any) -> int:
        f = float(v)
        if f != int(f):
            raise ValueError("weekly_capacity must be a whole number of units")
        return int(f)


class TransportRow(_Row):
    plant: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    transport_cost_per_unit: float = Field(ge=0)
    transit_days: int = Field(ge=0)
    duty_pct: float = Field(ge=0, le=100, description="import duty as a percentage, e.g. 5 = 5%")
    carbon_kg_per_unit: float = Field(ge=0)


class CapacityEventRow(_Row):
    event_id: str = Field(min_length=1)
    plant: str = Field(min_length=1)
    from_date: date
    to_date: date
    capacity_loss_pct: float = Field(ge=0, le=100, description="share lost: 30 or 0.30 both mean 30%")

    @field_validator("from_date", "to_date", mode="before")
    @classmethod
    def _parse_dates(cls, v: Any) -> date:
        return parse_date(v)

    @model_validator(mode="after")
    def _order(self) -> "CapacityEventRow":
        if self.to_date < self.from_date:
            raise ValueError("to_date is before from_date")
        return self


ROW_MODELS: dict[str, type[_Row]] = {
    "orders": OrderRow,
    "plants": PlantRow,
    "transport_duties": TransportRow,
    "capacity_events": CapacityEventRow,
}

# Alternative header names we accept and map to Annex B names.
COLUMN_ALIASES: dict[str, dict[str, str]] = {
    "orders": {"product_id": "product", "quantity": "qty", "due_date": "due", "order": "order_id"},
    "plants": {"plant_id": "plant", "product_id": "product", "capacity": "weekly_capacity",
               "carbon_intensity": "carbon_kg_per_kwh", "energy_price": "energy_price_per_kwh",
               "energy_per_unit": "energy_kwh_per_unit"},
    "transport_duties": {"plant_id": "plant", "transport_cost": "transport_cost_per_unit",
                         "import_duty": "duty_pct", "duty": "duty_pct",
                         "transport_carbon": "carbon_kg_per_unit"},
    "capacity_events": {"plant_id": "plant", "start_date": "from_date", "end_date": "to_date",
                        "capacity_reduction_pct": "capacity_loss_pct", "loss_pct": "capacity_loss_pct"},
}

# --------------------------------------------------------------------------------------
# API contract (Section 6)
# --------------------------------------------------------------------------------------

AnswerType = Literal["allocation", "scenario", "brief", "infeasible", "clarification_needed", "refused"]


class Weights(BaseModel):
    cost: Optional[float] = Field(default=None, ge=0)
    carbon: Optional[float] = Field(default=None, ge=0)
    lateness: Optional[float] = Field(default=None, ge=0)


class AnalyzeRequest(BaseModel):
    request: str = Field(min_length=1)
    weights: Optional[Weights] = None
    scenario: Optional[dict[str, Any]] = None


class AllocationItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    order_id: str
    plant: Optional[str]
    cost: float
    carbon_kg: float
    arrival: Optional[str]
    on_time: bool


class KPIs(BaseModel):
    model_config = ConfigDict(extra="allow")
    total_cost: float
    total_carbon_t: float
    late_orders: int
    plant_utilisation: dict[str, float]


class ValidatorResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    capacity_ok: bool
    eligibility_ok: bool


class Recommendation(BaseModel):
    action: str
    rationale: str
    evidence_refs: list[str]
    confidence: Literal["high", "medium", "low"]
    estimated_impact: dict[str, float]
    requires_approval: bool


class AnalyzeResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    run_id: str
    answer_type: AnswerType
    summary: str
    allocation: list[AllocationItem] = []
    kpis: Optional[KPIs] = None
    tradeoff: list[dict[str, Any]] = []
    weights_used: dict[str, float] = {}
    validator: Optional[ValidatorResult] = None
    recommendations: list[Recommendation] = []
    assumptions: list[str] = []
    limitations: list[str] = []


class IngestResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    status: Literal["ok", "partial", "rejected"]
    table: str
    rows_loaded: int
    rows_rejected: int
    errors: list[dict[str, Any]] = []
    validation: Optional[dict[str, Any]] = None
