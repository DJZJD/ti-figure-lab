"""Source-only geometric evidence for a limited mechanical-results table grammar.

Row/column selection never uses a model value or reference master. Original
model quotes, flags and review decisions are retained separately.
"""
import io
import re
from decimal import Decimal
from PIL import ImageDraw
from pydantic import Field
from . import paper_jobs as pj
from .candidates import unit_spec

HEADERS={'UTS/MPa':('tensile_strength','MPa'),'YS/MPa':('yield_strength','MPa'),
         'Elongation/%':('elongation','%'),'Impactproperty/J':('impact_toughness','J')}
SCALAR=re.compile(r'\s*(\d+(?:\.\d+)?)\s*(?:±\s*(\d+(?:\.\d+)?))?\s*')


class Request(pj.Strict):
    expected_revision:int=Field(ge=1)


def expression(text):
    m=SCALAR.fullmatch(text)
    return (Decimal(m[1]),Decimal(m[2]) if m[2] else None) if m else None


def inside(w,box):
    x=(w['x0']+w['x1'])/2;y=(w['top']+w['bottom'])/2
    return box[0]<=x<box[2] and box[1]<=y<box[3]


def ordered(words):
    lines=[]
    for w in sorted(words,key=lambda w:((w['top']+w['bottom'])/2,w['x0'])):
        y=(w['top']+w['bottom'])/2
        if not lines or y-lines[-1][0]>2.5:lines.append((y,[w]))
        else:lines[-1][1].append(w)
    return [w for _,line in lines for w in sorted(line,key=lambda w:w['x0'])]


def parse_table(words,headers,boxes,bounds,table_number):
    """Inputs are PDF-detected header cells and words, not an answer or ROI guess."""
    names=[re.sub(r'\s+','',h or '') for h in headers]
    if names.count('Coolingmedium')!=1 or any(names.count(h)!=1 for h in HEADERS):raise ValueError('Unsupported or ambiguous table headings.')
    labelbox=boxes[names.index('Coolingmedium')]
    columns={key:(boxes[names.index(key)],quantity,unit) for key,(quantity,unit) in HEADERS.items()}
    if not labelbox or any(not b for b,_,_ in columns.values()):raise ValueError('Missing header geometry.')
    y0=max(b[3] for b in boxes if b)
    labels=sorted([w for w in words if inside(w,(labelbox[0],y0,labelbox[2],bounds[3]))],key=lambda w:w['top'])
    if not 2<=len(labels)<=100 or any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,20}',w['text']) for w in labels):raise ValueError('Unsupported sample-label column.')
    if len({w['text'] for w in labels})!=len(labels):raise ValueError('Repeated sample labels within table.')
    # Empty header columns must remain empty; otherwise inferred column partitions lost data.
    for name,box in zip(names,boxes):
        if not name and box and any(inside(w,(box[0],y0,box[2],bounds[3])) for w in words):raise ValueError('Unlabelled column contains data.')
    rows=[]
    for i,label in enumerate(labels):
        height=label['bottom']-label['top']
        top=max(y0,label['top']-height/4)
        bottom=labels[i+1]['top']-(labels[i+1]['bottom']-labels[i+1]['top'])/4 if i+1<len(labels) else bounds[3]
        if bottom<=top:raise ValueError('Overlapping sample rows.')
        for key,(hb,quantity,unit) in columns.items():
            cell=(hb[0],top,hb[2],bottom)
            selected=ordered([w for w in words if inside(w,cell)])
            if any(w['x0']<cell[0]-0.5 or w['x1']>cell[2]+0.5 or w['top']<cell[1]-0.5 or w['bottom']>cell[3]+0.5 for w in selected):raise ValueError('Word crosses a cell boundary.')
            text=' '.join(w['text'] for w in selected);parts=expression(text)
            if not parts:raise ValueError('Unsupported cell expression; no nearest-value search.')
            if parts[1] is not None and len(selected)<2:raise ValueError('Insufficient word evidence.')
            rows.append(dict(table_number=table_number,sample_label=label['text'],quantity=quantity,unit=unit,
                value=float(parts[0]),uncertainty=float(parts[1]) if parts[1] is not None else None,
                uncertainty_kind='unspecified',expression=text,cell_bbox=list(cell),header_bbox=list(hb),
                label_bbox=[label['x0'],label['top'],label['x1'],label['bottom']],word_boxes=selected,table_bbox=list(bounds)))
    return rows


def extract(pdf_page):
    words=[{k:w[k] for k in ('text','x0','x1','top','bottom')} for w in pdf_page.extract_words(x_tolerance=2,y_tolerance=3)]
    tables=pdf_page.find_tables({'vertical_strategy':'text','horizontal_strategy':'lines','min_words_vertical':3})
    cells=[];deferred=[]
    for table in tables:
        grid=table.extract();headers=grid[0] if grid else []
        if not any(re.sub(r'\s+','',h or '') in HEADERS for h in headers):continue
        # A local Table N caption must precede this table in the same horizontal region.
        captions=[]
        for w in words:
            if w['text']=='Table' and table.bbox[0]-3<=w['x0']<table.bbox[2] and 0<table.bbox[1]-w['bottom']<80:
                ns=[n for n in words if n['text'].isdigit() and 0<=n['x0']-w['x1']<12 and abs(n['top']-w['top'])<2]
                if len(ns)==1:captions.append((w['top'],int(ns[0]['text'])))
        try:
            if not captions:raise ValueError('No local Table N caption.')
            number=max(captions)[1]
            cells.extend(parse_table(words,headers,table.rows[0].cells,table.bbox,number))
        except ValueError as e:deferred.append(dict(bbox=list(table.bbox),reason=str(e)))
    return cells,deferred


def match(record,cells):
    locator=re.match(r'^Table\s+(\d+)\b',record['locator'],re.I)
    if not locator:return dict(status='unresolved',reason='No explicit Table N locator.')
    candidates=[c for c in cells if c['table_number']==int(locator[1]) and c['sample_label']==record['sample_label'] and c['quantity']==record['quantity']]
    if len(candidates)!=1:return dict(status='ambiguous' if candidates else 'unresolved',reason='No unique source row/column for sample and quantity.')
    cell=candidates[0];a,b=unit_spec(record['unit']),unit_spec(cell['unit'])
    parsed=expression(record['raw_value'])
    same_unit=bool(a and b and a==b)
    center=record['value'] is not None and Decimal(str(record['value']))==Decimal(str(cell['value']))
    full=parsed is not None and parsed==expression(cell['expression'])
    return dict(status='supported' if same_unit and center and full else 'conflict',cell=cell,
        unit_agrees=same_unit,center_agrees=center,full_expression_agrees=full,
        notice='Independent PDF cell evidence, not a repaired verbatim quote or scientific approval. Conditions and uncertainty meaning still require review.')


def build(folder,state):
    import pdfplumber
    import pypdfium2 as pdfium
    raw=(folder/'source.pdf').read_bytes()
    if pj.digest(raw)!=state['pdf_sha256']:raise ValueError('Original PDF hash changed.')
    dest=folder/'table-evidence';dest.mkdir(exist_ok=True)
    if (dest/'audit.json').exists():raise ValueError('Cell evidence already exists; original audit retained.')
    rows=[];deferred=[];results=[]
    page_numbers=sorted({r['page'] for r in state['records']})
    with pdfplumber.open(io.BytesIO(raw)) as pdf, pdfium.PdfDocument(raw) as rendered:
        for number in page_numbers:
            if not 1<=number<=len(pdf.pages):
                results.extend(dict(record_id=r['id'],page=number,status='unresolved',reason='Page unavailable.') for r in state['records'] if r['page']==number)
                continue
            cells,skipped=extract(pdf.pages[number-1]);rows.extend(dict(page=number,**c) for c in cells)
            deferred.extend(dict(page=number,**d) for d in skipped)
            relevant=[r for r in state['records'] if r['page']==number]
            for r in relevant:results.append(dict(record_id=r['id'],page=number,**match(r,cells)))
        for number in page_numbers:
            selected=[r for r in results if r['page']==number and 'cell' in r]
            if not selected:continue
            page=rendered[number-1];bitmap=page.render(scale=3)
            try:
                image=bitmap.to_pil().convert('RGB')
                for result in selected:
                    cell=result['cell'];box=cell['table_bbox'];cropbox=(max(0,int((box[0]-5)*3)),max(0,int((box[1]-32)*3)),min(image.width,int((box[2]+5)*3)),min(image.height,int((box[3]+5)*3)))
                    crop=image.crop(cropbox);name=result['record_id'];crop.save(dest/(name+'-source.png'));draw=ImageDraw.Draw(crop)
                    for key,color in [('header_bbox','#df8a00'),('label_bbox','#1670db'),('cell_bbox','#b5263c')]:
                        b=cell[key];draw.rectangle((b[0]*3-cropbox[0],b[1]*3-cropbox[1],b[2]*3-cropbox[0],b[3]*3-cropbox[1]),outline=color,width=3)
                    crop.save(dest/(name+'-overlay.png'))
                    result.update(source_image=f'table-evidence/{name}-source.png',overlay_image=f'table-evidence/{name}-overlay.png',render_scale=3,crop_bbox_render_pixels=list(cropbox))
            finally:bitmap.close();page.close()
    audit=dict(schema_version='tifigure-table-evidence/1',source_pdf_sha256=state['pdf_sha256'],created_at=pj.now(),source_revision=state['revision'],
        method='native_pdf_table_cells/1',model_calls=0,reference_answers_used=False,cells=rows,results=results,deferred=deferred,
        summary={status:sum(r['status']==status for r in results) for status in ('supported','conflict','unresolved','ambiguous')},
        limitations=['Only native-text mechanical tables with recognized headings, horizontal rules and simple sample rows.',
                    'No OCR, merged-cell interpretation, material/condition verification, or uncertainty-type inference. Original model flags remain unchanged.'])
    pj.write_json(dest/'audit.json',audit)
    by_id={r['record_id']:r for r in results}
    for r in state['records']:r['table_evidence']=by_id.get(r['id'],dict(status='unresolved',reason='Page unavailable.'))
    state['table_evidence_run']={k:v for k,v in audit.items() if k not in ('cells','results')}
    return audit


def run(root,ident,request):
    with pj.LOCK:
        folder=pj.job_path(root,ident);state=pj.read_json(folder/'job.json')
        if state['status']!='awaiting_review' or state['revision']!=request.expected_revision:raise ValueError('Job changed or extraction incomplete; refresh first.')
        if state.get('table_evidence_run'):raise ValueError('Cell evidence already prepared.')
        if any(r['review_status']!='needs_review' for r in state['records']):raise ValueError('Prepare cell evidence before reviewing records.')
        build(folder,state);state['revision']+=1
        pj.write_json(folder/'job.json',state);pj.write_outputs(folder,state)
        return state
