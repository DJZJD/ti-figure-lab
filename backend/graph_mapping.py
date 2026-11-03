"""Conservative local graph-to-TiFM proposals; no reference-value fitting."""
import hashlib
import json
import math
import re
from pydantic import Field
from .models import Model, Candidate
from .candidates import unit_spec


class MappingRequest(Model):
    reference_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    paper_short_id: str = Field(min_length=1, max_length=80)
    expected_revision: int = Field(ge=1)
    time_tolerance_h: float = Field(default=0.02, gt=0, le=0.1, allow_inf_nan=False)


def conditions(label):
    # A deliberately small grammar. Unrecognized or multiple treatments defer.
    n=r'(\d+(?:\.\d+)?)'
    st=re.findall(r'Solution treatment\s*:\s*'+n+r'\s*°C\s*[×x]\s*'+n+r'\s*h',label,re.I)
    at=re.findall(r'Aging temperature\s*:\s*'+n+r'\s*°C',label,re.I)
    if len(st)!=1 or len(at)!=1:return None
    return dict(solution_temperature_C=float(st[0][0]),solution_time_h=float(st[0][1]),aging_temperature_C=float(at[0]))


def propose(document, evaluation, plot, reference, request, findings=()):
    if reference['paper_short_id']!=request.paper_short_id:
        raise ValueError('Paper identity differs from the selected master.')
    if document.reference_id and document.reference_id!=request.reference_id:
        raise ValueError('Workspace already uses another reference; do not replace it automatically.')
    if document.candidates:raise ValueError('Workspace already contains supplements; retain them and map additional fields manually.')
    if re.sub(r'[^a-z]','',plot['x_axis']['title'].lower())!='agingtime' or re.sub(r'[^a-z]','',plot['y_axis']['title'].lower())!='kic':
        raise ValueError('This adapter requires an Aging time / KIC graph. Other quantities remain available for manual mapping.')
    xs,ys=unit_spec(document.graph.x.unit),unit_spec(document.graph.y.unit)
    if not xs or xs[0]!='time' or not ys or ys[0]!='toughness':raise ValueError('Unsupported time or toughness unit.')
    values={r['id']:r for r in evaluation['measurements']}
    rows=[];candidates=[]
    for m in document.measurements:
        r=values[m.id];c=conditions(m.sample)
        row=dict(measurement_id=m.id,status='unmatched',conditions=c,sample_candidates=[],target_path=None,
                 source_label=m.sample,missing_checks=['material identity','cooling route','test conditions','KIC validity','source and geometry review'])
        rows.append(row)
        if not c or m.kind!='point' or not r['valid'] or not all(k in r['values'] for k in ('x','y')):
            row['reason']='Unsupported or incomplete conditions / point geometry.';continue
        x=r['values']['x']*xs[1];y=r['values']['y'];row.update(digitized_time_h=x,value=y,unit=document.graph.y.unit)
        for s in reference['samples']:
            route=s['context'].get('processing_route',[])
            if len(route)!=2:continue
            st,at=route
            time=at.get('hold_time_h')
            if (st.get('operation_standardized')=='solution_treatment' and at.get('operation_standardized')=='aging'
                and st.get('temperature_C')==c['solution_temperature_C'] and st.get('hold_time_h')==c['solution_time_h']
                and at.get('temperature_C')==c['aging_temperature_C'] and isinstance(time,(int,float)) and not isinstance(time,bool)
                and math.isfinite(time) and abs(time-x)<=request.time_tolerance_h):
                row['sample_candidates'].append(dict(sample_id=s['sample_id'],nominal_time_h=time,time_difference_h=x-time,context=s['context']))
        if len(row['sample_candidates'])!=1:
            row['status']='ambiguous' if row['sample_candidates'] else 'unmatched';continue
        sid=row['sample_candidates'][0]['sample_id']
        targets=[t for t in reference['targets'] if t['kind']=='property' and t['definition']=='fracture_toughness' and t['sample_ids']==[sid]]
        if len(targets)!=1:
            row['status']='ambiguous_target' if targets else 'missing_target';continue
        t=targets[0];ts=unit_spec(t['unit'])
        if not ts or ts[0]!=ys[0]:row['status']='unit_mismatch';continue
        row.update(status='mapping_needs_review',target_path=t['path'],sample_ids=[sid],reference_value=t['value'],reference_unit=t['unit'],reference_source=t['source'])
        converted=y*ys[1]/ts[1]
        if isinstance(t['value'],(int,float)) and not isinstance(t['value'],bool) and math.isfinite(t['value']):
            row['difference_in_reference_unit']=converted-t['value']
        row['source_conflicts']=[f for f in findings if f['target_path']==t['path']]
        evidence=json.dumps(dict(conditions=c,digitized_time_h=x,nominal_time_h=row['sample_candidates'][0]['nominal_time_h'],
            time_tolerance_h=request.time_tolerance_h,missing_checks=row['missing_checks'],conflict=bool(row['source_conflicts'])),ensure_ascii=False)
        candidates.append(Candidate(id='gm-'+hashlib.sha256(m.id.encode()).hexdigest()[:24],source_kind='measurement',measurement_id=m.id,
            metric='y',target_path=t['path'],sample_ids=[sid],definition='Digitized KIC graph marker, not a new test or author-reported table value.',evidence=evidence))
    # Two points cannot silently become competing replacements for one field.
    paths=[c.target_path for c in candidates]
    duplicate={p for p in paths if paths.count(p)>1}
    candidates=[c for c in candidates if c.target_path not in duplicate]
    for row in rows:
        if row['target_path'] in duplicate:row['status']='duplicate_target'
    return dict(schema_version='tifigure-graph-mapping/1',paper_short_id=request.paper_short_id,reference_id=request.reference_id,
        time_tolerance_h=request.time_tolerance_h,rows=rows,proposed_fields=len(candidates),
        notice='Condition-based suggestions, not verified sample identity. Reference values are compared only after matching. No automatic acceptance or master replacement.'),candidates
