"""Local, review-only reported-value links. Reference values never select a target."""
import json
import math
import re
from pydantic import Field
from .models import Model
from .candidates import unit_spec
from .references import catalog
from . import paper_jobs as pj


class Request(Model):
    reference_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    paper_short_id: str = Field(min_length=1,max_length=80)
    expected_revision: int = Field(ge=1)


MODES={'water quenching':'WQ','oil quenching':'OQ','air cooling':'AC','furnace cooling':'FC'}
NUMBER=r'(\d+(?:\.\d+)?)'
MECHANICAL={'tensile_strength','yield_strength','elongation','impact_toughness'}


def conditions(text):
    # Only a single annealing route; reject additional process text rather than ignore it.
    parts=text.split(';');head=parts[0]
    if len(parts)>2 or (len(parts)==2 and not re.fullmatch(r'room-temperature (?:tensile|impact) test(?: per GB/T [\d.]+[-–]\d{4})?',parts[1].strip(),re.I)):
        return None
    m=re.fullmatch(r'Annealed\s+'+NUMBER+r'\s*°C\s*/\s*'+NUMBER+r'\s*h,\s*('
        +'|'.join(MODES)+r')(?:\s*\(~?'+NUMBER+r'\s*°C/s\))?',head.strip(),re.I)
    if not m:return None
    return dict(operation_standardized='annealing',temperature_C=float(m[1]),hold_time_h=float(m[2]),
                cooling_mode=MODES[m[3].lower()],cooling_rate_C_per_s=float(m[4]) if m[4] else None)


def route_agrees(sample,c):
    route=sample['context'].get('processing_route',[])
    if len(route)!=1:return False
    s=route[0];cool=s.get('cooling') or {}
    return (s.get('operation_standardized')==c['operation_standardized']
        and s.get('temperature_C')==c['temperature_C'] and s.get('hold_time_h')==c['hold_time_h']
        and cool.get('mode')==c['cooling_mode']
        and (c['cooling_rate_C_per_s'] is None or cool.get('cooling_rate_C_per_s')==c['cooling_rate_C_per_s']))


def finite(value):return isinstance(value,(float,int)) and not isinstance(value,bool) and math.isfinite(value)


def quantity_key(text):return '_'.join(text.strip().lower().split())


def propose(state,reference,request,pages):
    if reference['paper_short_id']!=request.paper_short_id:raise ValueError('Reference paper ID differs from the supplied paper ID.')
    # The job label is user metadata, not verified bibliographic identity.
    if not (state['paper']==request.paper_short_id or state['paper'].startswith(request.paper_short_id+'-')):
        raise ValueError('Job paper label differs from the supplied paper ID. Do not link different papers.')
    rows=[]
    for r in state['records']:
        c=conditions(r['conditions']);cell=r.get('table_evidence',{})
        row=dict(record_id=r['id'],quantity=r['quantity'],sample_label=r['sample_label'],status='unmatched',
            target_path=None,sample_candidates=[],parsed_conditions=c,conditions=r['conditions'],
            value=r['value'],unit=r['unit'],raw_value=r['raw_value'],
            source=dict(page=r['page'],locator=r['locator'],evidence_quote=r['evidence_quote'],
                        condition_page=r['condition_page'],condition_quote=r['condition_quote'],
                        cell_evidence=cell),original_flags=r['checks'],review_status=r['review_status'],
            missing_checks=['paper and material identity','cooling and complete processing evidence',
                            'test/specimen conditions','uncertainty meaning','human approval of target mapping'])
        rows.append(row)
        if r['review_status']!='needs_review' or r.get('review'):
            row.update(status='reviewed_record_deferred',reason='This adapter links original pending candidates only; retain reviewed corrections.');continue
        if r['kind']!='property' or r['quantity'] not in MECHANICAL or not c:
            row.update(status='unsupported',reason='Requires a supported mechanical quantity and single annealing/cooling route.');continue
        cp=r.get('condition_page');page=next((p for p in pages if p['page']==cp),None)
        quote=r.get('condition_quote','')
        # A located quote must support the shared temperature/time, not just exist somewhere.
        heat=re.fullmatch(r'The samples were heated to '+NUMBER+r'\s*[°◦]C in an electricity-resistant furnace and held for '+NUMBER+r'\s*h',pj.normalize(quote))
        if not page or not pj.matching_stream(page,quote) or not heat or float(heat[1])!=c['temperature_C'] or float(heat[2])!=c['hold_time_h']:
            row.update(status='condition_evidence_unresolved',reason='Temperature/time quote is unlocated or outside the supported grammar.');continue
        labelled=[s for s in reference['samples'] if s['label']==r['sample_label']]
        matches=[s for s in labelled if route_agrees(s,c)]
        row['sample_candidates']=[s['sample_id'] for s in matches]
        if len(matches)!=1:
            row['status']='ambiguous_sample' if len(matches)>1 else 'condition_mismatch' if labelled else 'missing_sample';continue
        s=matches[0];row.update(sample_id=s['sample_id'],reference_sample_context=s['context'])
        targets=[t for t in reference['targets'] if t['kind']=='property' and quantity_key(t['definition'])==r['quantity'] and t['sample_ids']==[s['sample_id']]]
        if len(targets)!=1:
            row['status']='ambiguous_target' if targets else 'missing_target';continue
        t=targets[0];a,b=unit_spec(r['unit']),unit_spec(t['unit'])
        row.update(target_path=t['path'],target_record_id=t['record_id'],reference_definition=t['definition'],reference_value=t['value'],reference_unit=t['unit'],
                   reference_source=t['source'],reference_record_context=t['record_context'])
        if not a or not b or a[0]!=b[0] or not finite(r['value']) or not finite(t['value']):
            row['status']='unit_or_value_unresolved';continue
        converted=r['value']*a[1]/b[1];difference=converted-t['value']
        row.update(value_in_reference_unit=converted,difference=difference,
                   status='mapping_needs_review' if abs(difference)<1e-9 else 'reference_value_conflict')
        if cell.get('status')!='supported':row['status']='cell_evidence_unresolved'
    paths=[r['target_path'] for r in rows if r['target_path']]
    for r in rows:
        if r['target_path'] and paths.count(r['target_path'])>1:r['status']='duplicate_target'
    return dict(schema_version='tifigure-reported-mapping/1',paper_short_id=request.paper_short_id,
        reference_id=request.reference_id,source_pdf_sha256=state['pdf_sha256'],source_revision=state['revision'],
        saved_revision=state['revision']+1,created_at=pj.now(),rows=rows,
        summary={s:sum(r['status']==s for r in rows) for s in sorted({r['status'] for r in rows})},
        model_calls=0,reference_values_used_for_selection=False,
        notice='Pending field links only. No master mutation or mapping approval. Limited annealing/cooling grammar; quoted heat conditions do not verify the entire route.')


def run(root,ident,request,raw):
    if pj.digest(raw)!=request.reference_id:raise ValueError('Reference hash mismatch.')
    reference=catalog(json.loads(raw.decode('utf-8-sig')))
    with pj.LOCK:
        folder=pj.job_path(root,ident);state=pj.read_json(folder/'job.json')
        if state['status']!='awaiting_review' or state['revision']!=request.expected_revision:raise ValueError('Job changed or incomplete; refresh first.')
        if pj.digest((folder/'source.pdf').read_bytes())!=state['pdf_sha256']:raise ValueError('Source PDF changed.')
        result=propose(state,reference,request,pj.read_json(folder/'source/pages.json'))
        dest=folder/'reported-mapping'/f'revision-{result["saved_revision"]:06}'
        dest.mkdir(parents=True,exist_ok=False)
        (dest/'reference-master.json').write_bytes(raw)
        pj.write_json(dest/'mapping.json',result)
        state['reported_mapping']=result;state['revision']+=1
        pj.write_json(folder/'job.json',state);pj.write_outputs(folder,state)
        pj.write_json(dest/'pending-field-preview.json',preview(root,ident))
        return state


def preview(root,ident):
    with pj.LOCK:
        state=pj.snapshot(root,ident);mapping=state.get('reported_mapping')
        if not mapping:raise ValueError('Prepare reported-value mappings first.')
        if mapping['saved_revision']!=state['revision']:raise ValueError('Mapping is stale after a job change. Prepare it again; prior versions remain archived.')
        return dict(artifact_type='tifigure_pending_field_export_preview_not_master',job_id=ident,
            source_revision=state['revision'],paper_short_id=mapping['paper_short_id'],reference_sha256=mapping['reference_id'],
            source_pdf_sha256=state['pdf_sha256'],rows=mapping['rows'],summary=mapping['summary'],
            applicable_patch=[],eligible_fields=0,
            notice='These rows are review proposals, not training-ready records or an executable patch. Uncertainty expressions and source warnings are retained. No master fields are changed.')
