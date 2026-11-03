"""Configured source-only table extraction and separate TiFM reference comparison."""
import hashlib
import json
import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from backend.mechanical_table import parse_mechanical_table, match_reference as match_mechanical
from backend.microstructure_table import parse_microstructure_table, match_reference as match_microstructure
from backend.table_extract import parse_heat_treatment_table, match_sample
from backend.models import ReportedValue


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


class SourceSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    paper: str = Field(pattern=r'^[A-Za-z0-9-]+$')
    pdf: str
    inspected_pdf_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    table: str = Field(min_length=1)
    page: int = Field(gt=0, strict=True)
    bbox: tuple[float, float, float, float]
    parser: Literal['microstructure', 'mechanical', 'heat_treatment_toughness']
    expected_rows: int = Field(gt=0, strict=True)
    header_aliases: dict[str, str] = Field(default_factory=dict)
    interpretation_note: str = Field(min_length=1)

    @model_validator(mode='after')
    def validate_region(self):
        x0, y0, x1, y1 = self.bbox
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError('bbox must be finite ordered normalized coordinates within the page.')
        if self.header_aliases and self.parser != 'mechanical':
            raise ValueError('Header aliases are only supported by the mechanical parser.')
        if not self.pdf.strip():
            raise ValueError('PDF path is required.')
        return self


class ComparisonSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    master: str
    allow_legacy_impact_label: bool = Field(default=False, strict=True)
    source_reviews: list[str] = Field(default_factory=list)


class TableCase(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$', max_length=60)
    source: SourceSpec
    comparison: ComparisonSpec


def contained_path(root, relative):
    """Configuration paths are relative to their explicit root, never implicit cwd."""
    path = Path(relative)
    if path.is_absolute() or path.drive:
        raise ValueError('Use a relative path within the configured root.')
    base = Path(root).resolve()
    resolved = (base/path).resolve()
    if not resolved.is_relative_to(base):
        raise ValueError('Configured path escapes its root.')
    return resolved


def source_signature(spec):
    return sha256(json.dumps(spec.model_dump(), sort_keys=True, ensure_ascii=False).encode('utf-8'))


def parse_source(spec, text):
    if not text or not text.strip():
        raise ValueError('No native PDF text in the region. OCR/manual review is required.')
    if spec.parser == 'microstructure':
        parsed = parse_microstructure_table(text)
    elif spec.parser == 'mechanical':
        parsed = parse_mechanical_table(text, confirmed_header_aliases=spec.header_aliases)
    else:
        records = parse_heat_treatment_table(text)
        parsed = dict(records=records, sample_count=len(records), warnings=[])
    if parsed['sample_count'] != spec.expected_rows:
        raise ValueError('Row count differs from the inspected table; review region/completeness.')
    if any(not math.isfinite(r['value']) or r['value'] < 0 for r in parsed['records']):
        raise ValueError('Nonfinite or negative source quantity.')
    return parsed


def verify_source(spec, source_root):
    path = contained_path(source_root, spec.pdf)
    if sha256(path.read_bytes()) != spec.inspected_pdf_sha256:
        raise ValueError('PDF differs from the inspected source. Review region and interpretation first.')
    return path


def extract_table(spec, source_root):
    """Accept only source specification: no master path or reference values."""
    import pdfplumber
    path = verify_source(spec, source_root)
    with pdfplumber.open(path) as pdf:
        if spec.page > len(pdf.pages):
            raise ValueError('Page is outside the PDF.')
        page = pdf.pages[spec.page-1]
        x0, y0, x1, y1 = spec.bbox
        text = page.crop((x0*page.width, y0*page.height, x1*page.width, y1*page.height)).extract_text()
    verify_source(spec, source_root)
    return dict(schema_version='tifigure-table-extraction/1', artifact_type='independent_source_extraction_draft',
        paper=spec.paper, source_spec=spec.model_dump(mode='json'), source_spec_sha256=source_signature(spec),
        pdf_sha256=spec.inspected_pdf_sha256, reference_supplied_to_extractor=False,
        extractor='configured_native_pdf_tables/1', pdfplumber_version=pdfplumber.__version__,
        raw_table_text=text, **parse_source(spec, text),
        limitations=['Assistant-configured region and supported grammar; not general automatic table detection.',
                    'Native text only. No OCR, model calls or automatic scientific approval.'])


def match_toughness(row, paper, master):
    if master.get('paper_short_id') != paper:
        raise ValueError('Cross-paper reference rejected.')
    sample = match_sample(row, master.get('samples', []))
    result = dict(sample_ids=sample['sample_ids'], target_path=None, status=sample['status'], comparison=None,
                  warnings=[], human_review_required=True, unverified_conditions=sample['unverified_conditions'])
    if sample['status'] != 'unique_reported_conditions_match':
        return result
    targets = [(i, r) for i, r in enumerate(master.get('sample_property_value_records', []))
               if r.get('linked_sample_ids') == sample['sample_ids'] and r.get('property_name') == row['definition']]
    if len(targets) != 1:
        result['status'] = 'target_ambiguous' if targets else 'outside_existing_targets'
        return result
    i, target = targets[0]
    result.update(target_path=f'/sample_property_value_records/{i}/value', status='mapping_needs_review',
                  reference_value=target.get('value'), reference_unit=target.get('unit'))
    if target.get('unit') not in ('Mpa·m^0.5', 'MPa·m^0.5'):
        result['status'] = 'unit_mismatch'
        return result
    value = target.get('value')
    if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value):
        result['status'] = 'non_numeric_reference'
        return result
    result['comparison'] = dict(exact_match=value == row['value'], difference=row['value']-value,
                               scope='table_transcription_only_not_experimental_truth')
    return result


def compare_records(case, extracted, master, reviews=(), master_sha256=None):
    if master.get('paper_short_id') != case.source.paper or extracted['paper'] != case.source.paper:
        raise ValueError('Cross-paper comparison rejected.')
    findings = []
    for review in reviews:
        if review['paper'] != case.source.paper or review['pdf_sha256'] != case.source.inspected_pdf_sha256:
            raise ValueError('Source review belongs to a different paper or PDF.')
        for finding in review['findings']:
            if finding['paper'] != case.source.paper or finding['master_sha256'] != master_sha256:
                raise ValueError('Source review is stale or cross-paper.')
            findings.append(finding)
    records, attached = [], set()
    for row in extracted['records']:
        if case.source.parser == 'mechanical':
            mapping = match_mechanical(row, case.source.paper, master,
                allow_legacy_impact_label=case.comparison.allow_legacy_impact_label)
        elif case.source.parser == 'microstructure':
            mapping = match_microstructure(row, case.source.paper, master)
        else:
            mapping = match_toughness(row, case.source.paper, master)
        evidence = []
        for index, finding in enumerate(findings):
            if finding['target_path'] == mapping['target_path']:
                if set(finding['sample_ids']) != set(mapping['sample_ids']) or finding['source_value'] != row['value'] or finding['source_value'] != mapping.get('reference_value'):
                    raise ValueError('Source finding sample/value binding changed; reinspect it.')
                evidence.append(finding); attached.add(index)
        conflicts = [f for f in evidence if f['finding'] in ('source_body_table_conflict','source_figure_table_conflict')]
        record = dict(extracted=row, mapping=mapping, source_findings=evidence,
            source_conflict=bool(conflicts), review_status='needs_review',
            scope='outside_existing_targets' if mapping['status']=='outside_existing_targets' else 'existing_target_review',
            reported_value_draft=None)
        if mapping['status'] == 'mapping_needs_review':
            note = f'{case.source.paper} {case.source.table}. Full conditions, uncertainty meaning and N require review. '
            note += ' '.join(mapping.get('warnings', []))
            note += ' '.join(f.get('conclusion', f['finding']) for f in conflicts)
            record['reported_value_draft'] = ReportedValue(id=f'table-value-{len(records)+1}',
                quantity=row['definition'], definition=row['definition'], raw_value=row.get('raw_cell', row['raw_row']),
                value_text=row['value_text'], unit=row['unit'], uncertainty_text=row.get('uncertainty_text',''),
                uncertainty_kind=row.get('uncertainty_kind','none'), sample_ids=mapping['sample_ids'],
                target_path=mapping['target_path'], source_class='unknown', scope_note=note).model_dump()
        records.append(record)
    if len(attached) != len(findings):
        raise ValueError('A configured source finding did not bind; do not silently discard it.')
    targets = [r['mapping']['target_path'] for r in records if r['mapping']['target_path'] is not None]
    if len(targets) != len(set(targets)):
        raise ValueError('Multiple source cells map to one target; review duplicate sample/condition bindings.')
    return records


def compare_table(case, extracted_raw, source_root, review_root):
    extracted = json.loads(extracted_raw)
    if extracted.get('schema_version') != 'tifigure-table-extraction/1':
        raise ValueError('Unsupported extraction artifact.')
    if extracted['source_spec_sha256'] != source_signature(case.source) or extracted['source_spec'] != case.source.model_dump(mode='json'):
        raise ValueError('Source configuration changed; rerun extraction.')
    if extracted['pdf_sha256'] != case.source.inspected_pdf_sha256 or extracted['reference_supplied_to_extractor'] is not False:
        raise ValueError('Invalid source provenance.')
    verify_source(case.source, source_root)
    reparsed = parse_source(case.source, extracted['raw_table_text'])
    if any(extracted[k] != reparsed[k] for k in reparsed):
        raise ValueError('Parsed records differ from source text; rerun extraction.')
    master_raw = contained_path(source_root, case.comparison.master).read_bytes()
    reviews, review_hashes = [], {}
    for relative in case.comparison.source_reviews:
        raw = contained_path(review_root, relative).read_bytes()
        reviews.append(json.loads(raw)); review_hashes[relative]=sha256(raw)
    records = compare_records(case, extracted, json.loads(master_raw.decode('utf-8-sig')), reviews, sha256(master_raw))
    return dict(schema_version='tifigure-table-comparison/1', case_id=case.id, paper=case.source.paper,
        source_spec=case.source.model_dump(mode='json'),
        extraction_sha256=sha256(extracted_raw), master_sha256=sha256(master_raw), source_review_hashes=review_hashes,
        comparison_spec=case.comparison.model_dump(), records=records,
        summary=dict(source_values=len(records), mapped_drafts=sum(r['reported_value_draft'] is not None for r in records),
            exact_center_matches=sum(bool(r['mapping']['comparison'] and r['mapping']['comparison']['exact_match']) for r in records),
            outside_existing_targets=sum(r['scope']=='outside_existing_targets' for r in records),
            blocked_mappings=sum(r['reported_value_draft'] is None and r['scope']!='outside_existing_targets' for r in records),
            source_conflicts=sum(r['source_conflict'] for r in records), human_approved=0),
        notice='Draft bundle only; no workspace/master mutation. Matching centers do not establish experimental truth or general accuracy.')
