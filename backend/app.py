import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL
from fastapi import FastAPI, HTTPException, Request, UploadFile, BackgroundTasks, Form
from starlette.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from PIL import Image, ImageOps, UnidentifiedImageError
from .models import Document, SaveRequest, PeakRequest, RegionRequest
from .regions import region_candidate
from .peaks import peak_candidates
from .references import catalog
from .candidates import candidate_results
from .plans import plan_results
from .reported import reported_results
from .llm_bridge import PreviewRequest, ValidationRequest, prepare, preview, validate_response
from .llm_live import DeepSeekSession, SendRequest, completion
from .summaries import object_summary
from .geometry import evaluate, validate_image_coordinates
from .demo import demo_image
from . import table_batches
from . import master_export
from . import paper_jobs
from . import paper_graphs
from . import graph_refine
from . import graph_mapping
from . import table_evidence
from . import reported_mapping
from . import paper_vision

ROOT=Path(__file__).resolve().parent.parent
DATA=Path(os.environ.get('TIFIGURE_DATA', 'D:/TiFigureLab/data' if os.name=='nt' else str(Path.home()/'.tifigure-lab')))
DATA.mkdir(parents=True,exist_ok=True)
IMAGES=DATA/'images'; IMAGES.mkdir(exist_ok=True)
REFERENCES=DATA/'references'; REFERENCES.mkdir(exist_ok=True)
DB=DATA/'workspace.sqlite3'
TABLE_SOURCE_ROOT=Path(os.environ.get('TIFIGURE_SOURCE_ROOT',str(ROOT/'data/papers')))
Image.MAX_IMAGE_PIXELS=40_000_000
image_lock=threading.Lock()


def connect():
    c=sqlite3.connect(DB,timeout=10)
    c.row_factory=sqlite3.Row
    return c


with connect() as c:
    c.execute('CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT, revision INTEGER, document TEXT, updated_at TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS snapshots (project_id TEXT, revision INTEGER, document TEXT, saved_at TEXT, PRIMARY KEY(project_id,revision))')

app=FastAPI(title='TiFigure Lab',version='0.8.1-dev')
deepseek = DeepSeekSession()
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver'])


@app.middleware('http')
async def local_mutations(request: Request, call_next):
    if request.method not in ('GET','HEAD','OPTIONS'):
        if request.headers.get('x-tifigure-client')!='workbench':
            return JSONResponse({'detail':'A local workbench request is required.'},status_code=403)
        origin=request.headers.get('origin')
        if origin and origin!=str(request.base_url).rstrip('/'):
            return JSONResponse({'detail':'Cross-origin writes are not allowed.'},status_code=403)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Cache-Control']='no-store'
    return response


def image_meta(image_id):
    if not re.fullmatch('[0-9a-f]{64}',image_id):
        raise HTTPException(404,'Image not found.')
    try: return json.loads((IMAGES/image_id/'meta.json').read_text('utf-8'))
    except FileNotFoundError: raise HTTPException(404,'Image not found.') from None


def store_image(raw, filename):
    if len(raw)>20*1024*1024:
        raise HTTPException(413,'Images must be 20 MB or smaller.')
    try:
        with Image.open(io.BytesIO(raw)) as original:
            if original.format not in ('PNG','JPEG','BMP','TIFF','WEBP'):
                raise ValueError('Unsupported image format.')
            if getattr(original,'n_frames',1)!=1:
                raise ValueError('Multi-frame images are not supported. Export one frame first.')
            if original.width*original.height>40_000_000:
                raise ValueError('Images must contain at most 40 million pixels.')
            canonical=ImageOps.exif_transpose(original).convert('RGBA')
            canonical.load()
    except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError) as err:
        raise HTTPException(422,f'Cannot read this image: {err}') from None
    ident=hashlib.sha256(raw).hexdigest()
    folder=IMAGES/ident
    with image_lock:
        if (folder/'meta.json').exists(): return image_meta(ident)
        folder.mkdir(exist_ok=True)
        (folder/'original.bin').write_bytes(raw)
        canonical.save(folder/'image.png')
        meta={'id':ident,'filename':filename[:240], 'width':canonical.width,'height':canonical.height,
              'url':f'/api/images/{ident}/image','sha256':ident,
              'coordinate_system':'EXIF-oriented canonical image pixels'}
        (folder/'meta.json').write_text(json.dumps(meta,ensure_ascii=False),'utf-8')
    return meta


def checked(doc):
    meta=image_meta(doc.image_id)
    try: validate_image_coordinates(doc,meta['width'],meta['height'])
    except ValueError as err: raise HTTPException(422,str(err)) from None
    reference=read_reference(doc.reference_id) if doc.reference_id else None
    paths={t['path'] for t in reference['targets']} if reference else set()
    if any(m.reference_path and m.reference_path not in paths for m in doc.measurements):
        raise HTTPException(422,'A linked field does not exist in the selected reference master.')
    results=evaluate(doc)
    results['planning']=plan_results(doc,reference,results['calibration'])
    reviewed_plans={p['id'] for p in results['planning']['plans'] if p['status']=='reviewed'}
    by_id={r['id']:r for r in results['measurements']}
    for m in doc.measurements:
        r=by_id[m.id]
        if m.plan_id and m.plan_id not in reviewed_plans:
            r.update(valid=False,values={},units={},error='Linked extraction plan or evidence needs review.')
        elif m.plan_id:
            p=next(p for p in doc.plans if p.id==m.plan_id)
            expected='point' if p.tool in ('point','peak') else 'line' if p.tool=='line' else 'area'
            if p.tool=='transcription' or m.kind!=expected or m.sample!=' | '.join(p.sample_ids):
                r.update(valid=False,values={},units={},error='Measurement kind or sample label does not match its linked plan.')
        if not r['valid'] or not m.sample.strip() or m.review_basis!=r['review_basis']:
            m.status='needs_review'
    results['summary']=object_summary(doc,results['measurements'])
    results['candidates']=candidate_results(doc,results,reference)
    results['reported_values']=reported_results(doc,reference,results['planning'])
    return meta,results


def read_reference(ident):
    if not re.fullmatch('[0-9a-f]{64}',ident): raise HTTPException(404,'Reference not found.')
    try:
        stored=json.loads((REFERENCES/(ident+'.index.json')).read_text('utf-8'))
        raw=(REFERENCES/(ident+'.json')).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=ident:raise HTTPException(422,'Reference bytes do not match the recorded SHA-256.')
        # Rebuild from immutable bytes so older imports acquire current context fields.
        index=catalog(json.loads(raw.decode('utf-8-sig')))
        return {**index,'id':ident,'filename':stored['filename']}
    except FileNotFoundError: raise HTTPException(404,'Reference not found.') from None


@app.post('/api/references')
async def import_reference(file: UploadFile):
    raw=await file.read(20*1024*1024+1);await file.close()
    if len(raw)>20*1024*1024: raise HTTPException(413,'Reference JSON must be 20 MB or smaller.')
    try:
        data=json.loads(raw.decode('utf-8-sig'),parse_constant=lambda v: (_ for _ in ()).throw(ValueError('Non-finite JSON value.')))
        json.dumps(data,allow_nan=False)
        index=catalog(data)
    except (ValueError,UnicodeError,RecursionError) as err: raise HTTPException(422,f'Cannot index this master: {err}') from None
    ident=hashlib.sha256(raw).hexdigest()
    index.update(id=ident,filename=file.filename or 'master.json')
    (REFERENCES/(ident+'.json')).write_bytes(raw)
    (REFERENCES/(ident+'.index.json')).write_text(json.dumps(index,ensure_ascii=False),'utf-8')
    return index


@app.get('/api/references/{ident}')
def reference_index(ident: str):
    return read_reference(ident)


@app.post('/api/peak-candidates')
def peaks(request: PeakRequest):
    image_meta(request.image_id)
    with Image.open(IMAGES/request.image_id/'image.png') as image:
        try: result=peak_candidates(image,request.point.x,request.point.y,request.radius)
        except ValueError as err: raise HTTPException(422,str(err)) from None
    return {**result,'algorithm':'local-raster-peak/0.1','radius_px':request.radius,'click':request.point.model_dump()}


@app.post('/api/region-candidate')
def region(request: RegionRequest):
    image_meta(request.image_id)
    with Image.open(IMAGES/request.image_id/'image.png') as image:
        try:return region_candidate(image,request.point.x,request.point.y,request.radius,request.tolerance)
        except ValueError as err:raise HTTPException(422,str(err)) from None


@app.get('/api/health')
def health():
    return {'name':'TiFigure Lab','version':'0.8.1-dev','storage':str(DATA.resolve()),'project_root':str(ROOT.resolve()),'model_connected':deepseek.status()['configured']}


@app.get('/api/llm/connection')
def llm_connection():
    return deepseek.status()


@app.post('/api/llm/connection')
async def llm_configure(request: Request):
    # Parse separately: Pydantic validation errors may echo secret input values.
    try:
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 2048: raise ValueError()
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {'api_key'}: raise ValueError()
        return deepseek.configure(data['api_key'])
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(422, 'Enter a valid API key (10–512 ASCII characters, no spaces). Nothing was stored.') from None


@app.delete('/api/llm/connection')
def llm_clear():
    return deepseek.clear()


@app.post('/api/llm/send')
async def llm_send(request: SendRequest):
    if request.config.provider.strip().lower() != 'deepseek':
        raise HTTPException(422, 'Only the DeepSeek live adapter is enabled.')
    packet = llm_packet(request)
    if packet['request_hash'] != request.request_hash:
        raise HTTPException(409, 'Context changed. Prepare a new preview before sending.')
    if len(request.document.plans) >= 100:
        raise HTTPException(422, 'Workspace plan limit reached. Split the work before making another call.')
    if deepseek.lock.locked(): raise HTTPException(409, 'A DeepSeek request is already running.')
    async with deepseek.lock:
        if not deepseek.status()['configured']: raise HTTPException(422, 'Set your API key under DeepSeek connection first.')
        if request.call_id in deepseek.attempted: raise HTTPException(409, 'This call was already attempted. Prepare a new preview to deliberately try again.')
        if len(deepseek.attempted) >= 1000: raise HTTPException(409, 'Session call limit reached. Restart the local service.')
        deepseek.attempted.add(request.call_id)
        try:
            import asyncio
            result = await asyncio.wait_for(deepseek.send(packet['request_body']), timeout=100)
        except TimeoutError:
            raise HTTPException(504, 'Request exceeded 100 seconds. Billing status is unknown; no automatic retry.') from None
        except ValueError as err:
            raise HTTPException(502, str(err)) from None
    content, usage, error = completion(result)
    response = {'network_executed':True, 'usage':usage, 'response_text':content or '', 'validated':None, 'error':error}
    if content is not None:
        try:
            validation = ValidationRequest(document=request.document, config=request.config, request_hash=request.request_hash,
                                           response_text=content, import_kind='pasted_unverified')
            valid = validate_response(validation, packet)
            if len(request.document.plans)+len(valid['plans']) > 100: raise ValueError('Workspace plan limit exceeded; response retained but not imported.')
            for plan in valid['plans']:
                plan['model_provenance'].update(import_kind='live_api', network_executed=True, usage=usage,
                    response_model=str(result.get('model') or '')[:200], called_at=datetime.now(timezone.utc).isoformat())
            valid.update(network_executed=True, notice='Received through the DeepSeek API. Local schema checks passed; scientific claims and all plans still require human review.')
            response['validated'] = valid
        except ValueError:
            response['error'] = 'The API returned output that failed local plan validation. Inspect the response; no plans were imported. Usage is shown below.'
    return response


def llm_packet(request):
    doc=request.document;meta=image_meta(doc.image_id)
    reference=read_reference(doc.reference_id) if doc.reference_id else None
    image_bytes=(IMAGES/doc.image_id/'image.png').read_bytes() if request.config.include_image else None
    try:return prepare(doc,request.config,reference,meta,image_bytes)
    except ValueError as err:raise HTTPException(422,str(err)) from None


@app.post('/api/llm/preview')
def llm_preview(request: PreviewRequest):
    return preview(llm_packet(request))


@app.post('/api/llm/export-request')
def llm_export_request(request: PreviewRequest):
    packet=llm_packet(request);folder=DATA/'exports';folder.mkdir(exist_ok=True)
    target=folder/(packet['request_hash']+'.llm-request.json')
    target.write_text(json.dumps(packet,ensure_ascii=False,indent=2,allow_nan=False),'utf-8')
    return {'path':str(target),'request_hash':packet['request_hash'],'network_executed':False}


@app.post('/api/llm/validate-response')
def llm_validate_response(request: ValidationRequest):
    try:
        result=validate_response(request,llm_packet(request))
        if len(request.document.plans)+len(result['plans'])>100:raise ValueError('Workspace limit: 100 plans. Split the work into projects.')
        return result
    except ValueError as err:raise HTTPException(422,str(err)) from None


@app.post('/api/images')
async def upload_image(file: UploadFile):
    raw=await file.read(20*1024*1024+1)
    await file.close()
    return store_image(raw,file.filename or 'Imported image')


@app.post('/api/demos/{mode}')
def demo(mode: str):
    if mode not in ('graph','micrograph'): raise HTTPException(404,'Demo not found.')
    return store_image(demo_image(mode),f'Synthetic {mode} practice.png')


@app.get('/api/images/{image_id}/image')
def get_image(image_id: str):
    image_meta(image_id)
    return FileResponse(IMAGES/image_id/'image.png',media_type='image/png')


@app.post('/api/evaluate')
def evaluate_document(doc: Document):
    _,results=checked(doc)
    return results


@app.get('/api/projects')
def list_projects():
    with connect() as c:
        return [dict(r) for r in c.execute('SELECT id,name,revision,updated_at FROM projects ORDER BY updated_at DESC')]


def project_row(project_id):
    try: UUID(project_id)
    except ValueError: raise HTTPException(404,'Project not found.') from None
    with connect() as c: row=c.execute('SELECT * FROM projects WHERE id=?',(project_id,)).fetchone()
    if not row: raise HTTPException(404,'Project not found.')
    return row


def envelope(row):
    doc=Document.model_validate_json(row['document'])
    meta,results=checked(doc)
    return {'id':row['id'],'revision':row['revision'],'updated_at':row['updated_at'],
            'document':doc.model_dump(),'image':meta,'results':results,
            'reference':read_reference(doc.reference_id) if doc.reference_id else None}


@app.get('/api/projects/{project_id}')
def get_project(project_id: str):
    return envelope(project_row(project_id))


def checked_table_batch(case_id):
    try: return table_batches.load_batch(ROOT,TABLE_SOURCE_ROOT,case_id)
    except (ValueError,OSError,KeyError,TypeError) as err:
        raise HTTPException(422,f'Table batch unavailable: {err}') from None


@app.get('/api/table-batches')
def list_table_batches():
    try: configured=table_batches.cases(ROOT)
    except (ValueError,OSError,KeyError) as err: raise HTTPException(422,str(err)) from None
    items=[]
    for case in configured:
        try:
            batch=checked_table_batch(case.id)
            items.append(dict(case_id=case.id,table=case.source.table,paper=case.source.paper,status='ready',summary=batch['comparison']['summary']))
        except HTTPException as err:
            items.append(dict(case_id=case.id,table=case.source.table,paper=case.source.paper,status='unavailable',error=err.detail))
    return {'items':items}


@app.get('/api/table-batches/{case_id}')
def get_table_batch(case_id: str):
    batch=checked_table_batch(case_id)
    with connect() as c: exists=c.execute('SELECT id FROM projects WHERE id=?',(batch['project_id'],)).fetchone() is not None
    return {**table_batches.preview(batch),'existing_project':exists,'image_url':f'/api/table-batches/{case_id}/source-image'}


@app.get('/api/table-batches/{case_id}/source-image')
def table_batch_image(case_id: str):
    batch=checked_table_batch(case_id)
    return Response(batch['image_raw'],media_type='image/png')


@app.post('/api/table-batches/{case_id}/import')
def import_table_batch(case_id: str, request: table_batches.BatchImportRequest):
    batch=checked_table_batch(case_id)
    if request.preview_hash != batch['preview_hash']:
        raise HTTPException(409,'Batch changed since preview. Refresh it before importing.')
    def existing_result():
        existing=get_project(batch['project_id'])
        if existing['document']['image_id']!=batch['image_sha256'] or existing['document']['reference_id']!=sha256_batch_master:
            raise HTTPException(409,'Registered project has a different image or reference; it was not overwritten.')
        return {'project_id':batch['project_id'],'reused':True,'revision':existing['revision']}
    sha256_batch_master=hashlib.sha256(batch['master_raw']).hexdigest()
    with connect() as c: existing=c.execute('SELECT id FROM projects WHERE id=?',(batch['project_id'],)).fetchone()
    if existing: return existing_result()
    index=catalog(json.loads(batch['master_raw'].decode('utf-8-sig')))
    index.update(id=sha256_batch_master,filename=Path(batch['case'].comparison.master).name)
    (REFERENCES/(sha256_batch_master+'.json')).write_bytes(batch['master_raw'])
    (REFERENCES/(sha256_batch_master+'.index.json')).write_text(json.dumps(index,ensure_ascii=False),'utf-8')
    image=store_image(batch['image_raw'],batch['image_path'].name)
    doc=table_batches.build_document(batch,image['id'],sha256_batch_master)
    try:
        saved=save_project(batch['project_id'],SaveRequest(document=doc,expected_revision=0))
    except HTTPException as err:
        if err.status_code==409:return existing_result()
        raise
    return {'project_id':saved['id'],'reused':False,'revision':saved['revision']}


@app.put('/api/projects/{project_id}')
def save_project(project_id: str, payload: SaveRequest):
    try: UUID(project_id)
    except ValueError: raise HTTPException(422,'Invalid project ID.') from None
    doc=payload.document.model_copy(deep=True)
    _,results=checked(doc)
    now=datetime.now(timezone.utc).isoformat()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        oldrow=c.execute('SELECT * FROM projects WHERE id=?',(project_id,)).fetchone()
        revision=oldrow['revision'] if oldrow else 0
        if revision!=payload.expected_revision:
            raise HTTPException(409,'This project changed in another window. Reload it before saving.')
        computed={r['id']:r for r in results['measurements']}
        for m in doc.measurements:
            r=computed[m.id]
            if not r['valid'] or not m.sample.strip() or m.review_basis!=r['review_basis']:
                m.status='needs_review'
        encoded=doc.model_dump_json()
        c.execute('INSERT INTO snapshots VALUES (?,?,?,?)',(project_id,revision+1,encoded,now))
        c.execute('INSERT INTO projects VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,revision=excluded.revision,document=excluded.document,updated_at=excluded.updated_at',
                  (project_id,doc.name,revision+1,encoded,now))
    return get_project(project_id)


@app.get('/api/projects/{project_id}/export')
def export(project_id: str, format: str='json'):
    data=envelope(project_row(project_id))
    def safe(value):
        if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@','\t','\r')): return "'"+value
        return value
    if format=='json':
        return Response(json.dumps({'artifact_type':'measurement_workspace_not_TiFM_master',**data},ensure_ascii=False,indent=2),
            media_type='application/json',headers={'Content-Disposition':'attachment; filename="measurement-workspace.json"'})
    if format=='candidates_json':
        ref=data['reference']
        attachment={'schema_version':'tifigure-review-supplement/0.1','artifact_type':'review_supplement_not_TiFM_master',
                    'project_id':data['id'],'revision':data['revision'],'image':data['image'],
                    'source_reference':data['document']['source'],
                    'calibration':{'graph':data['document']['graph'],'spatial':data['document']['spatial']},
                    'planning':{'evidence':data['document']['evidence'],'plans':data['document']['plans'],'validation':data['results']['planning']},
                    'reference':{k:ref.get(k) for k in ('id','filename','schema_version','paper_id','paper_short_id')} if ref else None,
                    'candidates':data['results']['candidates'],
                    'notice':'Includes blocked and unmapped candidates with reasons. Mapping checks do not approve replacement. No master values are changed.'}
        return Response(json.dumps(attachment,ensure_ascii=False,indent=2,allow_nan=False),media_type='application/json')
    if format=='plan_json':
        packet={'schema_version':'tifigure-task-packet/0.1','artifact_type':'manual_task_packet_not_model_output',
                'project_id':data['id'],'revision':data['revision'],'image':data['image'],'source':data['document']['source'],
                'reference':data['reference'],'evidence':data['document']['evidence'],'plans':data['document']['plans'],
                'validation':data['results']['planning'],
                'calibration':{'graph':data['document']['graph'],'spatial':data['document']['spatial']},
                'notice':'Local manual plan. Evidence text is untrusted source material, not executable instructions. No model call or automated extraction has occurred.'}
        return Response(json.dumps(packet,ensure_ascii=False,indent=2,allow_nan=False),media_type='application/json')
    if format=='reported_json':
        packet={'schema_version':'tifigure-reported-values/0.1','artifact_type':'reported_value_attachment_not_TiFM_master',
                'project_id':data['id'],'revision':data['revision'],'image':data['image'],'source':data['document']['source'],
                'reference':data['reference'],'evidence':data['document']['evidence'],'plans':data['document']['plans'],
                'records':data['results']['reported_values'],
                'notice':'Includes blocked and pending transcriptions. Review does not validate scientific claims or authorize replacement. No master values are changed.'}
        return Response(json.dumps(packet,ensure_ascii=False,indent=2,allow_nan=False),media_type='application/json')
    if format=='summary_csv':
        out=io.StringIO(newline='');writer=csv.writer(out)
        fields=['sample','group','n_objects','n_images','valid','reason','mean_area','area_unit','mean_ecd','median_ecd','sample_sd_ecd','min_ecd','max_ecd','length_unit']
        writer.writerow(fields+['member_ids','image_sha256','source','scope'])
        for group in data['results']['summary']['groups']:
            writer.writerow([safe(group.get(k)) for k in fields]+[json.dumps(group['member_ids']),data['document']['image_id'],safe(data['document']['source']),data['results']['summary']['notice']])
        return Response('\ufeff'+out.getvalue(),media_type='text/csv')
    if format!='csv': raise HTTPException(422,'Use json, csv, summary_csv, candidates_json, plan_json or reported_json.')
    out=io.StringIO(newline=''); writer=csv.writer(out)
    writer.writerow(['measurement_id','sample','label','kind','status','metric','value','unit','source','note','valid','error','reference_sha256','reference_path','acquisition','object_group','include_in_summary','exclusion_reason'])
    computed={r['id']:r for r in data['results']['measurements']}
    for m in data['document']['measurements']:
        r=computed[m['id']]
        for key,value in (r['values'].items() or [('','')]):
            writer.writerow([safe(v) for v in [m['id'],m['sample'],m['label'],m['kind'],m['status'],key,value,
                r['units'].get(key,''),data['document']['source'],m['note'],r['valid'],r.get('error',''),
                data['document'].get('reference_id') or '',m.get('reference_path') or '',m.get('acquisition','manual'),
                m.get('group_label',''),m.get('include_in_summary',False),m.get('exclusion_reason','')]])
    return Response('\ufeff'+out.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="measurements.csv"'})


@app.post('/api/projects/{project_id}/export-file')
def export_file(project_id: str, format: str='json'):
    row=project_row(project_id)
    response=export(project_id,format)
    folder=DATA/'exports';folder.mkdir(exist_ok=True)
    extension={'summary_csv':'summary.csv','candidates_json':'review-supplement.json','plan_json':'task-packet.json','reported_json':'reported-values.json'}.get(format,format)
    target=folder/f'{project_id}-r{row["revision"]}.{extension}'
    target.write_bytes(response.body)
    return {'path':str(target),'artifact_type':{'candidates_json':'review_supplement_not_TiFM_master','plan_json':'manual_task_packet_not_model_output','reported_json':'reported_value_attachment_not_TiFM_master'}.get(format,'measurement_workspace_not_TiFM_master')}


def master_copy_context(project_id):
    data=get_project(project_id)
    if not data['document']['reference_id']:raise HTTPException(422,'Import a reference master before preparing a copy.')
    raw=(REFERENCES/(data['document']['reference_id']+'.json')).read_bytes()
    try:
        findings,hashes=master_export.source_findings(ROOT,TABLE_SOURCE_ROOT,raw)
        preview=master_export.prepare(data,raw,findings,hashes)
    except (ValueError,OSError,KeyError,TypeError) as err:raise HTTPException(422,f'Cannot prepare master copy: {err}') from None
    return data,raw,preview


@app.get('/api/projects/{project_id}/master-copy-preview')
def preview_master_copy(project_id: str):
    return master_copy_context(project_id)[2]


@app.post('/api/projects/{project_id}/master-copy')
def export_master_copy(project_id: str,request: master_export.MasterCopyRequest):
    data,raw,preview=master_copy_context(project_id)
    if request.preview_hash!=preview['preview_hash']:raise HTTPException(409,'Project or source review changed. Refresh the preview.')
    try: bundle,audit=master_export.build_bundle(data,raw,preview,request)
    except ValueError as err:raise HTTPException(422,str(err)) from None
    folder=DATA/'exports';folder.mkdir(exist_ok=True)
    target=folder/f'{project_id}-r{data["revision"]}-master-copy-{uuid4().hex[:12]}.zip'
    with target.open('xb') as stream:stream.write(bundle)
    return dict(path=str(target),adopted_fields=len(audit['adopted_fields']),
        changed_values=sum(c['numeric_change'] for c in audit['adopted_fields']),
        untouched_original_fields_not_revalidated=True,downstream_reader_tested=False,
        artifact_type=audit['artifact_type'])


PAPER_JOBS=DATA/'paper-jobs'


@app.get('/api/paper-jobs')
def papers_list():
    return {'items':paper_jobs.list_jobs(PAPER_JOBS)}


@app.post('/api/paper-jobs')
async def papers_create(file: UploadFile, paper: str=Form(...), scope: str=Form('all')):
    raw=await file.read(paper_jobs.MAX_PDF+1);await file.close()
    try:
        return await run_in_threadpool(paper_jobs.create,PAPER_JOBS,raw,file.filename or 'paper.pdf',paper,
            (ROOT/'configs/paper-extraction.md').read_text('utf-8'),scope)
    except ValueError as err:raise HTTPException(422,str(err)) from None


@app.get('/api/paper-jobs/{ident}')
def papers_get(ident: str):
    try:return paper_jobs.snapshot(PAPER_JOBS,ident)
    except ValueError as err:raise HTTPException(404,str(err)) from None


@app.get('/api/paper-jobs/{ident}/request')
def papers_request(ident: str):
    try:
        path=paper_jobs.job_path(PAPER_JOBS,ident)/'source/request.json'
        if not path.exists():raise ValueError('PDF preparation did not produce a request.')
        return paper_jobs.read_json(path)
    except ValueError as err:raise HTTPException(422,str(err)) from None


@app.get('/api/paper-jobs/{ident}/assets/{name:path}')
def papers_asset(ident: str,name: str):
    try:return FileResponse(paper_jobs.asset(PAPER_JOBS,ident,name))
    except ValueError as err:raise HTTPException(404,str(err)) from None


async def execute_paper_job(ident,body):
    import asyncio
    envelope={};content=None;usage={};error=None
    try:
        async with deepseek.lock:
            envelope=await asyncio.wait_for(deepseek.send(body),timeout=100)
        content,usage,error=completion(envelope)
    except (ValueError,asyncio.TimeoutError) as err:
        error=str(err) or 'Model deadline exceeded. Billing is unknown; no automatic retry.'
    except Exception:
        error='Unexpected provider failure. No automatic retry; inspect this archived job.'
    await run_in_threadpool(paper_jobs.finish,PAPER_JOBS,ident,envelope,content,usage,error)


@app.post('/api/paper-jobs/{ident}/run')
async def papers_run(ident: str,request: paper_jobs.RunRequest,tasks: BackgroundTasks):
    if not deepseek.status()['configured']:raise HTTPException(422,'Configure an API key first. No call was attempted.')
    try:body=paper_jobs.claim(PAPER_JOBS,ident,request)
    except ValueError as err:raise HTTPException(409,str(err)) from None
    tasks.add_task(execute_paper_job,ident,body)
    return paper_jobs.snapshot(PAPER_JOBS,ident)


@app.post('/api/paper-jobs/{ident}/records/{record_id}/review')
def papers_review(ident: str,record_id: str,request: paper_jobs.ReviewRequest):
    try:return paper_jobs.review(PAPER_JOBS,ident,record_id,request)
    except ValueError as err:raise HTTPException(409,str(err)) from None


@app.post('/api/paper-jobs/{ident}/table-evidence')
def papers_table_evidence(ident: str,request: table_evidence.Request):
    try:return table_evidence.run(PAPER_JOBS,ident,request)
    except ValueError as err:raise HTTPException(422,str(err)) from None


@app.post('/api/paper-jobs/{ident}/reported-mapping')
def papers_reported_mapping(ident: str,request: reported_mapping.Request):
    read_reference(request.reference_id)
    raw=(REFERENCES/(request.reference_id+'.json')).read_bytes()
    try:return reported_mapping.run(PAPER_JOBS,ident,request,raw)
    except ValueError as err:raise HTTPException(422,str(err)) from None


@app.get('/api/paper-jobs/{ident}/reported-mapping/preview')
def papers_reported_mapping_preview(ident: str):
    try:result=reported_mapping.preview(PAPER_JOBS,ident)
    except ValueError as err:raise HTTPException(409,str(err)) from None
    return JSONResponse(result,headers={'Content-Disposition':f'attachment; filename="pending-fields-{ident}.json"'})


@app.get('/api/paper-jobs/{ident}/archive')
def papers_archive(ident: str):
    try:
        extras={}
        state=paper_jobs.snapshot(PAPER_JOBS,ident)
        project_ids=set()
        for run in state.get('vision_runs',{}).values():
            project_ids.update(p['project_id'] for p in run.get('panels',[]) if p.get('project_id'))
            project_ids.update(p['previous_attempt']['project_id'] for p in run.get('panels',[]) if p.get('previous_attempt',{}).get('project_id'))
            project_ids.update(p['local_previous_attempt']['project_id'] for p in run.get('panels',[]) if p.get('local_previous_attempt',{}).get('project_id'))
            project_ids.update(p['fallback_project_id'] for p in run.get('panels',[]) if p.get('fallback_project_id'))
            project_ids.update(pid for p in run.get('panels',[]) for pid in p.get('region_project_ids',[]))
        for p in state.get('graph_run',{}).get('panels',[]):
            project_ids.update(i for i in (p.get('project_id'),p.get('original_proposal',{}).get('project_id')) if i)
        for project_id in sorted(project_ids):
            with connect() as c:
                current=c.execute('SELECT * FROM projects WHERE id=?',(project_id,)).fetchone()
                history=[dict(r) for r in c.execute('SELECT revision,document,saved_at FROM snapshots WHERE project_id=? ORDER BY revision',(project_id,))]
            if current:
                extras[f'workspaces/{project_id}.json']=json.dumps(dict(current=envelope(current),history=history),ensure_ascii=False,allow_nan=False).encode()
                reference_id=json.loads(current['document']).get('reference_id')
                if reference_id:
                    read_reference(reference_id)
                    extras[f'references/{reference_id}.json']=(REFERENCES/(reference_id+'.json')).read_bytes()
        raw=paper_jobs.bundle(PAPER_JOBS,ident,extras)
    except ValueError as err:raise HTTPException(409,str(err)) from None
    return Response(raw,media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="paper-job-{ident}.zip"'})


@app.post('/api/paper-jobs/{ident}/vision/run')
async def papers_vision_run(ident: str,request: paper_vision.Run,tasks: BackgroundTasks):
    if not deepseek.status()['configured']:raise HTTPException(422,'Configure an API key first. No call was attempted.')
    rules=(ROOT/'configs/visual-extraction.md').read_text('utf-8')
    try:paper_vision.begin(PAPER_JOBS,ident,request,rules)
    except ValueError as err:raise HTTPException(409,str(err)) from None
    async def send(body):
        async with deepseek.lock:return await deepseek.send(body)
    tasks.add_task(paper_vision.execute,PAPER_JOBS,ident,request.kind,send,rules)
    return paper_jobs.snapshot(PAPER_JOBS,ident)


@app.post('/api/paper-jobs/{ident}/vision/{kind}/{panel_id}/open')
def papers_vision_open(ident: str,kind: str,panel_id: str):
    if kind not in ('micrograph','xrd'):raise HTTPException(422,'Unknown visual branch.')
    with paper_jobs.LOCK:
        try:folder,run,item,result,path=paper_vision.panel(PAPER_JOBS,ident,kind,panel_id)
        except ValueError as err:raise HTTPException(422,str(err)) from None
        suffix=item.get('remeasure',{}).get('id','') if item.get('remeasure',{}).get('status')=='needs_review' else ''
        if item.get('local_recompute',{}).get('status')=='needs_review':suffix=item['local_recompute']['id']
        project_id=str(uuid5(NAMESPACE_URL,f'tifigure-paper-vision:{ident}:{run["id"]}:{panel_id}:{suffix}'))
        with connect() as c:exists=c.execute('SELECT id FROM projects WHERE id=?',(project_id,)).fetchone() is not None
        if not exists:
            meta=store_image((path/'crop.png').read_bytes(),f'{ident}-{kind}-{panel_id}.png')
            state=paper_jobs.snapshot(PAPER_JOBS,ident)
            doc=paper_vision.workspace(result,meta['id'],state['paper'],
                f'Paper job {ident}; visual run {run["id"]}; {result["selection"]["locator"]}; page {item["page"]}. Unreviewed LLM proposals with local tools; full evidence in archive.')
            save_project(project_id,SaveRequest(expected_revision=0,document=doc))
        item['project_id']=project_id;paper_vision.update(folder,run)
        return dict(project_id=project_id,reused=exists,job=paper_jobs.snapshot(PAPER_JOBS,ident))


@app.post('/api/paper-jobs/{ident}/vision/{kind}/{panel_id}/open-source')
def papers_vision_source(ident: str,kind: str,panel_id: str):
    if kind not in ('micrograph','xrd'):raise HTTPException(422,'Unknown visual branch.')
    with paper_jobs.LOCK:
        try:
            folder=paper_jobs.job_path(PAPER_JOBS,ident);state=paper_jobs.snapshot(PAPER_JOBS,ident)
            run=state.get('vision_runs',{}).get(kind)
            if not run or paper_jobs.busy(state):raise ValueError('Wait for the visual run to finish.')
            item=next((p for p in run['panels'] if p['id']==panel_id),None)
            if not item:raise ValueError('Panel not found.')
            path=folder/'vision'/run['id']/item['id'];transform=paper_jobs.read_json(path/'transform.json')
            raw=(path/'crop.png').read_bytes()
            if paper_jobs.digest(raw)!=transform['crop_sha256']:raise ValueError('Crop changed.')
        except (ValueError,FileNotFoundError) as err:raise HTTPException(422,'No intact archived crop available for manual fallback.') from None
        project_id=str(uuid5(NAMESPACE_URL,f'tifigure-visual-manual:{ident}:{run["id"]}:{panel_id}'))
        with connect() as c:exists=c.execute('SELECT id FROM projects WHERE id=?',(project_id,)).fetchone() is not None
        if not exists:
            meta=store_image(raw,f'{ident}-{kind}-manual.png')
            doc=Document(image_id=meta['id'],mode='micrograph' if kind=='micrograph' else 'graph',name=f'{state["paper"]} manual fallback {item["locator"]}'[:200],
                source=f'Paper job {ident}; visual run {run["id"]}; page {item["page"]}; {item["locator"]}. Manual fallback: no model calibration or measurements adopted.'[:2000])
            save_project(project_id,SaveRequest(expected_revision=0,document=doc))
        item['fallback_project_id']=project_id;paper_vision.update(folder,run)
        return dict(project_id=project_id,reused=exists,job=paper_jobs.snapshot(PAPER_JOBS,ident))


from .micro_candidates import RegionSelection,selection_document

@app.post('/api/paper-jobs/{ident}/vision/micrograph/{panel_id}/propose-regions')
def papers_propose_regions(ident: str,panel_id: str,request: paper_vision.Retry):
    from .micro_candidates import save_candidates
    from .vision_tools import Micrograph
    with paper_jobs.LOCK:
        try:
            folder=paper_jobs.job_path(PAPER_JOBS,ident);state=paper_jobs.snapshot(PAPER_JOBS,ident)
            if state['revision']!=request.expected_revision or paper_jobs.busy(state):raise ValueError('Job changed or busy.')
            run=state.get('vision_runs',{}).get('micrograph')
            if not run:raise ValueError('No micrograph run.')
            item=next((p for p in run['panels'] if p['id']==panel_id),None)
            if not item:raise ValueError('Panel not found.')
            path=folder/'vision'/run['id']/item['id'];raw=(path/'crop.png').read_bytes()
            if paper_jobs.digest(raw)!=paper_jobs.read_json(path/'transform.json')['crop_sha256']:raise ValueError('Crop changed.')
            if (path/'region-candidates.json').exists():report=paper_jobs.read_json(path/'region-candidates.json')
            else:
                parsed=Micrograph.model_validate(paper_jobs.read_json(path.parent/(panel_id+'-measure')/'parsed.json'))
                if parsed.roi is None:raise ValueError('No model ROI available; use manual fallback.')
                with Image.open(io.BytesIO(raw)) as im:
                    w,h=im.size;b=parsed.roi;report=save_candidates(im,(b.left*w/1000,b.top*h/1000,b.right*w/1000,b.bottom*h/1000),path)
            item['region_candidates']=report;item['candidate_overlay']=f'vision/{run["id"]}/{panel_id}/candidates.png'
            paper_vision.update(folder,run);return paper_jobs.snapshot(PAPER_JOBS,ident)
        except (ValueError,FileNotFoundError) as err:raise HTTPException(409,str(err)) from None


@app.post('/api/paper-jobs/{ident}/vision/micrograph/{panel_id}/select-regions')
def papers_select_regions(ident: str,panel_id: str,request: RegionSelection):
    with paper_jobs.LOCK:
        try:
            folder=paper_jobs.job_path(PAPER_JOBS,ident);state=paper_jobs.snapshot(PAPER_JOBS,ident)
            if state['revision']!=request.expected_revision or paper_jobs.busy(state):raise ValueError('Job changed or busy. Refresh first.')
            run=state.get('vision_runs',{}).get('micrograph')
            if not run:raise ValueError('No micrograph run.')
            item=next((p for p in run['panels'] if p['id']==panel_id),None)
            if not item:raise ValueError('Panel not found.')
            path=folder/'vision'/run['id']/item['id']
            raw=(path/'crop.png').read_bytes();transform=paper_jobs.read_json(path/'transform.json')
            if paper_jobs.digest(raw)!=transform['crop_sha256']:raise ValueError('Crop changed.')
            candidate_raw=(path/'region-candidates.json').read_bytes();report=json.loads(candidate_raw)
            selected=sorted(request.candidate_ids)
            # Validate before storing anything; selection carries no model scale.
            doc=selection_document(report,selected,'0'*64,state['paper']+' selected regions',
                f'Paper job {ident}; panel {panel_id}. Local region candidates; no phase or scale adopted.')
            identity=paper_jobs.digest(candidate_raw+json.dumps(selected).encode())
            project_id=str(uuid5(NAMESPACE_URL,f'tifigure-regions:{ident}:{run["id"]}:{panel_id}:{identity}'))
            with connect() as c:exists=c.execute('SELECT id FROM projects WHERE id=?',(project_id,)).fetchone() is not None
            if not exists:
                meta=store_image(raw,f'{ident}-{panel_id}-regions.png');doc.image_id=meta['id']
                save_project(project_id,SaveRequest(expected_revision=0,document=doc))
            item.setdefault('region_project_ids',[])
            if project_id not in item['region_project_ids']:item['region_project_ids'].append(project_id)
            paper_jobs.write_json(path/('selection-'+identity+'.json'),dict(candidate_ids=selected,candidates_sha256=paper_jobs.digest(candidate_raw),project_id=project_id,scale_adopted=False))
            paper_vision.update(folder,run)
            return dict(project_id=project_id,reused=exists,job=paper_jobs.snapshot(PAPER_JOBS,ident))
        except (ValueError,FileNotFoundError) as err:raise HTTPException(409,str(err)) from None


@app.post('/api/paper-jobs/{ident}/vision/{kind}/{panel_id}/remeasure')
async def papers_vision_remeasure(ident: str,kind: str,panel_id: str,request: paper_vision.Retry,tasks: BackgroundTasks):
    if kind not in ('micrograph','xrd'):raise HTTPException(422,'Unknown visual branch.')
    if not deepseek.status()['configured']:raise HTTPException(422,'Configure an API key first. No call was attempted.')
    try:paper_vision.begin_retry(PAPER_JOBS,ident,kind,panel_id,request)
    except ValueError as err:raise HTTPException(409,str(err)) from None
    async def send(body):
        async with deepseek.lock:return await deepseek.send(body)
    tasks.add_task(paper_vision.remeasure,PAPER_JOBS,ident,kind,panel_id,send,(ROOT/'configs/visual-extraction.md').read_text('utf-8'))
    return paper_jobs.snapshot(PAPER_JOBS,ident)


@app.post('/api/paper-jobs/{ident}/vision/micrograph/{panel_id}/recompute')
def papers_micro_recompute(ident: str,panel_id: str,request: paper_vision.Retry):
    try:return paper_vision.recompute_xrd(PAPER_JOBS,ident,panel_id,request,'micrograph')
    except (ValueError,FileNotFoundError) as err:raise HTTPException(409,str(err)) from None


@app.post('/api/paper-jobs/{ident}/vision/xrd/{panel_id}/recompute')
def papers_xrd_recompute(ident: str,panel_id: str,request: paper_vision.Retry):
    try:return paper_vision.recompute_xrd(PAPER_JOBS,ident,panel_id,request)
    except ValueError as err:raise HTTPException(409,str(err)) from None


@app.post('/api/paper-jobs/{ident}/graphs/run')
async def papers_graph_run(ident: str,request: paper_graphs.Run,tasks: BackgroundTasks):
    if not deepseek.status()['configured']:raise HTTPException(422,'Configure an API key first. No call was attempted.')
    rules=(ROOT/'configs/graph-extraction.md').read_text('utf-8')
    try:paper_graphs.begin(PAPER_JOBS,ident,request,rules)
    except ValueError as err:raise HTTPException(409,str(err)) from None
    async def send(body):
        async with deepseek.lock:return await deepseek.send(body)
    tasks.add_task(paper_graphs.execute,PAPER_JOBS,ident,send,rules)
    return paper_jobs.snapshot(PAPER_JOBS,ident)


@app.post('/api/paper-jobs/{ident}/graphs/{panel_id}/open')
def papers_graph_open(ident: str,panel_id: str):
    with paper_jobs.LOCK:
        try:folder,graph,item,result,path=paper_graphs.panel(PAPER_JOBS,ident,panel_id)
        except ValueError as err:raise HTTPException(422,str(err)) from None
        suffix=item.get('refinement',{}).get('id','') if result.get('pixel_refinement') else ''
        project_id=str(uuid5(NAMESPACE_URL,f'tifigure-paper-graph:{ident}:{graph["id"]}:{panel_id}'+(':'+suffix if suffix else '')))
        with connect() as c:exists=c.execute('SELECT id FROM projects WHERE id=?',(project_id,)).fetchone() is not None
        if not exists:
            meta=store_image((path/'crop.png').read_bytes(),f'{ident}-{panel_id}.png')
            state=paper_jobs.snapshot(PAPER_JOBS,ident)
            doc=paper_graphs.workspace(result,meta['id'],state['paper'],
                f'Paper job {ident}; graph run {graph["id"]}; {result["selection"]["locator"]}; page {item["page"]}; original transform and model response in paper archive. Unreviewed automatic geometry.')
            try:save_project(project_id,SaveRequest(expected_revision=0,document=doc))
            except HTTPException as err:
                if err.status_code!=409:raise
                # A concurrent open may already have created the project; never overwrite it.
                get_project(project_id)
        item['project_id']=project_id;paper_graphs.update(folder,graph)
        return dict(project_id=project_id,reused=exists,job=paper_jobs.snapshot(PAPER_JOBS,ident))


@app.post('/api/paper-jobs/{ident}/graphs/{panel_id}/mapping')
def papers_graph_mapping(ident: str,panel_id: str,request: graph_mapping.MappingRequest):
    with paper_jobs.LOCK:
        try:
            folder,graph,item,result,path=paper_graphs.panel(PAPER_JOBS,ident,panel_id)
            if not item.get('project_id'):raise ValueError('Open and save a review workspace first.')
            data=get_project(item['project_id'])
            if data['revision']!=request.expected_revision:raise ValueError('Workspace changed; reopen it before mapping.')
            reference=read_reference(request.reference_id)
            raw=(REFERENCES/(request.reference_id+'.json')).read_bytes()
            findings,hashes=master_export.source_findings(ROOT,TABLE_SOURCE_ROOT,raw)
            state=paper_jobs.snapshot(PAPER_JOBS,ident)
            if any(f['paper']!=request.paper_short_id for f in findings):raise ValueError('Cross-paper finding.')
            # Registered findings apply only to their original PDF, not a similarly named upload.
            for review_path in hashes:
                review=json.loads((ROOT/review_path).read_text('utf-8'))
                if review['pdf_sha256']!=state['pdf_sha256']:raise ValueError('Registered findings belong to a different PDF.')
            doc=Document.model_validate(data['document'])
            proposal,fields=graph_mapping.propose(doc,data['results'],result['plot'],reference,request,findings)
            if not fields:return dict(job=state,proposal=proposal,project_id=item['project_id'],saved=False)
            doc.reference_id=request.reference_id;doc.candidates=fields
            saved=save_project(item['project_id'],SaveRequest(expected_revision=request.expected_revision,document=doc))
            proposal.update(project_id=item['project_id'],source_revision=request.expected_revision,saved_revision=saved['revision'],source_finding_hashes=hashes)
            paper_jobs.write_json(path/'mapping.json',proposal)
            item['mapping']=proposal;paper_graphs.update(folder,graph)
            return dict(job=paper_jobs.snapshot(PAPER_JOBS,ident),proposal=proposal,project_id=item['project_id'],saved=True)
        except ValueError as e:raise HTTPException(422,str(e)) from None


@app.post('/api/paper-jobs/{ident}/graphs/{panel_id}/refine')
async def papers_refine(ident:str,panel_id:str):
    if not deepseek.status()['configured']:raise HTTPException(422,'Configure an API key first.')
    async def send(body):
        async with deepseek.lock:return await deepseek.send(body)
    try:return await graph_refine.execute(PAPER_JOBS,ident,panel_id,send)
    except (ValueError,FileNotFoundError) as e:raise HTTPException(422,str(e)) from None


DIST=ROOT/'frontend/dist'
if DIST.exists(): app.mount('/',StaticFiles(directory=DIST,html=True),name='ui')
