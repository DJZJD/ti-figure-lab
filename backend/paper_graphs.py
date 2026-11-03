"""Bounded text selection -> visual crop -> visual geometry -> local computation.

All outputs are unreviewed proposals. No reference masters or known coordinates
are provided to the model. One run per paper job; failures never auto-retry.
"""
import base64
import csv
import io
import json
import math
from pathlib import Path
from typing import Literal
from uuid import uuid4

from PIL import Image, ImageDraw
from pydantic import Field, model_validator
from . import paper_jobs as pj
from .geometry import axis_value
from .models import Axis, Point, Document, Measurement, GraphCalibration
from .llm_live import completion


class Run(pj.Strict):
    expected_revision: int = Field(ge=1)
    objective: str = Field(min_length=5,max_length=1500)
    max_panels: int = Field(default=1,ge=1,le=3)


class Evidence(pj.Strict):
    page: int = Field(ge=1,le=pj.MAX_PAGES)
    role: Literal['caption','conditions','discussion']
    quote: str = Field(min_length=5,max_length=1500)


class Target(pj.Strict):
    page: int = Field(ge=1,le=pj.MAX_PAGES)
    locator: str = Field(min_length=1,max_length=1000)
    quantity: str = Field(min_length=1,max_length=200)
    figure_pages: list[int] = Field(default_factory=list,max_length=10)
    evidence: list[Evidence] = Field(min_length=1,max_length=12)
    reason: str = Field(min_length=1,max_length=1000)


class Selection(pj.Strict):
    targets: list[Target] = Field(max_length=3)
    deferred: list[str] = Field(max_length=50)


def validate_evidence(target,pages):
    figure_pages=target.figure_pages or [target.page]
    if target.page not in figure_pages or len(set(figure_pages))!=len(figure_pages):
        raise ValueError('Figure pages must be unique and include the primary page.')
    if any(p<1 or p>len(pages) for p in figure_pages):
        raise ValueError('Figure page is outside the paper.')
    if not any(e.role=='caption' for e in target.evidence):
        raise ValueError('A separately sourced caption quote is required.')
    valid=[];rejected=[]
    for e in target.evidence:
        if e.page>len(pages) or pj.matching_stream(pages[e.page-1],e.quote) is None:
            if e.role=='caption':
                raise ValueError(f'{e.role} quote not found as contiguous text on page {e.page}. Keep cross-page evidence separate.')
            rejected.append(dict(**e.model_dump(),error='Quote/page mismatch; excluded from downstream evidence.'))
        else:valid.append(e.model_dump())
    return valid,rejected


class Box(pj.Strict):
    left: float = Field(ge=0,le=1000)
    top: float = Field(ge=0,le=1000)
    right: float = Field(ge=0,le=1000)
    bottom: float = Field(ge=0,le=1000)

    @model_validator(mode='after')
    def order(self):
        if self.right-self.left<20 or self.bottom-self.top<20:
            raise ValueError('Rectangle is empty or too small.')
        return self


class Located(pj.Strict):
    status: Literal['located','deferred']
    reason: str = Field(min_length=1,max_length=2000)
    bbox: Box | None


class Position(pj.Strict):
    x: float = Field(ge=0,le=1000)
    y: float = Field(ge=0,le=1000)


class Tick(Position):
    value: float


class ReadAxis(pj.Strict):
    title: str = Field(min_length=1,max_length=200)
    unit: str = Field(max_length=40)
    ticks: list[Tick] = Field(min_length=3,max_length=3)


class Marker(Position):
    sample_label: str = Field(max_length=200)
    curve_label: str = Field(max_length=150)
    binding_evidence: str = Field(max_length=1000)


class Plot(pj.Strict):
    plot_bounds: Box
    x_axis: ReadAxis
    y_axis: ReadAxis
    markers: list[Marker] = Field(min_length=1,max_length=100)


class Measured(pj.Strict):
    status: Literal['measured','deferred']
    reason: str = Field(min_length=1,max_length=2000)
    plot: Plot | None
    warnings: list[str] = Field(max_length=30)


def parse(content,cls):
    def unique(pairs):
        result={}
        for k,v in pairs:
            if k in result:raise ValueError('Duplicate output key.')
            result[k]=v
        return result
    return cls.model_validate(json.loads(content,object_pairs_hook=unique,
        parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Nonfinite output.'))))


def request_body(rules,stage,context,cls,image_raw=None):
    parts=[{'type':'text','text':json.dumps({'stage':stage,'context':context},ensure_ascii=False)}]
    if image_raw is not None:
        if len(image_raw)>8*1024*1024:raise ValueError('Image exceeds this workflow’s 8 MiB limit.')
        parts.append({'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(image_raw).decode(),'detail':'high'}})
    return dict(model='deepseek-flash',messages=[dict(role='system',content=rules+'\nResponse JSON schema:\n'+json.dumps(cls.model_json_schema())),
        dict(role='user',content=parts)],response_format={'type':'json_object'},max_tokens=8192,thinking={'type':'disabled'},stream=False)


def begin(root,ident,request,rules):
    with pj.LOCK:
        folder=pj.job_path(root,ident);job=pj.read_json(folder/'job.json')
        if job['revision']!=request.expected_revision or pj.busy(job):raise ValueError('Paper changed or extraction is running. Refresh first.')
        if job.get('graph_run'):raise ValueError('Graph extraction already attempted for this job. Reimport for an explicit new run; no paid auto-retries.')
        if not (folder/'source/pages.json').exists():raise ValueError('PDF preparation must succeed first.')
        if pj.digest((folder/'source.pdf').read_bytes())!=job['pdf_sha256']:raise ValueError('Archived PDF changed. Reimport it.')
        if not request.objective.strip():raise ValueError('A graph objective is required.')
        runid=uuid4().hex;run_dir=folder/'graphs'/runid;run_dir.mkdir(parents=True)
        (run_dir/'requirements.md').write_bytes(rules.encode())
        graph=dict(id=runid,status='running',objective=request.objective,max_panels=request.max_panels,
            max_calls=1+2*request.max_panels,calls_attempted=0,panels=[],deferred=[],started_at=pj.now(),error=None,
            requirements_sha256=pj.digest(rules.encode()),source_pdf_sha256=job['pdf_sha256'],
            notice='Experimental model-localized marker centers, not independently verified pixel detections. All results require review.')
        job.update(graph_run=graph,revision=job['revision']+1)
        pj.write_json(run_dir/'state.json',graph);pj.write_json(folder/'job.json',job)
        return graph


def update(folder,graph):
    with pj.LOCK:
        job=pj.read_json(folder/'job.json')
        job.update(graph_run=graph,revision=job['revision']+1)
        pj.write_json(folder/'graphs'/graph['id']/'state.json',graph)
        pj.write_json(folder/'job.json',job)


def crop_pdf(folder,page_number,box,destination):
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(str(folder/'source.pdf')) as pdf:
        page=pdf[page_number-1]
        width,height=page.get_size();scale=min(4.0,3200/max(width,height))
        bitmap=page.render(scale=scale)
        try:
            image=bitmap.to_pil().convert('RGB')
            expanded=padded_box(box)
            rect=(math.floor(expanded.left*image.width/1000),math.floor(expanded.top*image.height/1000),
                  math.ceil(expanded.right*image.width/1000),math.ceil(expanded.bottom*image.height/1000))
            cropped=image.crop(rect)
            if min(cropped.size)<120:raise ValueError('Target crop is too small for this graph workflow.')
            cropped.save(destination)
            return dict(page=page_number,page_render_width=image.width,page_render_height=image.height,
                crop_box_pixels=list(rect),crop_width=cropped.width,crop_height=cropped.height,
                pdf_page_width=width,pdf_page_height=height,render_scale=scale,
                model_box=box.model_dump(),expanded_box=expanded.model_dump(),padding_fraction=0.18,
                source='PDF page rendering; no missing-detail reconstruction',crop_sha256=pj.digest(destination.read_bytes()))
        finally:bitmap.close();page.close()


def padded_box(box):
    dx=(box.right-box.left)*0.18;dy=(box.bottom-box.top)*0.18
    return Box(left=max(0,box.left-dx),top=max(0,box.top-dy),right=min(1000,box.right+dx),bottom=min(1000,box.bottom+dy))


def convert(plot,width,height):
    def point(p):return Point(x=p.x*width/1000,y=p.y*height/1000)
    box=plot.plot_bounds
    axes={};checks={}
    for dim,read in [('x',plot.x_axis),('y',plot.y_axis)]:
        ordered=sorted(read.ticks,key=lambda t:getattr(t,dim))
        a,c,b=ordered
        positions=[getattr(t,dim) for t in (a,b,c)]
        if len(set(t.value for t in read.ticks))!=3 or not min(positions[:2])<positions[2]<max(positions[:2]):
            raise ValueError('Three distinct ticks are required, with the check tick between the first two.')
        if abs(positions[0]-positions[1])<150:raise ValueError('Reference ticks span too little of the crop.')
        orth='y' if dim=='x' else 'x'
        alignment=max(getattr(t,orth) for t in read.ticks)-min(getattr(t,orth) for t in read.ticks)
        if alignment>8:raise ValueError('Tick centers do not lie on an axis-aligned line.')
        edge_candidates=(box.top,box.bottom) if dim=='x' else (box.left,box.right)
        if min(abs(getattr(a,orth)-e) for e in edge_candidates)>12:raise ValueError('Tick axis does not match the plot boundary.')
        lower,upper=(box.left,box.right) if dim=='x' else (box.top,box.bottom)
        if any(not lower-8<=p<=upper+8 for p in positions):raise ValueError('Tick lies outside the plot extent.')
        axis=Axis(points=[point(a),point(b)],values=[a.value,b.value],unit=read.unit)
        predicted=axis_value(axis,getattr(point(c),dim),dim)
        pixels=abs((predicted-c.value)/(b.value-a.value)*(getattr(point(b),dim)-getattr(point(a),dim)))
        checks[dim]=dict(check_tick=c.model_dump(),predicted_value=predicted,pixel_residual=pixels,
            tolerance_px=3,meaning='Internal consistency only, not recognition accuracy or an uncertainty bound.')
        if pixels>3:raise ValueError(f'{dim.upper()} third-tick check disagrees by more than 3 crop pixels.')
        axes[dim]=axis
    rows=[];points=[]
    for i,m in enumerate(plot.markers,1):
        if not box.left<=m.x<=box.right or not box.top<=m.y<=box.bottom:raise ValueError('Marker outside plot rectangle.')
        p=point(m)
        if any(math.hypot(p.x-q.x,p.y-q.y)<2 for q in points):raise ValueError('Duplicate/overlapping marker centers require review.')
        points.append(p)
        rows.append(dict(id=f'marker-{i:03}',pixel=p.model_dump(),x=axis_value(axes['x'],p.x,'x'),y=axis_value(axes['y'],p.y,'y'),
            sample_label=m.sample_label,curve_label=m.curve_label,binding_evidence=m.binding_evidence,status='needs_review',
            flags=[] if m.sample_label and m.binding_evidence else ['Sample/condition binding unresolved.']))
    return GraphCalibration(x=axes['x'],y=axes['y'],use_y=True),rows,checks


def build_result(panel_dir,selection,measured,transform):
    if measured.status!='measured' or measured.plot is None:raise ValueError(measured.reason)
    calibration,rows,checks=convert(measured.plot,transform['crop_width'],transform['crop_height'])
    for r in rows:
        r['page_render_pixel']=dict(x=r['pixel']['x']+transform['crop_box_pixels'][0],y=r['pixel']['y']+transform['crop_box_pixels'][1])
        r['pdf_page_point']=dict(x=r['page_render_pixel']['x']*transform['pdf_page_width']/transform['page_render_width'],
            y=r['page_render_pixel']['y']*transform['pdf_page_height']/transform['page_render_height'])
    with Image.open(panel_dir/'crop.png') as img:
        overlay=img.convert('RGB');draw=ImageDraw.Draw(overlay)
        for dim,axis in [('X',calibration.x),('Y',calibration.y)]:
            for j,p in enumerate(axis.points):
                draw.ellipse((p.x-6,p.y-6,p.x+6,p.y+6),outline='#0088ff',width=2)
                draw.text((p.x+8,p.y+3),f'{dim}{j+1}',fill='#0088ff')
        for row in rows:
            x,y=row['pixel']['x'],row['pixel']['y'];draw.ellipse((x-7,y-7,x+7,y+7),outline='#ed2350',width=2)
            draw.text((x+9,y-12),row['id'],fill='#ed2350')
        overlay.save(panel_dir/'overlay.png')
    result=dict(status='needs_review',selection=selection.model_dump(),transform=transform,plot=measured.plot.model_dump(),
        calibration=calibration.model_dump(),points=rows,tick_checks=checks,model_warnings=measured.warnings,
        reason=measured.reason,model_geometry=True,scientifically_approved=False)
    pj.write_json(panel_dir/'result.json',result)
    stream=io.StringIO(newline='');writer=csv.writer(stream);writer.writerow(['marker_id','x','x_unit','y','y_unit','sample_label','review_status'])
    for row in rows:
        label=row['sample_label'];label="'"+label if label.lstrip().startswith(('=','+','-','@')) else label
        writer.writerow([row['id'],row['x'],calibration.x.unit,row['y'],calibration.y.unit,label,'needs_review'])
    (panel_dir/'points.candidates.csv').write_bytes(stream.getvalue().encode('utf-8-sig'))
    return result


async def execute(root,ident,send,rules):
    import asyncio
    from starlette.concurrency import run_in_threadpool
    folder=pj.job_path(root,ident);job=pj.read_json(folder/'job.json');graph=job['graph_run'];run_dir=folder/'graphs'/graph['id']
    async def call(name,stage,context,cls,image_raw=None):
        if graph['calls_attempted']>=graph['max_calls']:raise ValueError('Graph call budget exhausted.')
        body=request_body(rules,stage,context,cls,image_raw)
        round_dir=run_dir/name;round_dir.mkdir(exist_ok=False)
        (round_dir/'requirements.md').write_bytes(rules.encode());pj.write_json(round_dir/'input.json',body)
        graph['calls_attempted']+=1;graph['active_stage']=name;update(folder,graph)
        try:
            response=await asyncio.wait_for(send(body),timeout=110)
            pj.write_json(round_dir/'response.json',response)
            content,usage,error=completion(response);pj.write_json(round_dir/'usage.json',usage)
            if content is not None:(round_dir/'output.txt').write_bytes(content.encode())
            if error:raise ValueError(error)
            parsed=parse(content,cls);pj.write_json(round_dir/'parsed.json',parsed.model_dump())
            return parsed
        except Exception as err:
            # Transport never includes credentials in errors. Keep stage failure alongside raw response.
            reason=str(err)[:1200] if isinstance(err,(ValueError,asyncio.TimeoutError)) else 'Unexpected stage failure; see local server logs.'
            pj.write_json(round_dir/'error.json',{'error':reason or 'Request timed out; billing unknown. No retry.'})
            raise ValueError(reason or 'Request timed out; billing unknown. No retry.') from None
    try:
        pages=pj.read_json(folder/'source/pages.json')
        chosen=await call('01-select','selection',dict(objective=graph['objective'],max_panels=graph['max_panels'],
            pages=[pj.text_context(p) for p in pages]),Selection)
        if len(chosen.targets)>graph['max_panels']:raise ValueError('Model exceeded the selected panel limit. Nothing silently truncated.')
        graph['deferred']=chosen.deferred
        seen=set()
        for index,target in enumerate(chosen.targets,1):
            pid=f'panel-{index:03}';pd=run_dir/pid;pd.mkdir()
            item=dict(id=pid,page=target.page,figure_pages=target.figure_pages or [target.page],selection=target.model_dump(),locator=target.locator,status='processing',error=None)
            graph['panels'].append(item);update(folder,graph)
            try:
                if (target.page,target.locator) in seen:raise ValueError('Duplicate selected panel.')
                seen.add((target.page,target.locator))
                if target.page>len(pages):raise ValueError('Selected page is outside the paper.')
                page=pages[target.page-1]
                evidence,rejected=validate_evidence(target,pages)
                item['validated_evidence']=evidence
                item['rejected_evidence']=rejected
                safe_target=target.model_dump(exclude={'evidence','reason'})
                if len(target.figure_pages)>1:
                    raise ValueError('Figure spans multiple pages. Page references and verified evidence retained for manual review; stitching and measurement across pages are not supported yet.')
                image_raw=(folder/page['image']).read_bytes()
                located=await call(f'{pid}-locate','locate',dict(target=safe_target,page_text=page['text'],alternate_texts=pj.text_context(page)['alternate_texts'],verified_evidence=evidence),Located,image_raw)
                if located.status!='located' or located.bbox is None:raise ValueError(located.reason)
                if pj.digest((folder/'source.pdf').read_bytes())!=graph['source_pdf_sha256']:raise ValueError('PDF changed during run.')
                transform=await run_in_threadpool(crop_pdf,folder,target.page,located.bbox,pd/'crop.png')
                pj.write_json(pd/'transform.json',transform)
                measured=await call(f'{pid}-measure','measure',dict(target=safe_target,page_text=page['text'],alternate_texts=pj.text_context(page)['alternate_texts'],verified_evidence=evidence,
                    coordinate_system='0–1000 normalized to supplied crop, top-left origin',width=transform['crop_width'],height=transform['crop_height']),Measured,(pd/'crop.png').read_bytes())
                if rejected:
                    measured.warnings.append('Some selection-stage condition quotes failed source checks and were excluded. Verify sample conditions independently.')
                result=await run_in_threadpool(build_result,pd,target,measured,transform)
                item.update(status='needs_review',result=result,crop=f'graphs/{graph["id"]}/{pid}/crop.png',overlay=f'graphs/{graph["id"]}/{pid}/overlay.png')
            except ValueError as err:item.update(status='deferred',error=str(err)[:1500])
            update(folder,graph)
        graph['status']='awaiting_review' if any(p['status']=='needs_review' for p in graph['panels']) else 'deferred'
    except Exception as err:
        graph.update(status='failed',error=str(err)[:1500] if isinstance(err,ValueError) else 'Local graph processing failed; inspect the saved stages.')
    finally:
        graph['finished_at']=pj.now();graph.pop('active_stage',None);update(folder,graph)


def panel(root,ident,panel_id):
    folder=pj.job_path(root,ident);state=pj.read_json(folder/'job.json');graph=state.get('graph_run')
    if not graph or graph['status']=='running':raise ValueError('Graph run is not ready.')
    item=next((p for p in graph['panels'] if p['id']==panel_id),None)
    if not item or item['status']!='needs_review':raise ValueError('Panel has no validated geometry proposal.')
    if item.get('refinement',{}).get('status')=='running':raise ValueError('Wait for pixel refinement to finish.')
    path=folder/'graphs'/graph['id']/panel_id
    if item.get('refinement',{}).get('status')=='needs_review':path=path/item['refinement']['id']
    result=pj.read_json(path/'result.json')
    if pj.digest((path/'crop.png').read_bytes())!=result['transform']['crop_sha256']:raise ValueError('Crop bytes changed.')
    return folder,graph,item,result,path


def workspace(result,image_id,paper,source):
    calibration,rows,_=convert(Plot.model_validate(result['plot']),result['transform']['crop_width'],result['transform']['crop_height'])
    return Document(name=f'{paper} {result["selection"]["locator"]}'[:200],image_id=image_id,mode='graph',source=source[:2000],graph=calibration,
        measurements=[Measurement(id=r['id'],kind='point',points=[Point(**r['pixel'])],sample=r['sample_label'],label=r['curve_label'][:200],
            acquisition='model_vision',status='needs_review',note=('Model-proposed marker center; review ticks, position and sample binding. '+r['binding_evidence'])[:2000]) for r in rows])
