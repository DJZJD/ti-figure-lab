"""PDF -> native text -> bounded model extraction -> evidence crops -> review archive.

This pre-Round-1 pipeline deliberately does not produce a final TiFM master.
"""
import csv
import hashlib
import io
import json
import math
import re
import threading
import zipfile
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_PAGES = 60
MAX_TEXT = 140_000
MAX_PDF = 40 * 1024 * 1024
LOCK = threading.RLock()
PROPERTIES = {'yield_strength','tensile_strength','elongation','impact_toughness','fracture_toughness'}
MICROSTRUCTURE = {'primary_alpha_fraction','second_alpha_fraction','beta_fraction','primary_alpha_size','transformed_beta_fraction'}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, data):
    raw = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(raw, 'utf-8')
    temp.replace(path)


def read_json(path):
    return json.loads(path.read_text('utf-8'))


def now():
    return datetime.now(timezone.utc).isoformat()


def job_path(root, ident):
    if not re.fullmatch(r'[0-9a-f]{32}', ident):
        raise ValueError('Invalid paper job ID.')
    path = Path(root) / ident
    if not (path / 'job.json').is_file():
        raise ValueError('Paper job not found.')
    return path


def busy(state):
    return (state['status']=='running' or state.get('graph_run',{}).get('status')=='running'
        or any(p.get('refinement',{}).get('status')=='running' for p in state.get('graph_run',{}).get('panels',[]))
        or any(r.get('status')=='running' for r in state.get('vision_runs',{}).values()))


def asset(root, ident, name):
    folder = job_path(root, ident)
    if not re.fullmatch(r'(?:source\.pdf|pages/page-\d{3}\.png|evidence/record-\d{4}\.png|table-evidence/record-\d{4}-(?:source|overlay)\.png|(?:graphs|vision)/[0-9a-f]{32}/panel-\d{3}/(?:[0-9a-f]{32}/)?(?:crop|overlay|candidates)\.png)', name):
        raise ValueError('Unknown paper artifact.')
    target = folder / name
    if not target.is_file():
        raise ValueError('Paper artifact not found.')
    return target


def normalize(text):
    return ' '.join(text.split())


def streams(page):
    return [dict(name='spatial',text=page['text'],words=page.get('words',[])),*page.get('alternate_streams',[])]


def text_context(page):
    return dict(page=page['page'],text=page['text'],alternate_texts=[dict(name=s['name'],text=s['text']) for s in page.get('alternate_streams',[])],quality_warnings=page.get('quality_warnings',[]))


def matching_stream(page,quote):
    q=normalize(quote)
    return next((s for s in streams(page) if q and q in normalize(s['text'])),None)


def parse_pdf(raw, folder):
    try:
        import pdfplumber
        import pypdfium2 as pdfium
    except ImportError:
        raise ValueError('Install requirements-paper.txt in this server runtime before importing PDFs.') from None
    pages = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf, pdfium.PdfDocument(raw) as rendered:
        if not 1 <= len(pdf.pages) <= MAX_PAGES:
            raise ValueError(f'Paper intake supports 1–{MAX_PAGES} pages per job.')
        for index, page in enumerate(pdf.pages):
            if not (0 < page.width <= 2400 and 0 < page.height <= 2400):
                raise ValueError('Unsupported PDF page dimensions.')
            # The supplied text and quote localization share exactly the same word stream.
            words = page.extract_words(x_tolerance=2, y_tolerance=3)
            text = ' '.join(w['text'] for w in words)
            flow=page.extract_words(x_tolerance=2,y_tolerance=3,use_text_flow=True)
            flow_text=' '.join(w['text'] for w in flow)
            alternates=[] if flow_text==text else [dict(name='pdf_text_flow',text=flow_text,words=[{k:w[k] for k in ('text','x0','x1','top','bottom')} for w in flow])]
            if sum(len(s['text']) for p in pages for s in streams(p)) + len(text) + sum(len(s['text']) for s in alternates) > MAX_TEXT:
                raise ValueError('Native text exceeds the single-request limit. No truncated request was sent.')
            scale = min(2.0, 1600 / max(page.width, page.height))
            rp = rendered[index]
            bitmap = rp.render(scale=scale)
            try:
                image = bitmap.to_pil().convert('RGB')
                image.save(folder / 'pages' / f'page-{index+1:03}.png')
                size = image.size
            finally:
                bitmap.close()
                rp.close()
            pages.append(dict(page=index+1, text=text,alternate_streams=alternates, words=[{k:w[k] for k in ('text','x0','x1','top','bottom')} for w in words],
                width=page.width, height=page.height, image_width=size[0], image_height=size[1],
                image=f'pages/page-{index+1:03}.png', native_text_usable=len(text.strip()) >= 60,
                quality_warnings=['Unresolved PDF font symbols (cid); verify numbers, units and conditions on the page image.'] if '(cid:' in text else []))
    return pages


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Candidate(Strict):
    kind: Literal['property', 'microstructure']
    quantity: str = Field(min_length=1, max_length=80)
    definition: str = Field(min_length=1, max_length=800)
    sample_label: str = Field(max_length=300)
    conditions: str = Field(max_length=1500)
    condition_page: int | None = Field(ge=1, le=MAX_PAGES)
    condition_quote: str = Field(max_length=3000)
    value: float | None
    raw_value: str = Field(min_length=1, max_length=100)
    unit: str = Field(max_length=100)
    page: int = Field(ge=1, le=MAX_PAGES)
    locator: str = Field(min_length=1, max_length=200)
    evidence_quote: str = Field(min_length=3, max_length=4000)
    notes: str = Field(max_length=2000)

    @model_validator(mode='after')
    def check_quantity(self):
        allowed = PROPERTIES if self.kind == 'property' else MICROSTRUCTURE
        if self.quantity not in allowed:
            raise ValueError('Quantity outside the requested intake scope.')
        return self


class Deferred(Strict):
    page: int = Field(ge=1, le=MAX_PAGES)
    locator: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=1500)


class Extraction(Strict):
    summary: str = Field(max_length=3000)
    records: list[Candidate] = Field(max_length=160)
    deferred_items: list[Deferred] = Field(max_length=100)
    limitations: list[str] = Field(max_length=30)


class MechanicalCandidate(Candidate):
    kind: Literal['property']
    quantity: Literal['yield_strength','tensile_strength','elongation','impact_toughness']
    definition: str = Field(min_length=1,max_length=180)
    conditions: str = Field(max_length=220)
    condition_quote: str = Field(max_length=500)
    evidence_quote: str = Field(min_length=3,max_length=500)
    notes: str = Field(max_length=200)


class MechanicalExtraction(Extraction):
    summary: str = Field(max_length=800)
    records: list[MechanicalCandidate] = Field(max_length=16)
    deferred_items: list[Deferred] = Field(max_length=10)
    limitations: list[str] = Field(max_length=8)


class RunRequest(Strict):
    expected_revision: int = Field(ge=1)
    request_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


class ReviewRequest(Strict):
    expected_revision: int = Field(ge=1)
    decision: Literal['accepted','rejected','needs_review']
    reviewer: str = Field(min_length=1, max_length=100)
    note: str = Field(min_length=1, max_length=2000)
    # Explicit corrections stay separate from immutable model output.
    corrected_value: float | None
    corrected_unit: str = Field(max_length=100)
    corrected_sample_label: str = Field(max_length=300)


def create(root, raw, filename, paper, instructions, scope='all'):
    if scope not in ('all','mechanical_properties'):raise ValueError('Unsupported extraction scope.')
    if scope=='mechanical_properties':
        instructions+='\n\n## Focused batch (overrides broader scope above)\nExtract ONLY yield_strength, tensile_strength, elongation and impact_toughness reported by this work. At most 16 records. Prefer one complete primary results table; do not repeat the same measurement from body, abstract or another reading stream. If additional samples/values exceed this batch, list their source in deferred_items; do not imply full coverage. All microstructure and fracture-toughness tasks are outside this batch. Use concise definitions, conditions and notes. Quotes must be exact contiguous text in ONE supplied stream; never use ellipses or reassemble split uncertainty cells. Preserve the full raw scalar-with-uncertainty expression when available in that stream. Aim for a complete response under 6500 tokens.\n'
    if len(raw) > MAX_PDF or not raw.startswith(b'%PDF-'):
        raise ValueError('Upload an actual PDF of at most 40 MiB.')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,39}', paper):
        raise ValueError('Use a paper label with letters, numbers, underscores or hyphens (1–40 characters).')
    ident = uuid4().hex
    folder = Path(root) / ident
    folder.mkdir(parents=True)
    for name in ('source', 'pages', 'rounds', 'evidence', 'outputs'):
        (folder / name).mkdir()
    (folder / 'source.pdf').write_bytes(raw)
    (folder / 'source' / 'requirements.md').write_bytes(instructions.encode('utf-8'))
    state = dict(id=ident, paper=paper, filename=Path(filename).name, created_at=now(), revision=1,
        status='preparing', pdf_sha256=digest(raw), source_unchanged=True,
        pipeline='paper-intake-reported-values/0.1', extraction_scope=scope, network_calls_attempted=0,
        records=[], deferred_items=[], limitations=[], error=None, final_master_generated=False)
    write_json(folder/'job.json', state)
    try:
        pages = parse_pdf(raw, folder)
        write_json(folder/'source/pages.json', pages)
        (folder/'source/full.md').write_text('\n\n'.join(f'## Page {p["page"]} / {s["name"]}\n\n{s["text"]}' for p in pages for s in streams(p)), 'utf-8')
        state['page_count'] = len(pages)
        state['textless_pages'] = [p['page'] for p in pages if not p['native_text_usable']]
        state['encoding_warning_pages'] = [p['page'] for p in pages if p.get('quality_warnings')]
        state['status'] = 'ready' if any(p['native_text_usable'] for p in pages) else 'needs_ocr'
        state['limitations'] = ['Native text only; page previews are not sent to the model.',
            'Evidence crops are text regions, not semantic figure/panel crops.',
            'Reported-value extraction does not measure figures. The separate graph branch supports limited linear marker plots; no automatic phase recognition, full TiFM master or learning from feedback.']
        if state['textless_pages']:
            state['limitations'].append('Some pages need visual/OCR review; extraction cannot establish full-paper coverage.')
        if state['encoding_warning_pages']:
            state['limitations'].append('Unresolved font encoding on pages: '+', '.join(map(str,state['encoding_warning_pages']))+'. Verify symbols and units visually.')
        state['limitations'].append('Native reading order may interleave columns and table cells; quote matching does not validate row/column meaning.')
        context = dict(paper_short_id=paper,extraction_scope=scope,pages=[text_context(p) for p in pages])
        schema=MechanicalExtraction if scope=='mechanical_properties' else Extraction
        system = instructions + '\n\nResponse JSON schema:\n' + json.dumps(schema.model_json_schema(), ensure_ascii=False)
        body = dict(model='deepseek-flash', messages=[dict(role='system',content=system),
            dict(role='user',content=json.dumps(context, ensure_ascii=False))],
            response_format={'type':'json_object'}, max_tokens=8192, thinking={'type':'disabled'}, stream=False)
        packet = dict(pipeline=state['pipeline'], pdf_sha256=state['pdf_sha256'],
            requirements_sha256=digest(instructions.encode()), body=body, includes_images=False,
            reference_answers_supplied=False, max_calls=1)
        state['request_hash'] = digest(json.dumps(packet, sort_keys=True, ensure_ascii=False).encode())
        write_json(folder/'source/request.json', packet)
    except Exception as err:
        state.update(status='prepare_failed', error=f'{type(err).__name__}: {str(err)[:500]}')
    write_json(folder/'job.json', state)
    return state


def snapshot(root, ident):
    return read_json(job_path(root, ident)/'job.json')


def list_jobs(root):
    return sorted([read_json(p) for p in Path(root).glob('*/job.json')], key=lambda s:s['created_at'], reverse=True)


def claim(root, ident, request):
    with LOCK:
        folder = job_path(root, ident)
        state = read_json(folder/'job.json')
        if state['revision'] != request.expected_revision or state['request_hash'] != request.request_hash:
            raise ValueError('Job changed. Reload before running.')
        if state['status'] != 'ready' or busy(state):
            raise ValueError('Job is not ready, or a call has already been attempted. Import again for an explicit retry.')
        packet = read_json(folder/'source/request.json')
        if digest(json.dumps(packet,sort_keys=True,ensure_ascii=False).encode()) != state['request_hash'] or digest((folder/'source.pdf').read_bytes()) != state['pdf_sha256'] or digest((folder/'source/requirements.md').read_bytes()) != packet['requirements_sha256']:
            raise ValueError('Archived source/request changed. Reimport instead of sending stale evidence.')
        round_dir = folder/'rounds/01-extract'
        round_dir.mkdir(exist_ok=False)
        write_json(round_dir/'input.json', packet)
        (round_dir/'requirements.md').write_bytes((folder/'source/requirements.md').read_bytes())
        state.update(status='running', revision=state['revision']+1, network_calls_attempted=1, started_at=now())
        write_json(folder/'job.json',state)
        return packet['body']


def quote_bounds(page, quote):
    quote = normalize(quote)
    stream=matching_stream(page,quote)
    if stream is None:return None
    text = normalize(stream['text'])
    start = text.find(quote)
    if not quote or start < 0:
        return None
    # Multiple appearances are ambiguous, so preserve the full page as evidence.
    if text.find(quote, start+1) >= 0:
        return (0, 0, page['width'], page['height'])
    offset = 0
    selected = []
    for word in stream['words']:
        end = offset + len(normalize(word['text']))
        if end > start and offset < start + len(quote):
            selected.append(word)
        offset = end + 1
    if not selected:
        return None
    return (max(0,min(w['x0'] for w in selected)-8), max(0,min(w['top'] for w in selected)-8),
            min(page['width'],max(w['x1'] for w in selected)+8), min(page['height'],max(w['bottom'] for w in selected)+8))


def center_matches(value, raw):
    if value is None:
        return True
    # Conservative: simple scalar, optionally ± uncertainty. Ranges/approximation defer.
    match = re.fullmatch(r'\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(?:±\s*\d+(?:\.\d+)?)?\s*',raw)
    if not match:
        return False
    try:
        return Decimal(match[1]) == Decimal(str(value))
    except InvalidOperation:
        return False


def parse_output(content, schema=Extraction):
    def unique(pairs):
        result={}
        for k,v in pairs:
            if k in result:raise ValueError('Duplicate response JSON key.')
            result[k]=v
        return result
    if len(content)>50_000:raise ValueError('Oversized extraction response.')
    return schema.model_validate(json.loads(content,object_pairs_hook=unique,
        parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Nonfinite JSON.'))))


def finish(root, ident, envelope, content, usage, error=None):
    from PIL import Image
    with LOCK:
        folder=job_path(root,ident)
        state=read_json(folder/'job.json')
        if state['status']!='running':raise ValueError('No running extraction for this response.')
        round_dir=folder/'rounds/01-extract'
        write_json(round_dir/'response.json',envelope)
        if content is not None:(round_dir/'output.txt').write_text(content,'utf-8')
        state.update(usage=usage, finished_at=now(), revision=state['revision']+1)
        try:
            if error:raise ValueError(error)
            parsed=parse_output(content,MechanicalExtraction if state.get('extraction_scope')=='mechanical_properties' else Extraction)
            pages=read_json(folder/'source/pages.json')
            records=[]
            for i,candidate in enumerate(parsed.records,1):
                record=candidate.model_dump()
                record.update(id=f'record-{i:04}',review_status='needs_review',review=None,checks=[],evidence_image=None)
                page=pages[candidate.page-1] if candidate.page<=len(pages) else None
                if page:record['checks'].extend(page.get('quality_warnings',[]))
                bbox=quote_bounds(page,candidate.evidence_quote) if page else None
                matched=matching_stream(page,candidate.evidence_quote) if page else None
                record['evidence_text_stream']=matched['name'] if matched else None
                if bbox is None:record['checks'].append('Evidence quote not found on the stated page.')
                if normalize(candidate.raw_value) not in normalize(candidate.evidence_quote):record['checks'].append('Raw value is absent from the evidence quote.')
                if not center_matches(candidate.value,candidate.raw_value):record['checks'].append('Numeric center does not match a supported scalar expression.')
                if candidate.value is None:record['checks'].append('Non-scalar or unresolved expression requires manual extraction.')
                cp=pages[candidate.condition_page-1] if candidate.condition_page and candidate.condition_page<=len(pages) else None
                if not candidate.sample_label or not candidate.conditions or not cp or not quote_bounds(cp,candidate.condition_quote):
                    record['checks'].append('Sample/condition evidence is incomplete or unlocated.')
                if not candidate.unit:record['checks'].append('Unit not provided.')
                if bbox:
                    with Image.open(folder/page['image']) as image:
                        sx,sy=image.width/page['width'],image.height/page['height']
                        pixels=(max(0,math.floor(bbox[0]*sx)),max(0,math.floor(bbox[1]*sy)),min(image.width,math.ceil(bbox[2]*sx)),min(image.height,math.ceil(bbox[3]*sy)))
                        image.crop(pixels).save(folder/'evidence'/f'record-{i:04}.png')
                    record.update(evidence_image=f'evidence/record-{i:04}.png',evidence_bbox_pdf=list(bbox),
                        evidence_page_size=[page['width'],page['height']])
                records.append(record)
            # Conflicts are flags, not automatic selection of either source.
            for record in records:
                peers=[r for r in records if (r['kind'],r['quantity'],r['sample_label'],r['conditions']) == (record['kind'],record['quantity'],record['sample_label'],record['conditions'])]
                if len({(r['value'],r['unit'],r['raw_value']) for r in peers})>1:
                    record['checks'].append('Different values for the same candidate sample/quantity/conditions; inspect all sources.')
            deferred=[d.model_dump() for d in parsed.deferred_items]
            if any(d['page']>len(pages) for d in deferred):raise ValueError('Deferred task cites a page outside this paper.')
            state.update(status='awaiting_review',summary=parsed.summary,records=records,deferred_items=deferred,
                model_limitations=parsed.limitations,error=None)
            write_json(round_dir/'validated-candidates.json',dict(records=records,deferred_items=deferred,scientifically_approved=False))
        except (ValueError,TypeError,IndexError) as err:
            state.update(status='response_failed',error=str(err)[:1500])
        if state['status']=='awaiting_review' and state.get('extraction_scope')=='mechanical_properties':
            # Local evidence enrichment cannot turn a valid response into a failed extraction.
            from . import table_evidence
            try:table_evidence.build(folder,state)
            except Exception as err:
                state['table_evidence_run']=dict(status='failed',error=f'{type(err).__name__}: local table evidence unavailable; original candidates retained.',model_calls=0)
        write_json(folder/'job.json',state)
        write_outputs(folder,state)
        return state


def review(root,ident,record_id,request):
    with LOCK:
        folder=job_path(root,ident);state=read_json(folder/'job.json')
        if state['status']!='awaiting_review' or state['revision']!=request.expected_revision:
            raise ValueError('Job changed or is not ready for review. Reload first.')
        row=next((r for r in state['records'] if r['id']==record_id),None)
        if row is None:raise ValueError('Candidate not found.')
        if not request.reviewer.strip() or not request.note.strip():raise ValueError('Reviewer and review note are required.')
        if request.decision=='accepted' and (request.corrected_value is None or not request.corrected_unit.strip() or not request.corrected_sample_label.strip()):
            raise ValueError('Accepting requires a finite scalar, unit and supported sample label. Defer ranges.')
        before=dict(status=row['review_status'],review=row['review'])
        row.update(review_status=request.decision,review=dict(request.model_dump(exclude={'expected_revision','decision'}),at=now()))
        state['revision']+=1
        event=dict(record_id=record_id,revision=state['revision'],before=before,after=dict(status=row['review_status'],review=row['review']))
        # Immutable per-revision review events; original model rows are never overwritten.
        write_json(folder/'rounds'/f'review-{state["revision"]:06}.json',event)
        write_json(folder/'job.json',state);write_outputs(folder,state)
        return state


PROPERTY_COLUMNS='paper_short_id property_record_id source_original_microstructure specimen_group_raw sample_id_if_known property_name property_category value unit value_status data_origin source_figure_or_table source_file extraction_method replicate_or_average confidence notes'.split()
MICRO_COLUMNS='paper_short_id quantification_record_id sample_id image_id canonical_image_file parent_figure_id panel_id source_original_microstructure specimen_group_raw quantification_target value unit value_status method software roi_description replicate_id source_figure_or_table confidence notes'.split()


def write_outputs(folder,state):
    write_json(folder/'outputs/candidates.json',dict(artifact_type='pre_round1_candidates_not_TiFM_master',**state))
    for kind,columns,filename in [('property',PROPERTY_COLUMNS,'property_manual_extraction'),('microstructure',MICRO_COLUMNS,'microstructure_quantification')]:
        for accepted_only in (False,True):
            path=folder/'outputs'/f'{filename}.{"reviewed" if accepted_only else "candidates"}.csv'
            stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader()
            for r in state['records']:
                if r['kind']!=kind or (accepted_only and r['review_status']!='accepted'):continue
                rev=r['review'] if accepted_only else None
                record={c:'' for c in columns}
                record.update(paper_short_id=state['paper'],specimen_group_raw=rev['corrected_sample_label'] if rev else r['sample_label'],
                    value=rev['corrected_value'] if rev else (r['value'] if r['value'] is not None else r['raw_value']),
                    unit=rev['corrected_unit'] if rev else r['unit'],value_status='reported_numeric' if accepted_only else 'deferred',
                    source_figure_or_table=f'p.{r["page"]} {r["locator"]}',confidence='human_reviewed' if accepted_only else 'unreviewed',
                    notes=json.dumps(dict(candidate_id=r['id'],status=r['review_status'],raw_value=r['raw_value'],definition=r['definition'],
                        conditions=r['conditions'],evidence_quote=r['evidence_quote'],checks=r['checks'],notes=r['notes'],review=r['review']),ensure_ascii=False))
                if kind=='property':record.update(property_record_id='',property_name=r['quantity'],property_category='mechanical',
                    data_origin='author_reported',source_file='source.pdf',extraction_method='LLM native-text extraction with source quote')
                else:record.update(quantification_record_id='',quantification_target=r['quantity'],method='author_reported',software='TiFigure Lab paper intake')
                # CSV may be opened in Excel; source text must never become a formula.
                writer.writerow({k:("'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v) for k,v in record.items()})
            path.write_bytes(stream.getvalue().encode('utf-8-sig'))
    lines=['# Paper intake summary', '', f'Paper: {state["paper"]}',f'Status: {state["status"]}',
        f'Candidates: {len(state["records"])}',f'Accepted by human: {sum(r["review_status"]=="accepted" for r in state["records"])}',
        '',state.get('summary',''),'', '## Deferred tasks']
    lines.extend(f'- p.{d["page"]} {d["locator"]}: {d["reason"]}' for d in state['deferred_items'])
    lines+=['','## Limits','These are pre-Round-1 candidates, not a final master. Blank IDs require TiFM assembly; no sample IDs are fabricated.',
        'Candidate CSV values are unreviewed. Use reviewed CSVs only after checking each source, sample, unit and definition.',
        'Human review feedback is archived; no model training takes place.']+state['limitations']
    (folder/'outputs/summary.md').write_text('\n\n'.join(lines),'utf-8')


def bundle(root,ident,extras=None):
    with LOCK:
        folder=job_path(root,ident);state=read_json(folder/'job.json')
        if busy(state):raise ValueError('Wait for extraction to finish before exporting its archive.')
        output=io.BytesIO();manifest=[]
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
            for path in sorted(folder.rglob('*')):
                if path.is_file() and path.suffix!='.tmp':
                    raw=path.read_bytes();name=path.relative_to(folder).as_posix()
                    z.writestr(name,raw);manifest.append(dict(path=name,sha256=digest(raw),bytes=len(raw)))
            for name,raw in (extras or {}).items():
                if not re.fullmatch(r'(?:workspaces/[0-9a-f-]+|references/[0-9a-f]{64})\.json',name):raise ValueError('Invalid workspace or reference attachment name.')
                z.writestr(name,raw);manifest.append(dict(path=name,sha256=digest(raw),bytes=len(raw)))
            z.writestr('archive-manifest.json',json.dumps(dict(files=manifest,final_master=False),indent=2))
        return output.getvalue()
