"""Measurements are evaluated in canonical image pixels, never display pixels."""
import math
import hashlib
import json
from .models import Document, Axis, Point


def distance(a: Point, b: Point) -> float:
    return math.hypot(a.x-b.x, a.y-b.y)


def cross(a, b, c):
    return (b.x-a.x)*(c.y-a.y)-(b.y-a.y)*(c.x-a.x)


def intersects(a, b, c, d):
    def on(a, b, p):
        return (abs(cross(a,b,p)) < 1e-8 and min(a.x,b.x)-1e-8 <= p.x <= max(a.x,b.x)+1e-8
                and min(a.y,b.y)-1e-8 <= p.y <= max(a.y,b.y)+1e-8)
    if any((on(a,b,c), on(a,b,d), on(c,d,a), on(c,d,b))):
        return True
    return cross(a,b,c)*cross(a,b,d) < 0 and cross(c,d,a)*cross(c,d,b) < 0


def polygon_area(points: list[Point]) -> float:
    n = len(points)
    if n < 3:
        raise ValueError('An area needs at least three vertices.')
    for i in range(n):
        if distance(points[i], points[(i+1)%n]) < 1e-6:
            raise ValueError('Remove duplicate adjacent vertices.')
        for j in range(i+1,n):
            if j == i+1 or (i == 0 and j == n-1):
                continue
            if intersects(points[i],points[(i+1)%n],points[j],points[(j+1)%n]):
                raise ValueError('The polygon crosses or touches itself. Edit its vertices.')
    area = abs(sum(points[i].x*points[(i+1)%n].y-points[(i+1)%n].x*points[i].y for i in range(n)))/2
    if area < 1e-6:
        raise ValueError('The polygon has zero area.')
    return area


def axis_value(axis: Axis, pixel: float, coordinate: str) -> float:
    if len(axis.points) != 2:
        raise ValueError(f'Calibrate the {coordinate.upper()} axis with two reference ticks.')
    p0,p1 = [getattr(p, coordinate) for p in axis.points]
    if abs(p1-p0) < 2:
        raise ValueError(f'{coordinate.upper()} reference ticks must be at least 2 pixels apart.')
    a,b = axis.values
    if a == b:
        raise ValueError('Reference tick values must differ.')
    if axis.scale == 'log10':
        if min(a,b) <= 0:
            raise ValueError('Logarithmic reference values must be positive.')
        a,b = math.log10(a), math.log10(b)
    value = a+(pixel-p0)/(p1-p0)*(b-a)
    if axis.scale == 'log10':
        try:
            value = 10**value
        except OverflowError:
            raise ValueError('The calibrated result exceeds the numeric range.') from None
        if value <= 0:
            raise ValueError('The calibrated logarithmic result is outside the numeric range.')
    if not math.isfinite(value):
        raise ValueError('The calibrated result is not finite.')
    return value


def spatial_factor(doc):
    c = doc.spatial
    if len(c.points) != 2:
        raise ValueError('Set the two scale-bar endpoints first.')
    px = distance(*c.points)
    if px < 2 or c.length <= 0:
        raise ValueError('Use a positive scale length and endpoints at least 2 pixels apart.')
    return c.length / px


def validate_image_coordinates(doc: Document, width: int, height: int):
    ids = [m.id for m in doc.measurements]
    if len(ids) != len(set(ids)):
        raise ValueError('Measurement IDs must be unique.')
    points = doc.graph.x.points + doc.graph.y.points + doc.spatial.points
    for m in doc.measurements:
        points += m.points
        if (m.kind == 'point' and len(m.points)!=1) or (m.kind == 'line' and len(m.points)!=2):
            raise ValueError('Point measurements need one vertex; line measurements need two.')
        if m.kind == 'area' and len(m.points)<3:
            raise ValueError('Area measurements need at least three vertices.')
    if any(not (0 <= p.x <= width and 0 <= p.y <= height) for p in points):
        raise ValueError('A coordinate lies outside the image.')


def evaluate(doc: Document):
    rows=[]
    for m in doc.measurements:
        row={'id':m.id, 'kind':m.kind, 'valid':False, 'values':{}, 'units':{}, 'warnings':[]}
        try:
            if m.kind == 'point':
                row['values']['x']=axis_value(doc.graph.x, m.points[0].x, 'x')
                row['units']['x']=doc.graph.x.unit
                if doc.graph.use_y:
                    row['values']['y']=axis_value(doc.graph.y, m.points[0].y, 'y')
                    row['units']['y']=doc.graph.y.unit
                for coord,axis in [('x',doc.graph.x)]+([('y',doc.graph.y)] if doc.graph.use_y else []):
                    ends=[getattr(p,coord) for p in axis.points]
                    if not min(ends)<=getattr(m.points[0],coord)<=max(ends):
                        row['warnings'].append(f'{coord.upper()} is extrapolated beyond reference ticks.')
            else:
                factor=spatial_factor(doc)
                unit=doc.spatial.unit
                if m.kind == 'line':
                    length=distance(*m.points)
                    if length<1e-6:
                        raise ValueError('The line has zero length.')
                    row['values']={'length':length*factor}
                    row['units']={'length':unit}
                else:
                    area=polygon_area(m.points)*factor**2
                    row['values']={'area':area, 'ecd':2*math.sqrt(area/math.pi)}
                    row['units']={'area':unit+'²','ecd':unit}
            if not all(math.isfinite(v) for v in row['values'].values()):
                raise ValueError('Result exceeds the numeric range.')
            row['valid']=True
        except (ValueError,OverflowError) as err:
            row['values']={}; row['units']={}; row['error']=str(err)
        measurement=m.model_dump(exclude={'status','review_basis','include_in_summary','exclusion_reason'})
        # Preserve existing 0.1 reviews when the newly optional features are unused.
        if not m.reference_path: measurement.pop('reference_path',None)
        if m.acquisition=='manual': measurement.pop('acquisition',None)
        if not m.group_label:measurement.pop('group_label',None)
        if m.region_origin is None:measurement.pop('region_origin',None)
        if m.plan_id is None:measurement.pop('plan_id',None)
        basis={'algorithm':'geometry/0.1','image_id':doc.image_id,'source':doc.source,
               'measurement':measurement,
               'calibration':(doc.graph if m.kind=='point' else doc.spatial).model_dump()}
        if doc.reference_id: basis['reference_id']=doc.reference_id
        if m.plan_id:
            from .plans import plan_context
            basis['plan_context']=plan_context(doc,m.plan_id)
        row['review_basis']=hashlib.sha256(json.dumps(basis,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        rows.append(row)
    calibration={}
    for name,fn in [('x',lambda:axis_value(doc.graph.x,0,'x')),
                    ('y',lambda:axis_value(doc.graph.y,0,'y')),
                    ('spatial',lambda:spatial_factor(doc))]:
        try:
            fn(); calibration[name]={'valid':True}
        except (ValueError,OverflowError) as err:
            calibration[name]={'valid':False,'error':str(err)}
    from .summaries import object_summary
    return {'measurements':rows,'calibration':calibration,'summary':object_summary(doc,rows)}
