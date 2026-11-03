"""Bind image-first IDs without permitting the model to change their geometry."""
from typing import Literal
from pydantic import Field
from PIL import Image, ImageDraw
from . import paper_jobs as pj
from .models import Document, Measurement, Point
from .geometry import evaluate

class Decision(pj.Strict):
    candidate_id: str = Field(max_length=64)
    decision: Literal['keep','reject','uncertain']
    sample_label: str = Field(max_length=200)
    curve_label: str = Field(max_length=200)
    evidence: str = Field(min_length=1,max_length=1500)

class Bindings(pj.Strict):
    decisions: list[Decision] = Field(max_length=300)
    warnings: list[str] = Field(max_length=30)

def apply_bindings(path,result,bindings):
    candidates=result['calibration_check']['image_first_candidates']
    index={c['id']:c for c in candidates};seen=set()
    for d in bindings.decisions:
        if d.candidate_id not in index or d.candidate_id in seen:
            raise ValueError('Unknown or duplicate candidate ID; no bindings applied.')
        seen.add(d.candidate_id)
        if d.decision=='keep' and (not d.sample_label.strip() or not d.curve_label.strip()):
            raise ValueError('Kept candidates require explicit sample and curve labels.')
    if seen!=set(index):raise ValueError('Every candidate needs an explicit decision.')
    doc=Document.model_validate(result['document']);doc.measurements=[];provenance=[];deferred=[]
    for d in bindings.decisions:
        c=index[d.candidate_id]
        if d.decision!='keep':
            deferred.append(dict(id=c['id'],reason=d.decision+': '+d.evidence));continue
        doc.measurements.append(Measurement(id=c['id'],kind='point',points=[Point(x=c['x'],y=c['y'])],
            sample=d.sample_label,label=d.curve_label,acquisition='model_vision',note='Local pixel candidate; model binding requires review. '+d.evidence))
        provenance.append(dict(id=c['id'],method='local_candidate_model_binding',candidate=c,binding=d.model_dump(),warnings=['Identity and peak validity require human review.']))
    # All-rejected responses are valid, but yield no accepted scientific data.
    pj.write_json(path/'before-binding-result.json',result)
    result=dict(result,document=doc.model_dump(),measurements=evaluate(doc)['measurements'],provenance=provenance,
        deferred=deferred,warnings=result['warnings']+bindings.warnings,binding=bindings.model_dump(),scientifically_approved=False)
    im=Image.open(path/'crop.png').convert('RGB');draw=ImageDraw.Draw(im)
    for m in doc.measurements:
        x,y=m.points[0].x,m.points[0].y;draw.ellipse((x-5,y-5,x+5,y+5),outline='#ed2350',width=2);draw.text((x+6,y),m.id,fill='#ed2350')
    im.save(path/'overlay.png');pj.write_json(path/'result.json',result)
    return result
