"""Unlabelled local micrograph candidates. Pixel geometry only, no phase identity."""
from PIL import Image,ImageDraw
from .regions import region_candidate

def propose_regions(image,box,limit=40):
    rgb=image.convert('RGB');w,h=rgb.size
    if w*h>5000000:raise ValueError('Image too large for candidate segmentation.')
    x0,y0,x1,y1=map(int,box)
    if not 0<=x0<x1<=w or not 0<=y0<y1<=h:raise ValueError('Invalid region bounds.')
    crop=rgb.crop((x0,y0,x1,y1));gray=crop.convert('L');hist=gray.histogram();n=sum(hist)
    total=sum(i*v for i,v in enumerate(hist));weight=0;weighted=0;best=-1;threshold=0
    for i,count in enumerate(hist):
        weight+=count;weighted+=i*count
        if not weight or weight==n:continue
        score=weight*(n-weight)*(weighted/weight-(total-weighted)/(n-weight))**2
        if score>best:best=score;threshold=i
    if best<=0:return dict(candidates=[],threshold=threshold,reason='No grayscale contrast.')
    px=crop.load();gp=gray.load();cw,ch=crop.size
    colored={(x,y) for x in range(cw) for y in range(ch) if max(px[x,y])-min(px[x,y])>45}
    excluded=set(colored)
    for x,y in colored:
        excluded.update((x+dx,y+dy) for dx in range(-3,4) for dy in range(-3,4))
    candidates=[]
    for bright in (True,False):
        mask={(x,y) for x in range(cw) for y in range(ch) if (x,y) not in excluded and (gp[x,y]>threshold)==bright}
        while mask:
            seed=mask.pop();component={seed};stack=[seed]
            while stack:
                x,y=stack.pop()
                for q in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
                    if q in mask:mask.remove(q);component.add(q);stack.append(q)
            if not 60<=len(component)<=cw*ch*.08:continue
            if any(x in (0,cw-1) or y in (0,ch-1) or any((x+dx,y+dy) in excluded for dx,dy in ((-1,0),(1,0),(0,-1),(0,1))) for x,y in component):continue
            interior=[(x,y) for x,y in component if all((x+dx,y+dy) in component for dx in (-1,0,1) for dy in (-1,0,1))]
            if not interior:continue
            cx=sum(x for x,y in component)/len(component);cy=sum(y for x,y in component)/len(component)
            sx,sy=min(interior,key=lambda p:(p[0]-cx)**2+(p[1]-cy)**2)
            binary=Image.new('L',(cw,ch),255);bp=binary.load()
            for q in component:bp[q]=0
            r=region_candidate(binary,sx,sy,max(cw,ch),1)
            if not r['points']:continue
            points=[dict(x=p['x']+x0,y=p['y']+y0) for p in r['points']]
            candidates.append(dict(points=points,seed=dict(x=sx+x0,y=sy+y0),area_px2=r['polygon_area_px2'],polarity='bright' if bright else 'dark',phase=None,status='needs_review'))
    candidates.sort(key=lambda c:(c['seed']['y'],c['seed']['x']))
    return dict(candidates=[dict(c,id=f'region-{i:03}') for i,c in enumerate(candidates[:limit],1)],threshold=threshold,total_candidates=len(candidates),
        warning='Threshold components may split or merge physical objects. Pixel area only; verify identity, boundaries and scale. Colored annotation contacts and crop-edge objects excluded.')

def save_candidates(image,box,path):
    from .paper_jobs import write_json
    r=propose_regions(image,box);write_json(path/'region-candidates.json',r)
    im=image.convert('RGB');d=ImageDraw.Draw(im)
    for c in r['candidates']:
        pts=[(p['x'],p['y']) for p in c['points']];d.line(pts+[pts[0]],fill='#ee2288',width=2);d.text((c['seed']['x'],c['seed']['y']),c['id'],fill='#ee2288')
    im.save(path/'candidates.png');return r

from pydantic import Field
from .paper_jobs import Strict

class RegionSelection(Strict):
    expected_revision: int = Field(ge=1)
    candidate_ids: list[str] = Field(min_length=1,max_length=40)

def selection_document(report,ids,image_id,name,source):
    from .models import Document,Measurement,Point
    index={c['id']:c for c in report['candidates']}
    if len(set(ids))!=len(ids) or any(i not in index for i in ids):raise ValueError('Unknown or duplicate region candidate.')
    doc=Document(image_id=image_id,mode='micrograph',name=name[:200],source=source[:2000])
    for ident in ids:
        c=index[ident]
        doc.measurements.append(Measurement(id=ident,kind='area',points=[Point(**p) for p in c['points']],
            label='Unclassified region',acquisition='region_assist',note='Selected local threshold candidate; boundary and identity require review. Calibrate from the original scale bar before physical measurement.'))
    return doc
