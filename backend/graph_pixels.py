"""Local candidates for light-background framed plots and colored hollow markers.

Candidate IDs carry geometry, never tick values or phase/curve identity.
No paper-specific coordinates, colors, values or reference answers are used.
"""
from collections import deque
from PIL import Image, ImageDraw


def groups(indices):
    result=[]
    for x in indices:
        if not result or x>result[-1][-1]+1:result.append([x])
        else:result[-1].append(x)
    return result


def detect(path,guess):
    with Image.open(path) as im:img=im.convert('RGB')
    w,h=img.size
    if w*h>5_000_000:raise ValueError('Local candidate image exceeds five million pixels.')
    px=img.load()
    def dark(x,y):return max(px[x,y])<110
    # Detect long horizontal frame strokes; keep only near the proposed plot.
    left=guess['left']*w/1000;right=guess['right']*w/1000
    top=guess['top']*h/1000;bottom=guess['bottom']*h/1000
    lo=max(0,int(left-.22*w));hi=min(w,int(right+.22*w))
    strokes=[]
    for y in range(max(0,int(top-.16*h)),min(h,int(bottom+.16*h))):
        runs=groups(x for x in range(lo,hi) if dark(x,y))
        if runs:
            run=max(runs,key=len)
            if len(run)>.4*w:strokes.append((y,run[0],run[-1]))
    if not strokes:raise ValueError('No sufficiently long dark frame strokes found.')
    def choose(y):return min(strokes,key=lambda s:abs(s[0]-y)-.04*(s[2]-s[1]))
    a=choose(top);b=choose(bottom)
    y0,y1=a[0],b[0]
    if y1-y0<.25*h:raise ValueError('Frame height is too small or ambiguous.')
    x0=round((a[1]+b[1])/2);x1=round((a[2]+b[2])/2)
    if abs(a[1]-b[1])>5 or abs(a[2]-b[2])>5:raise ValueError('Frame edges do not agree.')
    for x in [x0,x1]:
        if sum(any(dark(xx,y) for xx in range(max(0,x-2),min(w,x+3))) for y in range(y0,y1+1))/(y1-y0+1)<.9:
            raise ValueError('Vertical frame edge is missing.')
    depth=max(8,round(w*.012));gap=max(3,round(w*.003))
    xt=groups(x for x in range(x0+gap,x1-gap) if sum(dark(x,y) for y in range(y1-depth,y1-gap))>=max(2,(depth-gap)*.45))
    yt=groups(y for y in range(y0+gap,y1-gap) if sum(dark(x,y) for x in range(x0+gap,x0+depth))>=max(2,(depth-gap)*.45))
    xs=[sum(g)/len(g) for g in xt if len(g)<depth]
    ys=[float(y0)]+[sum(g)/len(g) for g in yt if len(g)<depth]+[float(y1)]
    ticks={'x':[dict(id=f'X{i+1}',x=x,y=float(y1)) for i,x in enumerate(xs)],
           'y':[dict(id=f'Y{i+1}',x=float(x0),y=y) for i,y in enumerate(ys)]}
    # Flood complement of chromatic strokes. Closed interiors propose marker centers.
    mask=set()
    for y in range(y0+1,y1):
        for x in range(x0+1,x1):
            c=px[x,y]
            if max(c)-min(c)>55 and min(c)<190:mask.add((x,y))
    seen=set();holes=[]
    for sy in range(y0+1,y1):
        for sx in range(x0+1,x1):
            if (sx,sy) in seen or (sx,sy) in mask:continue
            q=deque([(sx,sy)]);seen.add((sx,sy));area=0;sumx=sumy=0;edge=False
            minx=maxx=sx;miny=maxy=sy
            while q:
                x,y=q.popleft();area+=1;sumx+=x;sumy+=y
                minx=min(minx,x);maxx=max(maxx,x);miny=min(miny,y);maxy=max(maxy,y)
                if x in (x0+1,x1-1) or y in (y0+1,y1-1):edge=True
                for nx,ny in [(x-1,y),(x+1,y),(x,y-1),(x,y+1)]:
                    if x0<nx<x1 and y0<ny<y1 and (nx,ny) not in mask and (nx,ny) not in seen:
                        seen.add((nx,ny));q.append((nx,ny))
            bw=maxx-minx+1;bh=maxy-miny+1
            if not edge and 12<=area<=w*h*.003 and .3<bw/bh<3 and min(bw,bh)>=4:
                holes.append(dict(x=sumx/area,y=sumy/area,interior_area=area,bbox=[minx,miny,maxx,maxy]))
    holes.sort(key=lambda p:p['x'])
    if not holes or len(holes)>100:raise ValueError('No bounded set of closed colored marker interiors found.')
    for i,p in enumerate(holes):p['id']=f'M{i+1}'
    return dict(width=w,height=h,frame=dict(left=x0,top=y0,right=x1,bottom=y1),ticks=ticks,markers=holes,
        method='dark frame strokes and closed chromatic interiors; marker center is interior centroid',
        warnings=['Candidate identity and tick values require verification. No OCR or curve/phase classification.',
                  'Supports light backgrounds, full dark rectangular frames, and colored hollow symbols only.'])


def overlay(path,candidates,destination):
    with Image.open(path) as im:img=im.convert('RGB')
    d=ImageDraw.Draw(img)
    for axis in ['x','y']:
        for p in candidates['ticks'][axis]:
            x,y=p['x'],p['y'];d.ellipse((x-3,y-3,x+3,y+3),outline='blue',width=2)
            d.text((x+5,y+5),p['id'],fill='blue')
    for p in candidates['markers']:
        x,y=p['x'],p['y'];d.ellipse((x-5,y-5,x+5,y+5),outline='green',width=2);d.text((x+10,y-10),p['id'],fill='green')
    img.save(destination)
