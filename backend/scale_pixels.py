"""Local horizontal dark scale-bar proposal. Printed value/identity remain unverified."""
import math
from statistics import median
from .graph_pixels import groups
from .models import Point

def refine_scale(image,points):
    if len(points)!=2:raise ValueError('Two scale endpoints required.')
    a,b=sorted(points,key=lambda p:p.x);length=b.x-a.x
    if length<20 or abs(a.y-b.y)>3:raise ValueError('Only horizontal scale bars of at least 20 pixels are supported.')
    w,h=image.size;px=image.convert('RGB').load();center=(a.y+b.y)/2
    margin=max(12,min(80,round(length*.5)));vertical=max(10,min(30,round(length*.2)))
    left=max(0,math.floor(a.x-margin));right=min(w,math.ceil(b.x+margin));top=max(0,round(center)-vertical);bottom=min(h,round(center)+vertical+1)
    runs=[]
    for y in range(top,bottom):
        for span in groups(x for x in range(left,right) if max(px[x,y])<90):
            l,r=span[0],span[-1]
            if l==left or r==right-1:continue
            if not .6*length<=r-l<=1.8*length:continue
            if abs(l-a.x)>margin or abs(r-b.x)>margin:continue
            runs.append((y,l,r))
    clusters=[]
    for y,l,r in runs:
        compatible=[g for g in clusters if y==g[-1][0]+1 and abs(l-g[-1][1])<=3 and abs(r-g[-1][2])<=3]
        if len(compatible)==1:compatible[0].append((y,l,r))
        else:clusters.append([(y,l,r)])
    candidates=[]
    for g in clusters:
        if not 2<=len(g)<=max(6,min(14,round(length*.08))):continue
        l=median(v[1] for v in g);r=median(v[2] for v in g);y=median(v[0] for v in g)
        # Require light surround on both sides of a thin stroke, away from glyphs.
        ys=(g[0][0]-3,g[-1][0]+3)
        if any(yy<0 or yy>=h for yy in ys):continue
        if any(sum(min(px[x,yy])>160 for x in range(round(l),round(r)+1))/(round(r)-round(l)+1)<.85 for yy in ys):continue
        candidates.append(dict(left=l,right=r,y=y,thickness=len(g)))
    if len(candidates)!=1:raise ValueError('No unique nearby horizontal dark scale bar. Calibrate manually; model endpoints are not used.')
    c=candidates[0];ends=[Point(x=c['left'],y=c['y']),Point(x=c['right'],y=c['y'])]
    return ends,dict(method='local-dark-horizontal-scale/1',original_points=[p.model_dump() for p in points],refined_points=[p.model_dump() for p in ends],
        original_length_px=length,refined_length_px=c['right']-c['left'],pixel_length_convention='Distance between stroke endpoint centers',
        printed_value_verified=False,warning='Only nearby stroke geometry verified. Confirm printed length/unit, main panel versus inset, and scale identity before accepting measurements.')
