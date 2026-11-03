"""Read registered local table batches and build evidence-linked, unapproved projects."""
import json
import re
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
from .models import Document, Evidence, ReportedValue
from .table_pipeline import TableCase, compare_table, contained_path, sha256


class BatchImportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    preview_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


def cases(root):
    raw = json.loads((root/'configs/table-cases.json').read_text(encoding='utf-8'))
    if raw['schema_version'] != 'tifigure-table-cases/1':
        raise ValueError('Unsupported table configuration.')
    entries = [TableCase.model_validate(c) for c in raw['cases']]
    if len({c.id for c in entries}) != len(entries):
        raise ValueError('Duplicate table case IDs.')
    return entries


def load_batch(root, source_root, case_id):
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', case_id):
        raise ValueError('Unknown registered table case.')
    found = [c for c in cases(root) if c.id == case_id]
    if len(found) != 1:
        raise ValueError('Unknown registered table case.')
    case = found[0]
    folder = root/'outputs/table-batch'
    # Failed reruns must not silently resurrect an older artifact in the GUI.
    for stage in ('extract','compare'):
        manifest_path = folder/(stage+'-batch.json')
        if manifest_path.exists():
            manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
            if any(i['case_id']==case_id and i['status']!='ok' for i in manifest['items']):
                raise ValueError('Latest batch run failed for this table. Rerun the CLI before importing.')
    extraction_raw=(folder/(case_id+'-extraction.json')).read_bytes()
    comparison_raw=(folder/(case_id+'-comparison.json')).read_bytes()
    stored=json.loads(comparison_raw)
    recomputed=compare_table(case, extraction_raw, source_root, root)
    if stored != recomputed:
        raise ValueError('Batch inputs or comparison changed. Rerun comparison and refresh this preview.')
    registry=json.loads((root/'configs/table-workspaces.json').read_text(encoding='utf-8'))
    registered_ids=[str(UUID(a['project_id'])) for a in registry.values()]
    if len(registered_ids)!=len(set(registered_ids)):
        raise ValueError('Each table case requires a distinct registered project ID.')
    assets=registry[case_id]
    project_id=str(UUID(assets['project_id']))
    image_path=contained_path(root, assets['image'])
    image_raw=image_path.read_bytes()
    if sha256(image_raw)!=assets['image_sha256']:
        raise ValueError('Registered source-page image changed. Recheck its source before importing.')
    master_raw=contained_path(source_root,case.comparison.master).read_bytes()
    if sha256(master_raw)!=stored['master_sha256']:
        raise ValueError('Reference changed during preview; refresh.')
    preview_hash=sha256(comparison_raw+extraction_raw+image_raw+master_raw+
        json.dumps(assets,sort_keys=True).encode())
    return dict(case=case, comparison=stored, extraction=json.loads(extraction_raw),
        project_id=project_id, image_path=image_path, image_raw=image_raw, master_raw=master_raw,
        image_sha256=assets['image_sha256'], preview_hash=preview_hash)


def preview(batch):
    return dict(case_id=batch['case'].id, project_id=batch['project_id'], preview_hash=batch['preview_hash'],
        source=batch['comparison']['source_spec'], summary=batch['comparison']['summary'],
        records=batch['comparison']['records'], raw_table_text=batch['extraction']['raw_table_text'])


def build_document(batch, image_id, reference_id):
    case=batch['case'];extracted=batch['extraction'];comparison=batch['comparison']
    evidence=[Evidence(id='batch-source',kind='table',locator=f'{case.source.paper} {case.source.table}; PDF page {case.source.page}',
                      text=extracted['raw_table_text']),
              Evidence(id='batch-interpretation',kind='user_note',locator='Configured interpretation; pending human review',
                       text=case.source.interpretation_note)]
    reports=[]
    for index, row in enumerate(comparison['records']):
        if row['reported_value_draft'] is None:
            continue
        report=ReportedValue.model_validate(row['reported_value_draft'])
        report.evidence_ids=['batch-source','batch-interpretation']
        for j, finding in enumerate(row['source_findings']):
            ident=f'finding-{index}-{j}'
            evidence.append(Evidence(id=ident,kind='user_note',locator=finding.get('source_locator','Source review'),
                                     text=json.dumps(finding,ensure_ascii=False)))
            report.evidence_ids.append(ident)
        # Import is never approval, even if a stored artifact is hand-edited.
        report.review_basis=None
        report.source_checked=report.sample_checked=report.quantity_checked=report.value_checked=False
        reports.append(report)
    if not reports:
        raise ValueError('This table has no importable mapped drafts.')
    return Document(name=f'{case.source.paper} {case.source.table} - Batch import (review required)',
        image_id=image_id, reference_id=reference_id, evidence=evidence, reported_values=reports,
        source=f'{case.source.paper} {case.source.table}; PDF page {case.source.page}. Source SHA256: {case.source.inspected_pdf_sha256}. Batch: {case.id}. All records require review.')
