from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Point(Model):
    x: float
    y: float


class Axis(Model):
    points: list[Point] = Field(default_factory=list, max_length=2)
    values: list[float] = Field(default_factory=lambda: [0, 1], min_length=2, max_length=2)
    unit: str = Field(default='', max_length=40)
    scale: Literal['linear', 'log10'] = 'linear'


class GraphCalibration(Model):
    x: Axis = Field(default_factory=Axis)
    y: Axis = Field(default_factory=Axis)
    use_y: bool = False


class SpatialCalibration(Model):
    points: list[Point] = Field(default_factory=list, max_length=2)
    length: float = 10
    unit: Literal['nm', 'µm', 'mm'] = 'µm'


class RegionOrigin(Model):
    algorithm: Literal['seed-gray-region/0.1']
    seed: Point
    radius_px: int = Field(ge=16,le=256)
    tolerance: int = Field(ge=1,le=100)
    pixel_count: int = Field(ge=1,le=263169)
    polygon_area_px2: float = Field(gt=0)
    simplification_delta_percent: float = Field(ge=0,le=3)
    warnings: list[str] = Field(default_factory=list,max_length=10)


class Measurement(Model):
    id: str = Field(pattern=r'^[a-zA-Z0-9-]{1,64}$')
    kind: Literal['point', 'line', 'area']
    points: list[Point] = Field(min_length=1, max_length=1000)
    label: str = Field(default='', max_length=200)
    sample: str = Field(default='', max_length=200)
    note: str = Field(default='', max_length=2000)
    status: Literal['needs_review', 'accepted'] = 'needs_review'
    review_basis: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    reference_path: str | None = Field(default=None, max_length=400)
    acquisition: Literal['manual','peak_assist','region_assist','model_vision'] = 'manual'
    group_label: str = Field(default='',max_length=200)
    include_in_summary: bool = False
    exclusion_reason: str = Field(default='',max_length=500)
    region_origin: RegionOrigin | None = None
    plan_id: str | None = Field(default=None,pattern=r'^[a-zA-Z0-9-]{1,64}$')


class Candidate(Model):
    id: str = Field(pattern=r'^[a-zA-Z0-9-]{1,64}$')
    source_kind: Literal['measurement','summary']
    measurement_id: str = Field(default='',max_length=64)
    sample: str = Field(default='',max_length=200)
    group: str = Field(default='',max_length=200)
    metric: Literal['x','y','length','area','ecd','mean_area','mean_ecd','median_ecd']
    target_path: str | None = Field(default=None,max_length=400)
    sample_ids: list[str] = Field(default_factory=list,max_length=100)
    definition: str = Field(default='',max_length=500)
    evidence: str = Field(default='',max_length=2000)
    sample_checked: bool = False
    definition_checked: bool = False
    unit_checked: bool = False
    source_checked: bool = False
    review_basis: str | None = Field(default=None,pattern=r'^[0-9a-f]{64}$')


class Evidence(Model):
    id: str = Field(pattern=r'^[a-zA-Z0-9-]{1,64}$')
    kind: Literal['caption','body','table','methods','user_note'] = 'caption'
    locator: str = Field(default='',max_length=500)
    text: str = Field(default='',max_length=12000)
    review_basis: str | None = Field(default=None,pattern=r'^[0-9a-f]{64}$')


class ModelProvenance(Model):
    provider: str = Field(max_length=100)
    model: str = Field(max_length=100)
    protocol: Literal['responses','chat_completions']
    import_kind: Literal['manual_fixture','pasted_unverified','live_api']
    request_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    response_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    prompt_version: str = Field(max_length=100)
    request_context_json: str = Field(max_length=60000)
    response_json: str = Field(max_length=50000)
    proposal_index: int = Field(ge=0,le=9)
    network_executed: bool = False
    usage: dict[str, int] = Field(default_factory=dict)
    response_model: str = Field(default='', max_length=200)
    called_at: str = Field(default='', max_length=80)


class ExtractionPlan(Model):
    id: str = Field(pattern=r'^[a-zA-Z0-9-]{1,64}$')
    origin: Literal['manual','model_response_import'] = 'manual'
    model_provenance: ModelProvenance | None = None
    task: Literal['peak_position','graph_value','length','object_area','mean_ecd','reported_value'] = 'peak_position'
    tool: Literal['point','peak','line','area','region','transcription'] = 'point'
    panel: str = Field(default='',max_length=300)
    object_identity: str = Field(default='',max_length=200)
    definition: str = Field(default='',max_length=1000)
    sample_ids: list[str] = Field(default_factory=list,max_length=100)
    evidence_ids: list[str] = Field(default_factory=list,max_length=100)
    image_quality: Literal['unassessed','usable','needs_better_source','not_measurable'] = 'unassessed'
    quality_note: str = Field(default='',max_length=1000)
    sampling_rule: str = Field(default='',max_length=1000)
    stop_conditions: str = Field(default='',max_length=1000)
    review_basis: str | None = Field(default=None,pattern=r'^[0-9a-f]{64}$')


class ReportedValue(Model):
    id: str = Field(pattern=r'^[a-zA-Z0-9-]{1,64}$')
    quantity: str = Field(default='', max_length=200)
    definition: str = Field(default='', max_length=1000)
    raw_value: str = Field(default='', max_length=1000)
    value_kind: Literal['scalar','range','text'] = 'scalar'
    qualifier: Literal['eq','approx','gt','ge','lt','le'] = 'eq'
    value_text: str = Field(default='', max_length=100)
    upper_text: str = Field(default='', max_length=100)
    unit: str = Field(default='', max_length=80)
    unit_status: Literal['reported','not_reported','dimensionless'] = 'reported'
    uncertainty_text: str = Field(default='', max_length=500)
    uncertainty_kind: Literal['none','sd','se','ci','unspecified','other'] = 'none'
    n_text: str = Field(default='', max_length=20)
    scope_note: str = Field(default='', max_length=1000)
    source_class: Literal['experimental','derived','predicted','cited','unknown'] = 'unknown'
    sample_ids: list[str] = Field(default_factory=list,max_length=100)
    evidence_ids: list[str] = Field(default_factory=list,max_length=100)
    target_path: str | None = Field(default=None,max_length=400)
    plan_id: str | None = Field(default=None,pattern=r'^[a-zA-Z0-9-]{1,64}$')
    source_checked: bool = False
    sample_checked: bool = False
    quantity_checked: bool = False
    value_checked: bool = False
    review_basis: str | None = Field(default=None,pattern=r'^[0-9a-f]{64}$')


class Document(Model):
    schema_version: Literal['tifigure-workspace/0.7'] = 'tifigure-workspace/0.7'

    @field_validator('schema_version',mode='before')
    @classmethod
    def migrate_workspace(cls,value):
        return 'tifigure-workspace/0.7' if value in tuple(f'tifigure-workspace/0.{v}' for v in range(1,7)) else value
    name: str = Field(default='Untitled project', min_length=1, max_length=200)
    image_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    mode: Literal['graph', 'micrograph'] = 'graph'
    source: str = Field(default='', max_length=2000)
    active_sample: str = Field(default='', max_length=200)
    active_group: str = Field(default='',max_length=200)
    graph: GraphCalibration = Field(default_factory=GraphCalibration)
    spatial: SpatialCalibration = Field(default_factory=SpatialCalibration)
    measurements: list[Measurement] = Field(default_factory=list, max_length=10000)
    reference_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    candidates: list[Candidate] = Field(default_factory=list,max_length=1000)
    evidence: list[Evidence] = Field(default_factory=list,max_length=200)
    plans: list[ExtractionPlan] = Field(default_factory=list,max_length=100)
    reported_values: list[ReportedValue] = Field(default_factory=list,max_length=1000)
    active_plan_id: str | None = Field(default=None,pattern=r'^[a-zA-Z0-9-]{1,64}$')

    @field_validator('candidates')
    @classmethod
    def unique_candidates(cls,values):
        if len({v.id for v in values})!=len(values):raise ValueError('Duplicate candidate IDs.')
        return values

    @field_validator('evidence','plans','reported_values')
    @classmethod
    def unique_entries(cls,values):
        if len({v.id for v in values})!=len(values):raise ValueError('Duplicate entry IDs.')
        return values


class SaveRequest(Model):
    expected_revision: int = Field(ge=0)
    document: Document


class PeakRequest(Model):
    image_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    point: Point
    radius: int = Field(default=40,ge=12,le=80)


class RegionRequest(Model):
    image_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    point: Point
    radius: int = Field(default=80,ge=16,le=256)
    tolerance: int = Field(default=24,ge=1,le=100)
