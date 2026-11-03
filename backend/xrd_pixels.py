"""Bounded dark-frame/tick refinement. No tick values or curve identity inferred."""
from .graph_pixels import groups


def scan_peaks(image, frame, radius=40):
    """Image-first proposals, independent of model peak coordinates or sample labels.

    Bounds must be the calibrated plot frame. Text and overlapping traces can
    still create false positives; IDs identify candidates, never phase identity.
    """
    if image.width*image.height>5000000:
        raise ValueError('Image too large for candidate scan.')
    x0,y0,x1,y1=map(int,frame)
    if not (0<=x0<x1<=image.width and 0<=y0<y1<=image.height):
        raise ValueError('Invalid plot frame.')
    if not 20<=radius<=80:raise ValueError('Radius must be 20–80 pixels.')
    from statistics import median
    rgb=image.convert('RGB');px=rgb.load();mask=set()
    # Long connected strokes distinguish traces from disconnected text glyphs.
    # No fixed author color; touching annotations remain an explicit limitation.
    from PIL import ImageFilter
    # A local robust background avoids treating a publisher's gradient as ink.
    background=rgb.filter(ImageFilter.MedianFilter(25)).load()
    for x in range(x0+8,x1-8):
        for y in range(y0+8,y1-8):
            if sum((px[x,y][k]-background[x,y][k])**2 for k in range(3))>65**2:mask.add((x,y))
    components=[]
    while mask:
        seed=mask.pop();stack=[seed];component=[seed]
        while stack:
            x,y=stack.pop();color=px[x,y]
            for dx,dy in ((-1,0),(1,0),(0,-1),(0,1),(-1,-1),(-1,1),(1,-1),(1,1)):
                q=(x+dx,y+dy)
                if q in mask and sum(abs(px[q][k]-color[k]) for k in range(3))<180:
                    mask.remove(q);stack.append(q);component.append(q)
        if max(x for x,y in component)-min(x for x,y in component)>(x1-x0)*.55:
            components.append(component)
    found=[]
    components.sort(key=lambda c:median(y for x,y in c))
    for trace_index,component in enumerate(components,1):
        columns={}
        for x,y in component:columns.setdefault(x,[]).append(y)
        top={x:min(ys) for x,ys in columns.items()}
        for x,y in sorted(top.items()):
            if not (x0+8<x<x1-8 and y0+8<y<y1-8):continue
            near=[top.get(xx) for xx in range(x-3,x+4)]
            if any(v is None for v in near) or y>min(near):continue
            left=[top[xx] for xx in range(x-12,x-3) if xx in top]
            right=[top[xx] for xx in range(x+4,x+13) if xx in top]
            if len(left)<6 or len(right)<6:continue
            rise=min(median(left)-y,median(right)-y)
            if rise<4:continue
            found.append(dict(x=x,y=y,local_rise_px=rise,kind='connected_trace_apex',trace_id=f'trace-{trace_index:03}'))
    kept=[]
    for p in sorted(found,key=lambda p:-p['local_rise_px']):
        if any(abs(p['x']-q['x'])<6 and abs(p['y']-q['y'])<8 for q in kept):continue
        kept.append(p)
    return [dict(p,id=f'candidate-{i:03}',sample=None,review='pending')
            for i,p in enumerate(sorted(kept,key=lambda p:(p['x'],p['y'])),1)]



def refine_axis(image,read,box):
    w,h=image.size
    if w*h>5000000:raise ValueError('Image too large for local tick refinement.')
    px=image.convert('RGB').load()
    def dark(x,y):return max(px[x,y])<110
    lo=max(0,int(box.left*w/1000-.10*w));hi=min(w,int(box.right*w/1000+.10*w))
    rows=[]
    for y in range(max(0,int(box.top*h/1000-.12*h)),min(h,int(box.bottom*h/1000+.18*h))):
        spans=groups(x for x in range(lo,hi) if dark(x,y))
        if spans:
            span=max(spans,key=len)
            if len(span)>.4*w:rows.append((y,span[0],span[-1]))
    pairs=[]
    for top in rows:
        if abs(top[0]-box.top*h/1000)>.10*h:continue
        for bottom in rows:
            y0,l,r=top;y1,l1,r1=bottom
            if y1-y0<.25*h or abs(l-l1)>4 or abs(r-r1)>4:continue
            x0=round((l+l1)/2);x1=round((r+r1)/2)
            if any(sum(any(dark(xx,y) for xx in range(max(0,x-2),min(w,x+3))) for y in range(y0,y1+1))/(y1-y0+1)<.95 for x in (x0,x1)):continue
            # A baseline inside the plot is not the frame bottom if both sides continue down.
            if any(sum(any(dark(xx,y) for xx in range(max(0,x-2),min(w,x+3))) for y in range(y1+5,min(h,y1+20)))>5 for x in (x0,x1)):continue
            pairs.append((x0,y0,x1,y1))
    if not pairs:raise ValueError('No complete bounded frame found.')
    frame=min(pairs,key=lambda b:abs(b[1]-box.top*h/1000)+abs(b[3]-box.bottom*h/1000))
    x0,y0,x1,y1=frame;depth=max(10,round(w*.014));gap=4
    spans=groups(x for x in range(x0+gap,x1-gap) if sum(dark(x,y) for y in range(y1-depth,y1-gap))>=(depth-gap)*.6)
    xs=[float(x0),*[sum(s)/len(s) for s in spans if len(s)<depth],float(x1)]
    corrected=read.model_copy(deep=True);changes=[];chosen=[]
    for tick in corrected.ticks:
        original=tick.model_dump();candidates=[x for x in xs if abs(x-tick.x*w/1000)<=.025*w]
        if len(candidates)!=1:raise ValueError('No unique nearby pixel tick for each model tick.')
        x=candidates[0]
        if x in chosen:raise ValueError('Multiple model ticks map to one pixel tick.')
        chosen.append(x);tick.x=x*1000/w;tick.y=y1*1000/h
        changes.append(dict(original=original,corrected=tick.model_dump(),shift_px=x-original['x']*w/1000))
    bounds=box.model_copy(update=dict(left=x0*1000/w,top=y0*1000/h,right=x1*1000/w,bottom=y1*1000/h))
    return corrected,bounds,dict(method='dark_frame_and_nearby_ticks/1',frame_pixels=frame,tick_candidates_px=xs,changes=changes,
        notice='Model values retained; only positions refined. No OCR, sample or peak validation.')


