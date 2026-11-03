"""Bounded seed-based grayscale region proposal, without learned phase labels."""
import math
from statistics import median
from PIL import Image
from .geometry import polygon_area
from .models import Point


def simplify_open(points,epsilon=.75):
    if len(points)<3:return points
    a,b=points[0],points[-1];dx,dy=b[0]-a[0],b[1]-a[1]
    denominator=math.hypot(dx,dy)
    distances=[abs(dx*(a[1]-p[1])-(a[0]-p[0])*dy)/denominator if denominator else math.dist(a,p) for p in points[1:-1]]
    maximum=max(distances,default=0)
    if maximum<=epsilon:return [a,b]
    split=distances.index(maximum)+1
    return simplify_open(points[:split+1],epsilon)[:-1]+simplify_open(points[split:],epsilon)


def region_candidate(image,x,y,radius=80,tolerance=24):
    if not (0<=x<image.width and 0<=y<image.height):raise ValueError('Click inside the image.')
    sx,sy=int(x),int(y)
    left,top=max(0,sx-radius),max(0,sy-radius)
    right,bottom=min(image.width,sx+radius+1),min(image.height,sy+radius+1)
    rgba=image.crop((left,top,right,bottom)).convert('RGBA')
    gray=Image.alpha_composite(Image.new('RGBA',rgba.size,'white'),rgba).convert('L')
    px=gray.load();w,h=gray.size;seed=(sx-left,sy-top)
    target=median(px[a,b] for a in range(max(0,seed[0]-1),min(w,seed[0]+2)) for b in range(max(0,seed[1]-1),min(h,seed[1]+2)))
    result={'algorithm':'seed-gray-region/0.1','seed':{'x':x,'y':y},'radius_px':radius,'tolerance':tolerance,
            'points':[],'warnings':[],'pixel_count':0,'polygon_area_px2':None,'simplification_delta_percent':None}
    def reject(reason):return {**result,'reason':reason}
    if abs(px[seed]-target)>tolerance:return reject('Seed lies on a mixed boundary. Click farther inside the object.')
    region={seed};stack=[seed];seen={seed}
    while stack:
        a,b=stack.pop()
        for q in ((a-1,b),(a+1,b),(a,b-1),(a,b+1)):
            if not (0<=q[0]<w and 0<=q[1]<h) or q in seen:continue
            seen.add(q)
            if abs(px[q]-target)<=tolerance:region.add(q);stack.append(q)
    result['pixel_count']=len(region)
    if any(a in (0,w-1) or b in (0,h-1) for a,b in region):
        return reject('Region reaches the search or image edge. It may leak into the background or be truncated. Lower tolerance, increase radius, or draw manually.')
    if len(region)<9:return reject('Too few connected pixels to propose a useful boundary. Use manual geometry or a clearer image.')
    # Directed pixel-cell edges preserve area before simplification. Multiple
    # loops are holes; ambiguous vertices may be touching boundaries.
    edges={}
    for a,b in region:
        for neighbor,start,end in [((a,b-1),(a,b),(a+1,b)),((a+1,b),(a+1,b),(a+1,b+1)),
                                    ((a,b+1),(a+1,b+1),(a,b+1)),((a-1,b),(a,b+1),(a,b))]:
            if neighbor not in region:
                if start in edges:return reject('Touching or ambiguous boundaries detected. Draw a simple region manually.')
                edges[start]=end
    loops=[]
    while edges:
        start=min(edges);p=start;loop=[]
        while True:
            loop.append(p)
            if p not in edges:return reject('Boundary could not be traced safely. Use manual area.')
            p=edges.pop(p)
            if p==start:break
        loops.append(loop)
    if len(loops)!=1:return reject('The candidate contains holes. This version does not fill them silently; use a suitable manual region or defer measurement.')
    ring=loops[0]
    # First remove collinear pixel edges, then simplify the closed ring in two halves.
    ring=[p for i,p in enumerate(ring) if (p[0]-ring[i-1][0])*(ring[(i+1)%len(ring)][1]-p[1])!=(p[1]-ring[i-1][1])*(ring[(i+1)%len(ring)][0]-p[0])]
    farthest=max(range(1,len(ring)),key=lambda i:math.dist(ring[0],ring[i]))
    try: outline=simplify_open(ring[:farthest+1])[:-1]+simplify_open(ring[farthest:]+[ring[0]])[:-1]
    except RecursionError:return reject('Boundary is too complex for this helper. Draw manually or use a clearer image.')
    if len(outline)>250:return reject('Boundary remains too complex to edit reliably. Use manual area or defer measurement.')
    points=[Point(x=a+left,y=b+top) for a,b in outline]
    try:area=polygon_area(points)
    except ValueError:return reject('Simplified boundary is ambiguous. Use manual area.')
    delta=100*abs(area-len(region))/len(region)
    result.update(polygon_area_px2=area,simplification_delta_percent=delta)
    if delta>3:return reject('Boundary simplification changes area by more than the helper limit (3%). This is an algorithm limit, not scientific accuracy; draw manually.')
    width=max(a for a,b in region)-min(a for a,b in region)+1
    height=max(b for a,b in region)-min(b for a,b in region)+1
    if min(width,height)<8:result['warnings'].append('The smaller bounding-box dimension is under 8 pixels. Boundary uncertainty may dominate the result.')
    result['warnings'].append('Connected similar-gray regions can merge touching particles. Check the complete boundary and phase evidence.')
    result['points']=[p.model_dump() for p in points]
    result['reason']='Candidate only. Check the outline before adding it; adding does not mark it reviewed.'
    return result
