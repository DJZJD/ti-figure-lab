"""Descriptive statistics of explicitly included, reviewed 2D objects in one image."""
from collections import defaultdict
from statistics import mean,median,stdev
from .geometry import cross


def on_boundary(p,points):
    return any(abs(cross(a,b,p))<1e-8 and min(a.x,b.x)-1e-8<=p.x<=max(a.x,b.x)+1e-8 and min(a.y,b.y)-1e-8<=p.y<=max(a.y,b.y)+1e-8
               for a,b in zip(points,points[1:]+points[:1]))


def inside(p,points):
    if on_boundary(p,points):return False
    yes=False
    for a,b in zip(points,points[1:]+points[:1]):
        if (a.y>p.y)!=(b.y>p.y) and p.x<(b.x-a.x)*(p.y-a.y)/(b.y-a.y)+a.x:yes=not yes
    return yes


def overlap(a,b):
    # Shared boundary alone is permitted. Positive intersection or identical
    # boundaries are blocked to avoid counting one drawn object twice.
    if all(on_boundary(p,b) for p in a) and all(on_boundary(p,a) for p in b):return True
    for first,second in ((a,b),(b,a)):
        for p,q in zip(first,first[1:]+first[:1]):
            mid=type(p)(x=(p.x+q.x)/2,y=(p.y+q.y)/2)
            if inside(p,second) or inside(mid,second):return True
    return any(cross(p,q,r)*cross(p,q,s)<-1e-8 and cross(r,s,p)*cross(r,s,q)<-1e-8
               for p,q in zip(a,a[1:]+a[:1]) for r,s in zip(b,b[1:]+b[:1]))


def object_summary(doc,results):
    rows={r['id']:r for r in results};eligible=[];omitted=[]
    for m in doc.measurements:
        if m.kind!='area':continue
        r=rows[m.id]
        reason=(m.exclusion_reason or 'Not selected for summary') if not m.include_in_summary else (
            'Invalid geometry or calibration' if not r['valid'] else
            'Needs current review' if m.status!='accepted' or m.review_basis!=r['review_basis'] else
            'Sample or object group missing' if not m.sample.strip() or not m.group_label.strip() else '')
        if reason:omitted.append({'id':m.id,'reason':reason})
        else:eligible.append(m)
    groups=defaultdict(list)
    for m in eligible:groups[(m.sample,m.group_label.strip())].append(m)
    conflicts=set();pairs=[];blocked=None;work=0
    if len(eligible)>200:blocked='More than 200 eligible objects. This prototype withholds summaries beyond its overlap-check limit.'
    else:
        bounds={m.id:(min(p.x for p in m.points),min(p.y for p in m.points),max(p.x for p in m.points),max(p.y for p in m.points)) for m in eligible}
        for i,a in enumerate(eligible):
            for b in eligible[i+1:]:
                ax,ay,ar,ab=bounds[a.id];bx,by,br,bb=bounds[b.id]
                if ar<=bx or br<=ax or ab<=by or bb<=ay:continue
                work+=len(a.points)*len(b.points)
                if work>2_000_000:blocked='Overlap checks exceed the prototype complexity limit. Simplify outlines or use fewer objects.';break
                if overlap(a.points,b.points):conflicts.update((a.id,b.id));pairs.append([a.id,b.id])
            if blocked:break
    summaries=[]
    for (sample,group),members in groups.items():
        issues=[m.id for m in members if m.id in conflicts]
        reason=blocked or ('Overlapping or duplicate included objects: '+', '.join(issues) if issues else None)
        areas=[rows[m.id]['values']['area'] for m in members];ecds=[rows[m.id]['values']['ecd'] for m in members]
        summaries.append({'sample':sample,'group':group,'n_objects':len(members),'n_images':1,'member_ids':[m.id for m in members],
                          'valid':not reason,'reason':reason,'length_unit':doc.spatial.unit,'area_unit':doc.spatial.unit+'²',
                          'mean_area':mean(areas) if not reason else None,'mean_ecd':mean(ecds) if not reason else None,
                          'median_ecd':median(ecds) if not reason else None,'sample_sd_ecd':stdev(ecds) if not reason and len(ecds)>1 else None,
                          'min_ecd':min(ecds) if not reason else None,'max_ecd':max(ecds) if not reason else None})
    return {'groups':summaries,'omitted':omitted,'overlap_pairs':pairs,
            'notice':'Selected reviewed 2D objects from one image. N is object count, not independent specimens. No phase fraction or volume fraction is inferred.'}
