"""Manual evidence and bounded extraction plans. No model or executable instructions."""
import hashlib
import json

TOOLS={'peak_position':('point','peak'),'graph_value':('point',),'length':('line',),
       'object_area':('area','region'),'mean_ecd':('area','region'),'reported_value':('transcription',)}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def evidence_result(doc,e):
    basis=digest({'image_id':doc.image_id,'source':doc.source,'evidence':e.model_dump(exclude={'review_basis'})})
    issues=[]
    if not e.text.strip() or not e.locator.strip() or not doc.source.strip():issues.append('Text, source locator and project source are required.')
    return {'id':e.id,'review_basis':basis,'issues':issues,'status':'blocked' if issues else 'reviewed' if e.review_basis==basis else 'needs_review'}


def plan_data(p,exclude=None):
    data=p.model_dump(exclude=exclude or set())
    if data.get('model_provenance') is None:data.pop('model_provenance',None)
    elif data['model_provenance']['import_kind'] != 'live_api':
        # New empty transport metadata must not invalidate existing offline reviews.
        for key in ('usage','response_model','called_at'):
            if not data['model_provenance'].get(key):data['model_provenance'].pop(key,None)
    return data


def plan_context(doc,ident):
    p=next((p for p in doc.plans if p.id==ident),None)
    return {'plan':plan_data(p) if p else None,
            'evidence':[e.model_dump() for e in doc.evidence if p and e.id in p.evidence_ids]}


def plan_results(doc,reference,calibration):
    evidences={e.id:evidence_result(doc,e) for e in doc.evidence}
    snippets={e.id:e for e in doc.evidence}
    samples={s['sample_id']:s for s in reference['samples']} if reference else {}
    plans=[]
    for p in doc.plans:
        blockers=[]
        if (p.origin=='model_response_import')!=(p.model_provenance is not None):blockers.append('Imported plans require provenance; manual plans must not claim model provenance.')
        if p.tool not in TOOLS[p.task]:blockers.append('Tool does not match the requested task.')
        if not p.panel.strip() or not p.object_identity.strip() or not p.definition.strip():blockers.append('Identify the panel, target object/curve and quantity definition.')
        if not p.sample_ids or len(set(p.sample_ids))!=len(p.sample_ids) or any(s not in samples for s in p.sample_ids):blockers.append('Select unique existing sample IDs from an imported master.')
        if len(' | '.join(p.sample_ids))>200:blockers.append('Combined sample label exceeds the measurement limit. Split the task into smaller sample groups.')
        if not p.evidence_ids or len(set(p.evidence_ids))!=len(p.evidence_ids) or any(i not in snippets for i in p.evidence_ids):blockers.append('Select existing, unique evidence entries.')
        if any(evidences.get(i,{}).get('status')!='reviewed' for i in p.evidence_ids):blockers.append('Selected evidence needs current transcription review.')
        if not any(i in snippets and snippets[i].kind!='user_note' for i in p.evidence_ids):blockers.append('Personal notes alone cannot establish source evidence. Add a caption, body, table or methods excerpt.')
        if p.task!='reported_value':
            if p.image_quality!='usable':blockers.append({'unassessed':'Assess whether the image supports this task.','needs_better_source':'Find a better source image before measuring; automatic source retrieval is not connected.','not_measurable':'Measurement is deferred because the image is not measurable.'}[p.image_quality])
            if not p.quality_note.strip():blockers.append('Describe scale/axis readability, object pixel size and relevant image limitations.')
        if not p.stop_conditions.strip():blockers.append('Specify when to stop, defer or request manual review.')
        if p.task=='mean_ecd' and not p.sampling_rule.strip():blockers.append('Specify object inclusion/exclusion and sampling scope for a mean ECD.')
        basis=digest({'plan':plan_data(p,{'review_basis'}),'image_id':doc.image_id,'source':doc.source,
                      'reference_id':doc.reference_id,'samples':[samples.get(i) for i in p.sample_ids],
                      'evidence':[{'entry':snippets[i].model_dump(),'review':evidences[i]} if i in snippets else {'missing':i} for i in p.evidence_ids]})
        status='blocked' if blockers else 'reviewed' if p.review_basis==basis else 'needs_review'
        required=[] if p.task=='reported_value' else ['x','y'] if p.task=='graph_value' else ['x'] if p.task=='peak_position' else ['spatial']
        calibration_ready=all(calibration.get(k,{}).get('valid') for k in required) and (p.task!='graph_value' or doc.graph.use_y)
        plans.append({'id':p.id,'status':status,'blockers':blockers,'review_basis':basis,
                      'can_start':status=='reviewed' and calibration_ready,
                      'calibration_ready':bool(calibration_ready),'required_calibration':required,
                      'notice':'Human-reviewed plan; no live model call or scientific verification. Imported response origin is unverified. Starting opens a tool, not automatic extraction.'})
    return {'evidence':list(evidences.values()),'plans':plans,'model_connected':False}
