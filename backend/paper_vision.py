"""LLM-directed micrograph/XRD proposals with local tools and immutable stage archives."""
from typing import Literal
from uuid import uuid4
import base64
import io
from PIL import Image,ImageDraw
from pydantic import Field
from . import paper_jobs as pj
from .paper_graphs import (Run as GraphRun, Target as pg_target, request_body, parse, Selection, Located,
    validate_evidence, crop_pdf)
from .llm_live import completion
from .vision_tools import Micrograph, XRD, build_result, workspace


class Run(GraphRun):
    kind: Literal['micrograph','xrd']
    bind_candidates: bool = True


class Retry(pj.Strict):
    expected_revision: int = Field(ge=1)


def add_grid(body,image_raw,destination):
    with Image.open(io.BytesIO(image_raw)) as source:img=source.convert('RGBA')
    layer=Image.new('RGBA',img.size);draw=ImageDraw.Draw(layer);w,h=img.size
    for n in range(100,1000,100):
        x=n*w/1000;y=n*h/1000
        draw.line((x,0,x,h),fill=(180,0,180,90),width=1);draw.line((0,y,w,y),fill=(180,0,180,90),width=1)
        for px,py,label in [(x+2,2,str(n)),(2,y+2,str(n))]:
            draw.rectangle((px,py,px+32,py+15),fill=(255,255,255,230));draw.text((px,py),label,fill=(110,0,110,255))
    grid=Image.alpha_composite(img,layer).convert('RGB');grid.save(destination)
    body['messages'][1]['content'].append(dict(type='image_url',image_url=dict(url='data:image/png;base64,'+base64.b64encode(destination.read_bytes()).decode(),detail='high')))


def begin(root,ident,request,rules):
    with pj.LOCK:
        folder=pj.job_path(root,ident);job=pj.read_json(folder/'job.json')
        if job['revision']!=request.expected_revision or pj.busy(job):raise ValueError('Job changed or another extraction is running. Refresh first.')
        if request.kind in job.get('vision_runs',{}):raise ValueError('This visual branch was already attempted. Import a new job for an explicit retry.')
        if pj.digest((folder/'source.pdf').read_bytes())!=job['pdf_sha256']:raise ValueError('Source PDF changed.')
        if not request.objective.strip():raise ValueError('Specify a visual objective.')
        if not (folder/'source/pages.json').exists():raise ValueError('Prepare PDF pages first.')
        run=dict(id=uuid4().hex,kind=request.kind,status='running',objective=request.objective,max_panels=request.max_panels,
            bind_candidates=request.bind_candidates,max_calls=1+(3 if request.kind=='xrd' and request.bind_candidates else 2)*request.max_panels,calls_attempted=0,panels=[],deferred=[],error=None,started_at=pj.now(),
            requirements_sha256=pj.digest(rules.encode()),source_pdf_sha256=job['pdf_sha256'])
        dest=folder/'vision'/run['id'];dest.mkdir(parents=True)
        (dest/'requirements.md').write_text(rules,encoding='utf8')
        job.setdefault('vision_runs',{})[request.kind]=run;job['revision']+=1
        pj.write_json(dest/'state.json',run);pj.write_json(folder/'job.json',job)
        return run


def update(folder,run):
    with pj.LOCK:
        job=pj.read_json(folder/'job.json');job.setdefault('vision_runs',{})[run['kind']]=run;job['revision']+=1
        pj.write_json(folder/'vision'/run['id']/'state.json',run);pj.write_json(folder/'job.json',job)


async def execute(root,ident,kind,send,rules):
    import asyncio
    from starlette.concurrency import run_in_threadpool
    folder=pj.job_path(root,ident);job=pj.read_json(folder/'job.json');graph=job['vision_runs'][kind];run_dir=folder/'vision'/graph['id']
    async def call(name,stage,context,cls,image_raw=None):
        if graph['calls_attempted']>=graph['max_calls']:raise ValueError('Visual call budget exhausted.')
        body=request_body(rules,stage,context,cls,image_raw)
        round_dir=run_dir/name;round_dir.mkdir(exist_ok=False)
        if stage=='measure':add_grid(body,image_raw,round_dir/'coordinate-grid.png')
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
        chosen=await call('01-select','selection',dict(kind=kind,objective=graph['objective'],max_panels=graph['max_panels'],
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
                located=await call(f'{pid}-locate','locate',dict(kind=kind,target=safe_target,page_text=page['text'],alternate_texts=pj.text_context(page)['alternate_texts'],verified_evidence=evidence),Located,image_raw)
                if located.status!='located' or located.bbox is None:raise ValueError(located.reason)
                if pj.digest((folder/'source.pdf').read_bytes())!=graph['source_pdf_sha256']:raise ValueError('PDF changed during run.')
                transform=await run_in_threadpool(crop_pdf,folder,target.page,located.bbox,pd/'crop.png')
                pj.write_json(pd/'transform.json',transform)
                item['crop']=f'vision/{graph["id"]}/{pid}/crop.png'
                measured=await call(f'{pid}-measure','measure',dict(kind=kind,target=safe_target,page_text=page['text'],alternate_texts=pj.text_context(page)['alternate_texts'],verified_evidence=evidence,
                    coordinate_system='0–1000 normalized to supplied crop, top-left origin',width=transform['crop_width'],height=transform['crop_height']),Micrograph if kind=='micrograph' else XRD,(pd/'crop.png').read_bytes())
                if rejected:
                    measured.warnings.append('Some selection-stage condition quotes failed source checks and were excluded. Verify sample conditions independently.')
                result=await run_in_threadpool(build_result,pd,target,measured,transform,kind)
                if kind=='xrd' and graph.get('bind_candidates'):
                    from .xrd_binding import Bindings,apply_bindings
                    candidates=result['calibration_check'].get('image_first_candidates',[])
                    if candidates:
                        try:
                            binding=await call(f'{pid}-bind','bind_candidates',dict(kind=kind,
                                target=safe_target,verified_evidence=evidence,candidates=candidates,
                                instruction='Decide keep/reject/uncertain for EVERY candidate ID. Never invent IDs or coordinates. Reject text/artifacts; uncertain for unclear curve/sample identity. Use printed legend and verified evidence. All keeps remain pending review.'),Bindings,(pd/'candidates.png').read_bytes())
                            result=await run_in_threadpool(apply_bindings,pd,result,binding)
                        except ValueError as err:
                            result['warnings'].append('Candidate binding failed; original unverified proposals retained: '+str(err))
                            pj.write_json(pd/'result.json',result)
                item.update(status='needs_review',result=result,crop=f'vision/{graph["id"]}/{pid}/crop.png',overlay=f'vision/{graph["id"]}/{pid}/overlay.png')
            except ValueError as err:item.update(status='deferred',error=str(err)[:1500])
            if kind=='micrograph' and (pd/'region-candidates.json').exists():
                item['region_candidates']=pj.read_json(pd/'region-candidates.json')
                item['candidate_overlay']=f'vision/{graph["id"]}/{pid}/candidates.png'
            update(folder,graph)
        graph['status']='awaiting_review' if any(p['status']=='needs_review' for p in graph['panels']) else 'deferred'
    except Exception as err:
        graph.update(status='failed',error=str(err)[:1500] if isinstance(err,ValueError) else 'Local visual processing failed; inspect the saved stages.')
    finally:
        graph['finished_at']=pj.now();graph.pop('active_stage',None);update(folder,graph)


def panel(root,ident,kind,panel_id):
    folder=pj.job_path(root,ident);job=pj.read_json(folder/'job.json');run=job.get('vision_runs',{}).get(kind)
    if not run or run['status']=='running':raise ValueError('Visual run is not ready.')
    item=next((p for p in run['panels'] if p['id']==panel_id),None)
    if not item or item['status']!='needs_review':raise ValueError('No reviewable geometry for this panel.')
    if item.get('remeasure',{}).get('status')=='running':raise ValueError('Wait for visual remeasurement.')
    path=folder/'vision'/run['id']/item['id']
    if item.get('remeasure',{}).get('status')=='needs_review':path=path/item['remeasure']['id']
    if item.get('local_recompute',{}).get('status')=='needs_review':path=folder/'vision'/run['id']/item['id']/item['local_recompute']['id']
    result=pj.read_json(path/'result.json')
    if pj.digest((path/'crop.png').read_bytes())!=result['transform']['crop_sha256']:raise ValueError('Crop changed.')
    return folder,run,item,result,path


def begin_retry(root,ident,kind,panel_id,request):
    with pj.LOCK:
        folder=pj.job_path(root,ident);job=pj.read_json(folder/'job.json');run=job.get('vision_runs',{}).get(kind)
        if job['revision']!=request.expected_revision or pj.busy(job) or not run:raise ValueError('Run unavailable or job changed.')
        item=next((p for p in run['panels'] if p['id']==panel_id),None)
        if not item or item.get('remeasure') or item.get('local_recompute'):raise ValueError('Panel unavailable or already reprocessed. No automatic retry.')
        path=folder/'vision'/run['id']/item['id']
        if not (path/'crop.png').is_file() or not (path/'transform.json').is_file():raise ValueError('No archived crop; reimport for a new selection.')
        if pj.digest((path/'crop.png').read_bytes())!=pj.read_json(path/'transform.json')['crop_sha256']:raise ValueError('Crop changed.')
        item['remeasure']=dict(id=uuid4().hex,status='running',calls_attempted=0)
        run['status']='running';run['max_calls']+=1;update(folder,run)


async def remeasure(root,ident,kind,panel_id,send,rules):
    import asyncio,shutil
    from starlette.concurrency import run_in_threadpool
    folder=pj.job_path(root,ident);job=pj.snapshot(root,ident);run=job['vision_runs'][kind]
    item=next(p for p in run['panels'] if p['id']==panel_id);retry=item['remeasure']
    source=folder/'vision'/run['id']/panel_id;dest=source/retry['id'];dest.mkdir()
    try:
        shutil.copyfile(source/'crop.png',dest/'crop.png');shutil.copyfile(source/'transform.json',dest/'transform.json')
        transform=pj.read_json(dest/'transform.json');target=pg_target.model_validate(item['selection'])
        pages=pj.read_json(folder/'source/pages.json');evidence,rejected=validate_evidence(target,pages)
        cls=Micrograph if kind=='micrograph' else XRD
        body=request_body(rules,'measure',dict(kind=kind,objective=run['objective'],target=target.model_dump(exclude={'evidence','reason'}),
            verified_evidence=evidence,previous_attempt_notice='Prior attempt retained. Reassess the same crop using the coordinate-grid helper; do not guess unreadable detail.',
            width=transform['crop_width'],height=transform['crop_height']),cls,(dest/'crop.png').read_bytes())
        add_grid(body,(dest/'crop.png').read_bytes(),dest/'coordinate-grid.png')
        (dest/'requirements.md').write_text(rules,encoding='utf8');pj.write_json(dest/'input.json',body)
        retry['calls_attempted']=1;run['calls_attempted']+=1;update(folder,run)
        response=await asyncio.wait_for(send(body),timeout=110);pj.write_json(dest/'response.json',response)
        content,usage,error=completion(response);pj.write_json(dest/'usage.json',usage)
        if content is not None:(dest/'output.txt').write_text(content,encoding='utf8')
        if error:raise ValueError(error)
        measured=parse(content,cls);pj.write_json(dest/'parsed.json',measured.model_dump())
        if rejected:measured.warnings.append('Some condition evidence was excluded; verify independently.')
        result=await run_in_threadpool(build_result,dest,target,measured,transform,kind)
        item['previous_attempt']={k:v for k,v in item.items() if k not in ('remeasure','previous_attempt')}
        item.pop('project_id',None)
        item.update(result=result,status='needs_review',error=None,crop=f'vision/{run["id"]}/{panel_id}/{retry["id"]}/crop.png',overlay=f'vision/{run["id"]}/{panel_id}/{retry["id"]}/overlay.png')
        retry['status']='needs_review'
    except Exception as err:
        retry.update(status='failed',error=str(err)[:1500] if isinstance(err,ValueError) else 'Remeasurement failed; original proposal retained. No retry.')
        pj.write_json(dest/'error.json',{'error':retry['error']})
    finally:
        run['status']='awaiting_review' if any(p['status']=='needs_review' for p in run['panels']) else 'deferred'
        update(folder,run)


def recompute_xrd(root,ident,panel_id,request,kind='xrd'):
    if kind not in ('xrd','micrograph'):raise ValueError('Unknown visual kind.')
    import shutil
    with pj.LOCK:
        folder=pj.job_path(root,ident);job=pj.snapshot(root,ident);run=job.get('vision_runs',{}).get(kind)
        if job['revision']!=request.expected_revision or pj.busy(job) or not run:raise ValueError('Job changed or run unavailable.')
        item=next((p for p in run['panels'] if p['id']==panel_id),None)
        if not item or item.get('local_recompute'):raise ValueError('Panel missing or local recomputation already attempted.')
        base=folder/'vision'/run['id']/item['id'];source=base
        if item.get('remeasure'):source=base/item['remeasure']['id'];parsed=source/'parsed.json'
        else:parsed=base.parent/f'{panel_id}-measure'/'parsed.json'
        if not parsed.is_file():raise ValueError('No complete model geometry to recompute.')
        transform=pj.read_json(source/'transform.json')
        if pj.digest((source/'crop.png').read_bytes())!=transform['crop_sha256']:raise ValueError('Crop changed.')
        rid=uuid4().hex;dest=base/rid;dest.mkdir()
        for name in ('crop.png','transform.json'):shutil.copyfile(source/name,dest/name)
        shutil.copyfile(parsed,dest/'model-proposal.json')
        item['local_recompute']=dict(id=rid,status='failed',model_calls=0)
        try:
            result=build_result(dest,pg_target.model_validate(item['selection']),(XRD if kind=='xrd' else Micrograph).model_validate(pj.read_json(parsed)),transform,kind)
            item['local_previous_attempt']={k:v for k,v in item.items() if k not in ('local_recompute','local_previous_attempt')}
            item.pop('project_id',None)
            item.update(result=result,status='needs_review',error=None,crop=f'vision/{run["id"]}/{panel_id}/{rid}/crop.png',overlay=f'vision/{run["id"]}/{panel_id}/{rid}/overlay.png')
            item['local_recompute']['status']='needs_review';run['status']='awaiting_review'
        except ValueError as err:
            item['local_recompute']['error']=str(err);pj.write_json(dest/'error.json',{'error':str(err)})
        update(folder,run)
        return pj.snapshot(root,ident)
