"""Auditable supplements anchored to a read-only master, never master patches."""
import hashlib
import json
import math


def unit_spec(unit):
    # Explicit aliases only. Preserve both original strings in the output.
    units={'nm':('length',1e-3),'µm':('length',1),'μm':('length',1),'um':('length',1),'mm':('length',1e3),
           'nm²':('area',1e-6),'µm²':('area',1),'μm²':('area',1),'um^2':('area',1),'mm²':('area',1e6),
           'deg':('angle',1),'°':('angle',1),'MPa':('stress',1),'Mpa':('stress',1),'GPa':('stress',1e3),'%':('percent',1),'J':('energy',1),
           'h':('time',1),'min':('time',1/60),'s':('time',1/3600)}
    for alias in ('Mpa·m^0.5','MPa·m^0.5','MPa m^0.5','MPa*sqrt(m)','MPa√m','MPa·m½','MPa m1/2','MPa m^1/2','MPa·√m'):
        units[alias]=('toughness',1)
    return units.get(unit)


def candidate_results(doc,results,reference):
    targets={t['path']:t for t in reference['targets']} if reference else {}
    known_samples={s['sample_id'] for s in reference['samples']} if reference else set()
    measured={r['id']:r for r in results['measurements']}
    records={m.id:m for m in doc.measurements}
    output=[]
    for c in doc.candidates:
        blockers=[];value=None;unit=None;source=None;members=[]
        if c.source_kind=='measurement':
            m=records.get(c.measurement_id);r=measured.get(c.measurement_id)
            if not m or not r: blockers.append('Source measurement is missing.')
            else:
                members=[{'measurement':m.model_dump(),'computed':r}]
                source={'kind':'single_object' if m.kind!='point' else 'digitized_graph_point','sample_label':m.sample,'metric':c.metric}
                if c.metric not in r['values']:blockers.append('Selected metric is unavailable for this measurement.')
                else:value=r['values'][c.metric];unit=r['units'][c.metric]
                if not r['valid'] or m.status!='accepted' or m.review_basis!=r['review_basis']:
                    blockers.append('Source geometry needs current review.')
        else:
            g=next((g for g in results['summary']['groups'] if g['sample']==c.sample and g['group']==c.group),None)
            source={'kind':'selected_object_summary','sample_label':c.sample,'group':c.group,'metric':c.metric,'summary':g}
            if not g or not g['valid']:blockers.append('Source summary is missing or withheld.')
            elif c.metric not in ('mean_area','mean_ecd','median_ecd'):blockers.append('Unsupported summary metric.')
            else:
                value=g[c.metric];unit=g['area_unit'] if c.metric=='mean_area' else g['length_unit']
                members=[{'measurement':records[i].model_dump(),'computed':measured[i]} for i in sorted(g['member_ids'])]
        target=targets.get(c.target_path) if c.target_path else None
        if not reference:blockers.append('Import a reference master to anchor paper and sample IDs.')
        if not c.sample_ids or len(set(c.sample_ids))!=len(c.sample_ids) or any(s not in known_samples for s in c.sample_ids):
            blockers.append('Select unique existing sample IDs from this master.')
        if not target:blockers.append('No existing target field is mapped. Keep this as an unmapped supplement.')
        elif set(c.sample_ids)!=set(target['sample_ids']):blockers.append('Sample IDs must match the complete target sample set.')
        if not c.definition.strip():blockers.append('Describe the measured quantity and its statistical scope.')
        if not c.evidence.strip() or not doc.source.strip():blockers.append('Provide source and mapping evidence.')
        converted=None;factor=None;comparison=None
        if target and value is not None:
            a,b=unit_spec(unit),unit_spec(target['unit'])
            if not a or not b or a[0]!=b[0]:blockers.append('Units are incompatible or not in the explicit conversion registry.')
            else:
                factor=a[1]/b[1];converted=value*factor
                if not math.isfinite(converted):converted=None;blockers.append('Converted value exceeds the numeric range.')
            if target['kind']=='xrd' and (not target['path'].endswith('/two_theta_deg') or c.source_kind!='measurement' or c.metric!='x' or not source or source['kind']!='digitized_graph_point'):
                blockers.append('XRD mapping supports only a graph X coordinate to two_theta_deg; normalized intensity is unsupported.')
            if target['kind']=='property' and (c.source_kind!='measurement' or c.metric!='y' or not source or source['kind']!='digitized_graph_point'):
                blockers.append('Property mapping currently requires a graph Y coordinate, with conditions verified from X and context.')
            if target['kind']=='microstructure':
                if c.source_kind!='summary':blockers.append('A single object cannot represent a microstructure aggregate in this adapter.')
                if b and b[0]=='percent':blockers.append('Object sizes and areas cannot be mapped to phase fractions.')
            old=target['value']
            if converted is not None and isinstance(old,(float,int)) and not isinstance(old,bool) and math.isfinite(old):
                difference=converted-old
                relative=abs(difference)/abs(old)*100 if old else None
                comparison={'existing_value':old,'candidate_value_in_target_unit':converted,'signed_difference':difference if math.isfinite(difference) else None,
                            'absolute_difference':abs(difference) if math.isfinite(difference) else None,'relative_difference_percent':relative if relative is not None and math.isfinite(relative) else None,
                            'notice':'Arithmetic only; no acceptance threshold. Existing master values are not necessarily author-reported.'}
        if not all((c.sample_checked,c.definition_checked,c.unit_checked,c.source_checked)):
            blockers.append('Complete all four mapping checks; similar numbers do not establish equivalence.')
        sample_context=[s for s in reference['samples'] if s['sample_id'] in c.sample_ids] if reference else []
        basis_data={'candidate':c.model_dump(exclude={'review_basis'}),'reference_sha256':doc.reference_id,'target':target,'sample_context':sample_context,
                    'image_sha256':doc.image_id,'source_reference':doc.source,'source':source,'members':members}
        basis=hashlib.sha256(json.dumps(basis_data,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()
        checks_current=c.review_basis==basis
        status='blocked' if blockers else 'mapping_checked' if checks_current else 'needs_mapping_review'
        output.append({'id':c.id,'status':status,'blockers':blockers,'review_basis':basis,'checks_current':checks_current,
            'selection':c.model_dump(),'source':source,'value':value,'unit':unit,'target':target,
            'conversion':{'factor':factor,'value':converted,'target_unit':target['unit']} if target and factor is not None else None,
            'comparison':comparison,'members':members,'sample_context':sample_context,
            'notice':'Independent review attachment. Not a TiFM record, replacement decision, author report, or training sample.'})
    return output

def measurement_drafts(doc,results):
    """Create unassigned review attachments, never infer master identity from values."""
    from .models import Candidate
    valid={r['id']:r for r in results['measurements'] if r['valid']}
    existing={(c.measurement_id,c.metric) for c in doc.candidates if c.source_kind=='measurement'}
    drafts=[]
    for m in doc.measurements:
        metric='x' if m.kind=='point' and not doc.graph.use_y else 'area' if m.kind=='area' else None
        if metric is None or m.id not in valid or metric not in valid[m.id]['values'] or (m.id,metric) in existing:continue
        definition=('Digitized graph X coordinate; curve and peak identity require review.' if metric=='x' else
                    'Single-object 2D area; not phase fraction, mean grain size or population statistics.')
        ident='draft-'+hashlib.sha256((m.id+':'+metric).encode()).hexdigest()[:24]
        if any(c.id==ident for c in doc.candidates):continue
        drafts.append(Candidate(id=ident,source_kind='measurement',measurement_id=m.id,metric=metric,sample=m.sample,
            definition=definition,evidence=(doc.source+'\n'+m.note)[:2000]))
    return drafts
