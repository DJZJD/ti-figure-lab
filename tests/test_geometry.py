import math
import pytest
from backend.models import Document, Point, Axis, Measurement
from backend.geometry import axis_value, polygon_area, evaluate, validate_image_coordinates

def p(x,y):return Point(x=x,y=y)
def doc():
    d=Document(image_id='a'*64)
    d.spatial.points=[p(100,600),p(200,600)]
    d.spatial.length=10
    return d

def test_area_length_and_ecd_have_known_physical_values():
    d=doc()
    d.measurements=[Measurement(id='square',kind='area',points=[p(200,200),p(400,200),p(400,400),p(200,400)]),
                    Measurement(id='diagonal',kind='line',points=[p(0,0),p(30,40)])]
    results=evaluate(d)['measurements']
    assert results[0]['values']['area']==pytest.approx(400)
    assert results[0]['values']['ecd']==pytest.approx(40/math.sqrt(math.pi))
    assert results[1]['values']['length']==pytest.approx(5)
    d.spatial.length=20
    assert evaluate(d)['measurements'][0]['values']['area']==pytest.approx(1600)

def test_linear_log_and_reversed_image_y():
    a=Axis(points=[p(100,600),p(900,600)],values=[10,90])
    assert axis_value(a,500,'x')==50
    y=Axis(points=[p(100,600),p(100,100)],values=[0,100])
    assert axis_value(y,350,'y')==50
    a.values=[1,100];a.scale='log10'
    assert axis_value(a,500,'x')==pytest.approx(10)
    a.values=[0,100]
    with pytest.raises(ValueError):axis_value(a,500,'x')

def test_self_crossing_and_touching_polygons_are_rejected():
    with pytest.raises(ValueError):polygon_area([p(0,0),p(10,10),p(0,10),p(10,0)])
    with pytest.raises(ValueError):polygon_area([p(0,0),p(10,0),p(10,10),p(5,0),p(0,10)])
    assert polygon_area([p(0,0),p(10,0),p(10,10),p(0,10)])==100
    assert polygon_area([p(0,10),p(10,10),p(10,0),p(0,0)])==100

def test_missing_calibration_never_produces_a_zero_measurement():
    d=Document(image_id='a'*64,measurements=[Measurement(id='x',kind='point',points=[p(50,50)])])
    r=evaluate(d)['measurements'][0]
    assert not r['valid'] and not r['values']

def test_bounds_and_duplicate_ids():
    d=doc(); d.measurements=[Measurement(id='x',kind='line',points=[p(0,0),p(1100,1)])]
    with pytest.raises(ValueError):validate_image_coordinates(d,1000,700)
    d.measurements=[Measurement(id='x',kind='point',points=[p(2,2)])]*2
    with pytest.raises(ValueError):validate_image_coordinates(d,1000,700)
