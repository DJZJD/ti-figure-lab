"""Small local tools executing bounded LLM micrograph / XRD proposals."""
import math
import re
from typing import Literal
from PIL import Image, ImageDraw
from pydantic import Field
from . import paper_graphs as pg, paper_jobs as pj
from .models import Document, Measurement, Point, Axis, SpatialCalibration, GraphCalibration
from .geometry import evaluate, validate_image_coordinates, polygon_area, axis_value
from .regions import region_candidate
from .peaks import peak_candidates

class Scale(pj.Strict):
    endpoints: list[pg.Position] = Field(min_length=2,max_length=2)
    length: float = Field(gt=0,le=1000000)
    unit: Literal['nm','µm','mm']


class ObjectProposal(pj.Strict):
    seed: pg.Position
    radius_px: int = Field(default=80,ge=16,le=256)
    tolerance: int = Field(default=24,ge=1,le=100)
    fallback_outline: list[pg.Position] = Field(default_factory=list,max_length=100)
    label: str = Field(max_length=150)
    sample_label: str = Field(max_length=200)
    identity_evidence: str = Field(max_length=800)


class Micrograph(pj.Strict):
    status: Literal['measured','deferred']
    reason: str = Field(min_length=1,max_length=2000)
    quality: Literal['usable','needs_better_source','not_measurable']
    scale: Scale | None
    roi: pg.Box | None
    objects: list[ObjectProposal] = Field(max_length=30)
    warnings: list[str] = Field(max_length=30)


class Peak(pg.Position):
    sample_label: str = Field(max_length=200)
    curve_label: str = Field(max_length=150)
    binding_evidence: str = Field(max_length=800)
    allow_local_snap: bool
    radius_px: int = Field(default=20,ge=12,le=80)


class XRD(pj.Strict):
    status: Literal['measured','deferred']
    reason: str = Field(min_length=1,max_length=2000)
    quality: Literal['usable','needs_better_source','not_measurable']
    plot_bounds: pg.Box | None
    x_axis: pg.ReadAxis | None
    peaks: list[Peak] = Field(max_length=60)
    warnings: list[str] = Field(max_length=30)


def xy(p,w,h):return Point(x=p.x*w/1000,y=p.y*h/1000)
def within(p,b,w,h):return b.left*w/1000<=p.x<=b.right*w/1000 and b.top*h/1000<=p.y<=b.bottom*h/1000


def x_calibration(read,box,w,h):
    if read.unit.strip().lower() not in ('deg','degree','degrees','°'):raise ValueError('XRD requires degrees; q/d-spacing and other axes are deferred.')
    title=re.sub(r'\s+','',read.title.lower()).replace('θ','theta')
    if not re.fullmatch(r'(?:2theta|2-theta|twotheta)(?:/(?:deg|degree|degrees|°)|\((?:deg|degree|degrees|°)\))?',title):
        raise ValueError('XRD must explicitly identify the 2-theta axis.')
    a,c,b=sorted(read.ticks,key=lambda t:t.x)
    if b.x-a.x<150 or not a.x<c.x<b.x or not a.value<c.value<b.value:raise ValueError('Need three ordered, distinct X ticks spanning the crop.')
    if max(t.y for t in read.ticks)-min(t.y for t in read.ticks)>8:raise ValueError('Rotated X axis unsupported.')
    axis_edge_disagreement=min(abs(a.y-box.top),abs(a.y-box.bottom))>12
    if any(not box.left-8<=t.x<=box.right+8 for t in read.ticks):raise ValueError('X tick outside plot.')
    axis=Axis(points=[xy(a,w,h),xy(b,w,h)],values=[a.value,b.value],unit='deg')
    residual=abs((axis_value(axis,c.x*w/1000,'x')-c.value)/(b.value-a.value)*(b.x-a.x)*w/1000)
    if residual>3:raise ValueError('X third-tick check exceeds 3 crop pixels.')
    return axis,dict(pixel_residual=residual,axis_edge_disagreement=axis_edge_disagreement,
        notice='X positions only. Verify tick centers; model Y/plot-edge disagreement is retained, not used to calculate intensity. Internal consistency is not accuracy.')


def build_result(path,target,response,transform,kind):
    if response.status!='measured' or response.quality!='usable':raise ValueError(response.reason)
    with Image.open(path/'crop.png') as original:img=original.convert('RGB')
    w,h=img.size
    doc=Document(image_id='0'*64,mode='micrograph' if kind=='micrograph' else 'graph')
    provenance=[];deferred=[];checks={}
    if kind=='micrograph':
        if response.scale is None or response.roi is None:raise ValueError('Missing scale or measurement ROI.')
        from .micro_candidates import save_candidates
        checks['independent_regions']=save_candidates(img,(response.roi.left*w/1000,response.roi.top*h/1000,response.roi.right*w/1000,response.roi.bottom*h/1000),path)
        ends=[xy(p,w,h) for p in response.scale.endpoints]
        if math.dist((ends[0].x,ends[0].y),(ends[1].x,ends[1].y))<20:raise ValueError('Scale bar spans fewer than 20 rendered pixels.')
        from .scale_pixels import refine_scale
        ends,checks['scale_pixels']=refine_scale(img,ends)
        response.warnings.append(checks['scale_pixels']['warning'])
        doc.spatial=SpatialCalibration(points=ends,length=response.scale.length,unit=response.scale.unit)
        for i,obj in enumerate(response.objects,1):
            ident=f'object-{i:03}';seed=xy(obj.seed,w,h)
            if not within(seed,response.roi,w,h):deferred.append(dict(id=ident,reason='Seed outside analysis ROI.'));continue
            local=region_candidate(img,seed.x,seed.y,obj.radius_px,obj.tolerance)
            points=[Point(**p) for p in local['points']];method='local_gray_region';warnings=list(local['warnings'])
            if not points:
                points=[xy(p,w,h) for p in obj.fallback_outline];method='model_outline'
                warnings.append('Local segmentation failed: '+local['reason'])
                warnings.append('LLM polygon fallback: boundary is not pixel-verified. Edit or reject before use.')
            try:
                if len(points)<3:raise ValueError('No usable contour; manual outline needed.')
                polygon_area(points)
                if any(not within(p,response.roi,w,h) for p in points):raise ValueError('Contour outside analysis ROI.')
                if any(abs(p.x-seed.x)>obj.radius_px+2 or abs(p.y-seed.y)>obj.radius_px+2 for p in points):
                    raise ValueError('Contour exceeds the proposed local object window.')
                if any(math.dist((seed.x,seed.y),(p['seed']['x'],p['seed']['y']))<3 for p in provenance):raise ValueError('Duplicate seed.')
                if any(m.points==points for m in doc.measurements):raise ValueError('Duplicate contour.')
                if min(max(p.x for p in points)-min(p.x for p in points),max(p.y for p in points)-min(p.y for p in points))<8:
                    warnings.append('Object width under 8 rendered pixels; source resolution may dominate error.')
                note=('Unreviewed '+method+'. Phase identity is a model hypothesis, not a measured fact. '+obj.identity_evidence+' '+' '.join(warnings))[:2000]
                doc.measurements.append(Measurement(id=ident,kind='area',points=points,label=obj.label,sample=obj.sample_label,
                    acquisition='model_vision',note=note,include_in_summary=False))
                provenance.append(dict(id=ident,method=method,seed=seed.model_dump(),proposal=obj.model_dump(),local_result=local,warnings=warnings))
            except ValueError as err:deferred.append(dict(id=ident,reason=str(err),proposal=obj.model_dump(),local_result=local))
    else:
        if response.x_axis is None or response.plot_bounds is None:raise ValueError('Missing X axis or plot bounds.')
        from .xrd_pixels import refine_axis
        read=response.x_axis;bounds=response.plot_bounds
        try:read,bounds,refined=refine_axis(img,read,bounds)
        except ValueError as err:refined=dict(status='unresolved',reason=str(err))
        axis,checks=x_calibration(read,bounds,w,h);checks['local_ticks']=refined;doc.graph=GraphCalibration(x=axis,use_y=False)
        from .xrd_pixels import scan_peaks
        frame=(bounds.left*w/1000,bounds.top*h/1000,bounds.right*w/1000,bounds.bottom*h/1000)
        independent=scan_peaks(img,frame)
        for candidate in independent:
            candidate['two_theta_deg']=axis_value(axis,candidate['x'],'x')
        checks['image_first_candidates']=independent
        pj.write_json(path/'image-first-candidates.json',dict(candidates=independent,
            warning='Unassigned pixel proposals; labels and intersections may be false positives. Human review required.'))
        candidate_image=img.copy();candidate_draw=ImageDraw.Draw(candidate_image)
        for candidate in independent:
            cx,cy=candidate['x'],candidate['y']
            candidate_draw.ellipse((cx-5,cy-5,cx+5,cy+5),outline='#ff00cc',width=2)
            candidate_draw.text((cx+6,cy),candidate['id'],fill='#ff00cc')
        candidate_image.save(path/'candidates.png')
        if checks['axis_edge_disagreement']:
            response.warnings.append('Model tick Y positions disagree with its plot edge. Only X positions are used; verify tick centers/calibration manually before accepting any peaks.')
        for i,peak in enumerate(response.peaks,1):
            ident=f'peak-{i:03}';point=xy(peak,w,h);initial=point.model_dump()
            if not within(point,response.plot_bounds,w,h):deferred.append(dict(id=ident,reason='Peak outside plot.'));continue
            local=peak_candidates(img,point.x,point.y,peak.radius_px) if peak.allow_local_snap else dict(candidates=[],reason='Model requested no snapping near interference.')
            candidates=[p for p in local['candidates'] if p['kind']=='supported_apex' and p['distance_px']<=peak.radius_px/2 and within(Point(x=p['x'],y=p['y']),response.plot_bounds,w,h)]
            method='model_peak';warnings=['Curve/sample binding requires human review. No intensity or phase assignment is calculated.']
            if len(candidates)==1:
                point=Point(x=candidates[0]['x'],y=candidates[0]['y']);method='local_peak_snap'
            else:warnings.append('No unique local apex: retained model position for manual correction, not a verified peak.')
            if any(math.dist((point.x,point.y),(m.points[0].x,m.points[0].y))<3 for m in doc.measurements):
                deferred.append(dict(id=ident,reason='Duplicate/overlapping peak position.'));continue
            doc.measurements.append(Measurement(id=ident,kind='point',points=[point],sample=peak.sample_label,label=peak.curve_label,
                acquisition='model_vision',note=(method+'. '+peak.binding_evidence+' '+' '.join(warnings))[:2000]))
            provenance.append(dict(id=ident,method=method,initial_pixel=initial,proposal=peak.model_dump(),local_result=local,warnings=warnings))
    pj.write_json(path/'tool-audit.json',dict(provenance=provenance,deferred=deferred,scientifically_approved=False))
    if not doc.measurements:raise ValueError('No usable proposals. See model output and tool-audit.json; manual extraction or better source required.')
    validate_image_coordinates(doc,w,h);evaluated=evaluate(doc)
    if any(not r['valid'] for r in evaluated['measurements']):raise ValueError('Local geometry calculation rejected a proposal.')
    overlay=img.copy();draw=ImageDraw.Draw(overlay)
    for p in (doc.spatial.points if kind=='micrograph' else doc.graph.x.points):draw.ellipse((p.x-5,p.y-5,p.x+5,p.y+5),outline='#0088ff',width=2)
    for m in doc.measurements:
        pts=[(p.x,p.y) for p in m.points]
        if m.kind=='area':draw.line(pts+[pts[0]],fill='#ed2350',width=2)
        else:
            x,y=pts[0];draw.ellipse((x-6,y-6,x+6,y+6),outline='#ed2350',width=2)
        draw.text((pts[0][0]+7,pts[0][1]),m.id,fill='#ed2350')
    overlay.save(path/'overlay.png')
    result=dict(kind=kind,status='needs_review',selection=target.model_dump(),transform=transform,proposal=response.model_dump(),
        document=doc.model_dump(),measurements=evaluated['measurements'],provenance=provenance,deferred=deferred,calibration_check=checks,
        warnings=response.warnings+['Model-selected objects/peaks are not exhaustive or statistically representative. No area/volume fraction or population mean is inferred.',
            'Rendered pixels do not restore lost source resolution. Scale/axis positions, boundaries and identities require review.'],scientifically_approved=False)
    pj.write_json(path/'result.json',result)
    return result


def workspace(result,image_id,paper,source):
    doc=Document.model_validate(result['document']);doc.image_id=image_id
    doc.name=f'{paper} {result["kind"]} {result["selection"]["locator"]}'[:200];doc.source=source[:2000]
    from .candidates import measurement_drafts
    doc.candidates.extend(measurement_drafts(doc,evaluate(doc)))
    return doc
