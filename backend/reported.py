"""Literal paper reports and explicit transcription review, independent of image geometry."""
import math
import re
from .plans import digest, evidence_result, plan_context
from .candidates import unit_spec


def number(text):
    if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', text.strip()):
        raise ValueError('Use a plain finite number (scientific notation is allowed).')
    value=float(text)
    if not math.isfinite(value):raise ValueError('Numeric value is outside the finite range.')
    return value


def reported_results(doc,reference,planning):
    samples={s['sample_id']:s for s in reference['samples']} if reference else {}
    targets={t['path']:t for t in reference['targets']} if reference else {}
    evidence={e.id:e for e in doc.evidence}
    plans={p['id']:p for p in planning['plans']}
    output=[]
    for item in doc.reported_values:
        errors=[]; warnings=[]; parsed=None; comparison=None
        if not all(v.strip() for v in (doc.source,item.quantity,item.definition,item.raw_value,item.scope_note)):
            errors.append('Source, quantity, definition, verbatim value and conditions/statistical scope are required.')
        if not item.sample_ids or len(set(item.sample_ids))!=len(item.sample_ids) or any(s not in samples for s in item.sample_ids):
            errors.append('Select unique existing sample IDs from the reference master.')
        if not item.evidence_ids or len(set(item.evidence_ids))!=len(item.evidence_ids) or any(i not in evidence for i in item.evidence_ids):
            errors.append('Select unique existing source excerpts.')
        selected=[evidence[i] for i in item.evidence_ids if i in evidence]
        if not any(e.kind!='user_note' for e in selected):errors.append('Personal notes alone cannot support a reported value.')
        if any(evidence_result(doc,e)['status']!='reviewed' for e in selected):errors.append('Source excerpts need current transcription review.')
        if item.source_class=='unknown':errors.append('Classify the value as this-work experimental, derived, predicted or cited.')
        elif item.source_class!='experimental':warnings.append('This is not classified as a direct experimental result from this work.')
        if item.unit_status=='reported' and not item.unit.strip():errors.append('Enter the reported unit or explicitly record missing/dimensionless units.')
        if item.unit_status!='reported' and item.unit.strip():errors.append('Clear the unit text when units are missing or the quantity is dimensionless.')
        if item.unit_status=='not_reported':warnings.append('Unit is not reported; no unit is inferred.')
        try:
            if item.value_kind=='text':
                if item.value_text or item.upper_text or item.qualifier!='eq':errors.append('Text-only values cannot carry numeric fields or a numeric qualifier.')
            else:
                value=number(item.value_text); parsed={'value':value,'qualifier':item.qualifier}
                if item.value_kind=='range':
                    upper=number(item.upper_text)
                    if upper<value:errors.append('Range upper bound must be at least its lower bound.')
                    if item.qualifier not in ('eq','approx'):errors.append('Range values support exact or approximate qualifiers only.')
                    parsed={'lower':value,'upper':upper,'qualifier':item.qualifier}
                elif item.upper_text.strip():errors.append('A scalar cannot carry an upper range bound.')
        except ValueError as err:errors.append(str(err))
        if (item.uncertainty_kind=='none')!= (not item.uncertainty_text.strip()):
            errors.append('Uncertainty text and its meaning must agree; use unspecified when the paper does not define ±.')
        if item.uncertainty_kind=='unspecified':warnings.append('Uncertainty meaning is unspecified; it is not assumed to be SD or SE.')
        if item.n_text and not re.fullmatch(r'[1-9]\d{0,8}',item.n_text):errors.append('Reported N must be a positive integer, or blank when not reported.')
        target=targets.get(item.target_path) if item.target_path else None
        if item.target_path:
            if not target:errors.append('Selected reference field is missing.')
            else:
                if set(target['sample_ids'])!=set(item.sample_ids):errors.append('Sample IDs must match the complete target sample set.')
                a=unit_spec(item.unit) if item.unit_status=='reported' else None
                b=unit_spec(target['unit'])
                if not a or not b or a[0]!=b[0]:errors.append('Mapped units are incompatible or unsupported; retain the report unmapped.')
                elif parsed and item.value_kind=='scalar' and item.qualifier in ('eq','approx'):
                    old=target['value']; converted=parsed['value']*a[1]/b[1]
                    if not math.isfinite(converted):errors.append('Converted value is outside the finite range.')
                    elif isinstance(old,(int,float)) and not isinstance(old,bool) and math.isfinite(old):
                        diff=converted-old
                        if math.isfinite(diff):comparison={'existing_value':old,'reported_value_in_target_unit':converted,'unit':target['unit'],'signed_difference':diff}
        if item.plan_id:
            p=next((p for p in doc.plans if p.id==item.plan_id),None)
            if not p or p.task!='reported_value' or plans.get(item.plan_id,{}).get('status')!='reviewed':errors.append('Linked transcription plan needs current review.')
            elif set(p.sample_ids)!=set(item.sample_ids) or not set(p.evidence_ids).issubset(item.evidence_ids):errors.append('Report must retain its plan sample set and all supporting evidence.')
        if not all((item.source_checked,item.sample_checked,item.quantity_checked,item.value_checked)):
            errors.append('Complete all four transcription/context checks.')
        context=[samples[i] for i in item.sample_ids if i in samples]
        basis=digest({'record':item.model_dump(exclude={'review_basis'}),'source':doc.source,'image_id':doc.image_id,
                      'reference_id':doc.reference_id,'sample_context':context,'target':target,
                      'evidence':[e.model_dump() for e in selected],'plan':plan_context(doc,item.plan_id) if item.plan_id else None})
        output.append({'id':item.id,'status':'blocked' if errors else 'reviewed' if item.review_basis==basis else 'needs_review',
                       'review_basis':basis,'blockers':errors,'warnings':warnings,'record':item.model_dump(),'parsed_value':parsed,
                       'target':target,'sample_context':context,'evidence':[e.model_dump() for e in selected],'comparison':comparison,
                       'notice':'Reviewed transcription, not independent scientific validation, a master replacement or a training record. Uncertainty text is preserved without numerical conversion.'})
    return output
