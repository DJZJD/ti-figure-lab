"""Explicit adoption into a TiFM copy; original master and evidence remain immutable."""
import copy
import hashlib
import io
import json
import math
import re
import zipfile
from datetime import datetime, timezone
from typing import Literal
from pydantic import Field
from .models import Model
from .references import catalog
from .table_pipeline import TableCase, contained_path, verify_source


class Adoption(Model):
    key: str = Field(min_length=1,max_length=100)
    reason: str = Field(min_length=1,max_length=2000)
    retained_metadata_checked: Literal[True]


class MasterCopyRequest(Model):
    preview_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    selections: list[Adoption] = Field(default_factory=list,max_length=1000)
    reviewer: str = Field(min_length=1,max_length=200)


def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False).encode('utf-8')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def numeric(value):
    return isinstance(value,(float,int)) and not isinstance(value,bool) and math.isfinite(value)


def leaf(data,path):
    """Only existing supported numeric fields, never arbitrary JSON writes."""
    if not re.fullmatch(r'/(?:sample_property_value_records|microstructure_quantification_records)/\d+/value|/xrd_records/\d+/peaks/\d+/(?:two_theta_deg|normalized_intensity)',path):
        raise ValueError('Unsupported target path.')
    node=data
    parts=path.strip('/').split('/')
    for part in parts[:-1]:node=node[int(part)] if isinstance(node,list) else node[part]
    return node,parts[-1]


def source_findings(root,source_root,master_raw):
    master=json.loads(master_raw.decode('utf-8-sig'));findings=[];seen=set();hashes={}
    config=json.loads((root/'configs/table-cases.json').read_text(encoding='utf-8'))
    if config['schema_version']!='tifigure-table-cases/1':raise ValueError('Unsupported case registry.')
    for raw in config['cases']:
        case=TableCase.model_validate(raw)
        if case.source.paper!=master.get('paper_short_id'):continue
        for path in case.comparison.source_reviews:
            if path in seen:continue
            seen.add(path);verify_source(case.source,source_root)
            raw_review=contained_path(root,path).read_bytes();hashes[path]=digest(raw_review)
            review=json.loads(raw_review)
            if review['paper']!=case.source.paper or review['pdf_sha256']!=case.source.inspected_pdf_sha256:
                raise ValueError('Source conflict review is stale; inspect it before export.')
            for finding in review['findings']:
                if finding['master_sha256']!=digest(master_raw) or finding['paper']!=case.source.paper:
                    raise ValueError('Registered source review belongs to a different master revision.')
                if finding['finding'] in ('source_body_table_conflict','source_figure_table_conflict'):
                    targets=[t for t in catalog(master)['targets'] if t['path']==finding['target_path']]
                    if len(targets)!=1 or set(targets[0]['sample_ids'])!=set(finding['sample_ids']) or targets[0]['value']!=finding['source_value']:
                        raise ValueError('Conflict target binding changed; inspect the reference.')
                    findings.append(finding)
    return findings,hashes


def prepare(data,master_raw,findings=(),finding_hashes=None):
    if digest(master_raw)!=data['document']['reference_id']:
        raise ValueError('Reference bytes no longer match the workspace.')
    master=json.loads(master_raw.decode('utf-8-sig'));index=catalog(master)
    targets={t['path']:t for t in index['targets']}
    conflicts={f['target_path'] for f in findings}
    rows=[]
    for kind,results in [('reported',data['results']['reported_values']),('candidate',data['results']['candidates'])]:
        for result in results:
            target=result.get('target');path=target['path'] if target else None
            blockers=list(result['blockers']);value=None
            if kind=='reported':
                record=result['record']
                if result['status']!='reviewed':blockers.append('Reported value needs current review.')
                if record['value_kind']!='scalar' or record['qualifier']!='eq':blockers.append('Only exact scalar values can replace an existing numeric field; ranges and qualifiers stay in attachments.')
                if record['source_class']!='experimental':blockers.append('This adapter only adopts this-work experimental reports; other origins stay in attachments.')
                if result.get('comparison'):value=result['comparison']['reported_value_in_target_unit']
                label=record['quantity']
            else:
                if result['status']!='mapping_checked':blockers.append('Measurement and field mapping need current review.')
                if result.get('conversion'):value=result['conversion']['value']
                label=result['selection']['definition']
            if not path or path not in targets:blockers.append('No supported existing target field.')
            elif not numeric(targets[path]['value']):blockers.append('The existing field is not a finite numeric scalar.')
            if not numeric(value):blockers.append('No finite value in the target unit is available.')
            if path in conflicts:blockers.append('Registered source conflict is unresolved. Keep the existing field and both sources; no replacement is allowed.')
            rows.append(dict(key=kind+':'+result['id'],kind=kind,source_id=result['id'],label=label,path=path,
                sample_ids=target['sample_ids'] if target else [],old_value=target['value'] if target else None,
                new_value=value,unit=target['unit'] if target else None,
                retained_metadata=target['record_context'] if target else None,eligible=not blockers,
                blockers=list(dict.fromkeys(blockers)),source_result=result))
    summary=dict(total=len(rows),eligible=sum(r['eligible'] for r in rows),blocked=sum(not r['eligible'] for r in rows),
                 known_conflicting_fields=len(conflicts))
    core=dict(project_id=data['id'],revision=data['revision'],reference_sha256=digest(master_raw),
        document=data['document'],rows=rows,source_findings=list(findings),source_finding_hashes=finding_hashes or {})
    return dict(schema_version='tifigure-master-copy-preview/1',project_id=data['id'],revision=data['revision'],
        paper=index['paper_short_id'],reference_sha256=digest(master_raw),preview_hash=digest(encoded(core)),
        rows=rows,summary=summary,source_findings=list(findings),source_finding_hashes=finding_hashes or {},
        notice='Only explicitly selected, currently reviewed scalar fields may change. Original values elsewhere, including unresolved conflicts, are retained without revalidation. Export is not a training-readiness certification.')


def build_bundle(data,master_raw,preview,request):
    if request.preview_hash!=preview['preview_hash']:raise ValueError('Preview is stale; refresh before exporting.')
    if not request.reviewer.strip():raise ValueError('Enter a reviewer name or local identifier.')
    original=json.loads(master_raw.decode('utf-8-sig'));modified=copy.deepcopy(original)
    available={r['key']:r for r in preview['rows']};seen_keys=set();seen_paths=set();changes=[]
    for selection in request.selections:
        row=available.get(selection.key)
        if row is None or not row['eligible']:raise ValueError('A selected result is blocked or missing; review it first.')
        if selection.key in seen_keys or row['path'] in seen_paths:raise ValueError('Select only one result for each target field.')
        if not selection.reason.strip():raise ValueError('Each adoption requires a reason.')
        seen_keys.add(selection.key);seen_paths.add(row['path'])
        parent,key=leaf(modified,row['path']);old=copy.deepcopy(parent[key]);value=row['new_value']
        if old!=row['old_value']:raise ValueError('Target value changed since preview.')
        if isinstance(old,int) and float(value).is_integer():value=int(value)
        parent[key]=value
        changes.append(dict(key=selection.key,path=row['path'],old_value=old,new_value=value,unit=row['unit'],
            numeric_change=old!=value,reviewer=request.reviewer.strip(),reason=selection.reason.strip(),
            retained_metadata_checked=True,sample_ids=row['sample_ids'],source_result=row['source_result']))
    # Re-read the produced JSON and restore selected leaves. Everything else must equal the original.
    master_bytes=encoded(modified)
    restored=json.loads(master_bytes)
    before=catalog(original);after=catalog(restored)
    if [t['path'] for t in before['targets']]!=[t['path'] for t in after['targets']] or before['samples']!=after['samples'] or before['warnings']!=after['warnings']:
        raise ValueError('Structural round-trip changed target paths, samples or indexing warnings.')
    for change in changes:
        parent,key=leaf(restored,change['path']);parent[key]=change['old_value']
    if encoded(restored)!=encoded(original):raise ValueError('An unselected field changed; export rejected.')
    timestamp=datetime.now(timezone.utc).isoformat()
    audit=dict(schema_version='tifigure-master-copy-audit/1',artifact_type='explicit_adoption_copy_not_training_certification',
        created_at=timestamp,project_id=data['id'],project_revision=data['revision'],reviewer=request.reviewer.strip(),
        original_sha256=digest(master_raw),exported_sha256=digest(master_bytes),preview_hash=preview['preview_hash'],
        adopted_fields=changes,known_source_findings=preview['source_findings'],source_finding_hashes=preview['source_finding_hashes'],
        validation=dict(json_readback=True,target_paths_preserved=True,samples_preserved=True,unselected_fields_unchanged=True,
            original_index_warnings=before['warnings'],downstream_reader_tested=False,complete_schema_validated=False),
        limits=['Only existing finite numeric scalar fields; no new records, metadata rewrites or uncertainty conversions.',
               'Retained source, uncertainty, statistics and test metadata must still apply to each selected value.',
               'Unselected original fields and pre-existing conflicts are copied, not verified by this export.'])
    pending=dict(artifact_type='unadopted_results_and_evidence',
        unadopted=[r for r in preview['rows'] if r['key'] not in seen_keys],source_findings=preview['source_findings'],
        document=data['document'],evaluation=data['results'],image=data['image'],reference=data['reference'])
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('master-copy.json',master_bytes)
        archive.writestr('original-master.json',master_raw)
        archive.writestr('adoption-audit.json',encoded(audit))
        archive.writestr('pending-review.json',encoded(pending))
        archive.writestr('NOTICE.txt','This is a copy with explicitly adopted scalar values. Keep this entire bundle for provenance. Unchanged original data is not newly validated. Known conflicts remain in the original fields and pending-review.json. Downstream training-reader compatibility has not been tested.\n')
    with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive:
        if archive.testzip() is not None or archive.read('master-copy.json')!=master_bytes:raise ValueError('Bundle readback failed.')
    return buffer.getvalue(),audit
