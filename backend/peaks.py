"""Bounded local raster peak candidates. No phase/curve identification or ML."""
import math
from statistics import median
from PIL import Image


def peak_candidates(image: Image.Image, x: float, y: float, radius: int = 40):
    if not (0 <= x < image.width and 0 <= y < image.height):
        raise ValueError('Click inside the image.')
    left, top = max(0, int(x)-radius), max(0, int(y)-radius)
    right, bottom = min(image.width, int(x)+radius+1), min(image.height, int(y)+radius+1)
    rgba = image.crop((left, top, right, bottom)).convert('RGBA')
    white = Image.new('RGBA', rgba.size, 'white')
    rgb = Image.alpha_composite(white, rgba).convert('RGB')
    pixels = rgb.load()
    width, height = rgb.size
    brightness = [sum(pixels[i,j])/3 for i in range(width) for j in range(height)]
    if median(brightness) < 90:
        return {'candidates': [], 'reason': 'This helper needs a light background. Use Point for this region.'}
    # Estimate each row independently: publisher figures can have tinted gradients.
    # This assumes background occupies most of each local row, not a fixed trace color.
    backgrounds=[tuple(median([pixels[i,j][k] for i in range(width)]) for k in range(3)) for j in range(height)]
    # Every column may contain several traces. Keep separate runs rather than
    # taking the topmost pixel, which would select only the upper stacked trace.
    columns = []
    mask=set()
    for i in range(width):
        runs=[]; start=None
        for j in range(height+1):
            ink=False
            if j < height:
                color=pixels[i,j]
                ink=sum((color[k]-backgrounds[j][k])**2 for k in range(3))>55**2
                if ink: mask.add((i,j))
            if ink and start is None: start=j
            if not ink and start is not None:
                if start>0 and j-start<height:
                    # Steep thick strokes have long vertical runs near an apex;
                    # their midpoint is biased downwards. Follow the upper edge
                    # with a small stroke-center offset instead.
                    cy=start+min(1.5,(j-start-1)/2)
                    runs.append((cy,pixels[i,round(cy)]))
                start=None
        columns.append(runs)
    span=max(5,min(12,radius//3))
    candidates=[]
    for i in range(span,width-span):
        for cy,color in columns[i]:
            tracks=[]
            for direction in (-1,1):
                track=[cy]; previous=cy
                for step in range(1,span+1):
                    options=[v for v,c in columns[i+direction*step]
                             if abs(v-previous)<=30 and sum(abs(c[k]-color[k]) for k in range(3))<210]
                    if not options: break
                    previous=min(options,key=lambda v:abs(v-previous));track.append(previous)
                tracks.append(track)
            if any(len(t)<2 for t in tracks): continue
            # A local maximum of plotted intensity is a minimum of image Y.
            if any(min(t)<cy-1 for t in tracks): continue
            prominence=min(median(t[-min(3,len(t)-1):])-cy for t in tracks)
            if prominence<2.5: continue
            px,py=left+i,top+cy
            distance=math.hypot(px-x,py-y)
            if distance>radius: continue
            candidates.append({'x':px,'y':py,'distance_px':round(distance,2),
                               'local_rise_px':round(prominence,2),'kind':'supported_apex'})
    if not candidates:
        # A one-column raster spike may have no resolvable shoulders. Expose a
        # weaker local stroke-tip proposal, explicitly not a confirmed apex.
        remaining=set(mask)
        while remaining:
            seed=remaining.pop();stack=[seed];component=[seed]
            while stack:
                a,b=stack.pop()
                for da,db in ((-1,0),(1,0),(0,-1),(0,1),(-1,-1),(-1,1),(1,-1),(1,1)):
                    q=(a+da,b+db)
                    if q in remaining: remaining.remove(q);stack.append(q);component.append(q)
            if len(component)<8: continue
            low=min(b for a,b in component);high=max(b for a,b in component)
            if low<2 or high-low<6: continue
            tip_x=sum(a for a,b in component if b==low)/sum(1 for a,b in component if b==low)
            px,py=left+tip_x,top+low
            distance=math.hypot(px-x,py-y)
            if distance<=radius:
                candidates.append({'x':px,'y':py,'distance_px':round(distance,2),
                                   'local_rise_px':None,'kind':'unresolved_stroke_tip'})
    # Collapse adjacent pixels from one rasterized apex, preserving separate peaks.
    ranked=sorted(candidates,key=lambda p:(p['distance_px'],-(p['local_rise_px'] or 0)))
    kept=[]
    for item in ranked:
        if any(abs(item['x']-q['x'])<=4 and abs(item['y']-q['y'])<=4 for q in kept): continue
        kept.append(item)
        if len(kept)==6: break
    reason='No supported peak top found nearby. Try a different radius or use Point.'
    if kept:
        reason=('Only stroke tips could be resolved. They may be labels or artifacts; verify manually.'
                if kept[0]['kind']=='unresolved_stroke_tip' else
                'Choose a candidate after checking its curve. Ranking is distance, not confidence.')
    return {'candidates':kept,'reason':reason}
