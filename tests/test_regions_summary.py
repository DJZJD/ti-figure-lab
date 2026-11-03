import math
from uuid import uuid4
import pytest
from PIL import Image,ImageDraw
from backend.regions import region_candidate
from backend.models import Document,Measurement,Point,RegionOrigin
from backend.geometry import evaluate
from test_api import client


def square(ident,x,y,size,sample='S1',group='Objects'):
    return Measurement(id=ident,kind='area',points=[Point(x=x,y=y),Point(x=x+size,y=y),Point(x=x+size,y=y+size),Point(x=x,y=y+size)],
                       sample=sample,group_label=group,include_in_summary=True,status='accepted')


def document(*objects):
    d=Document(image_id='a'*64,mode='micrograph',measurements=list(objects))
    d.spatial.points=[Point(x=0,y=0),Point(x=10,y=0)];d.spatial.length=1
    return review(d)


def review(d):
    rows={r['id']:r for r in evaluate(d)['measurements']}
    for m in d.measurements:m.review_basis=rows[m.id]['review_basis'];m.status='accepted'
    return d


def test_seed_region_exact_pixel_area_and_outline():
    im=Image.new('RGB',(100,100),'white');ImageDraw.Draw(im).rectangle((30,30,59,49),fill=(70,70,70))
    r=region_candidate(im,40,40,40,24)
    assert r['pixel_count']==600 and r['polygon_area_px2']==600 and len(r['points'])==4
    assert r['simplification_delta_percent']==0
    assert r['points']==region_candidate(im,40,40,40,24)['points']


def test_background_leak_and_holes_are_rejected_not_filled():
    im=Image.new('RGB',(100,100),'white')
    assert not region_candidate(im,50,50,40,24)['points']
    draw=ImageDraw.Draw(im);draw.rectangle((20,20,80,80),fill='black');draw.rectangle((40,40,60,60),fill='white')
    r=region_candidate(im,30,30,60,24)
    assert not r['points'] and 'holes' in r['reason']


def test_tiny_object_warning_and_connected_particles_not_identified():
    im=Image.new('RGB',(100,100),'white');draw=ImageDraw.Draw(im)
    draw.rectangle((30,30,34,34),fill='black')
    r=region_candidate(im,32,32,20,24)
    assert r['points'] and any('8 pixels' in w for w in r['warnings'])
    draw.rectangle((35,30,45,34),fill='black')
    r=region_candidate(im,32,32,30,24)
    assert r['pixel_count']==80 and any('merge' in w for w in r['warnings'])


def test_mean_ecd_is_mean_of_individual_diameters_and_singleton_sd_null():
    d=document(square('a',20,20,20),square('b',80,20,40),square('c',150,20,10,sample='S2'))
    summary=evaluate(d)['summary'];a,b=summary['groups']
    assert a['n_objects']==2 and a['n_images']==1
    assert a['mean_area']==pytest.approx(10)
    assert a['mean_ecd']==pytest.approx((4/math.sqrt(math.pi)+8/math.sqrt(math.pi))/2)
    assert a['mean_ecd']!=pytest.approx(2*math.sqrt(a['mean_area']/math.pi))
    assert a['sample_sd_ecd']==pytest.approx((4/math.sqrt(math.pi))/math.sqrt(2))
    assert b['sample_sd_ecd'] is None


def test_duplicate_and_overlapping_objects_withhold_statistics_but_touching_is_ok():
    d=document(square('a',20,20,20),square('b',20,20,20))
    s=evaluate(d)['summary'];assert not s['groups'][0]['valid'] and s['groups'][0]['mean_area'] is None
    assert s['overlap_pairs']==[['a','b']]
    d.measurements[1].include_in_summary=False
    s=evaluate(d)['summary'];assert s['groups'][0]['valid'] and s['groups'][0]['n_objects']==1
    d=document(square('a',20,20,20),square('b',30,20,20))
    assert not evaluate(d)['summary']['groups'][0]['valid']
    d=document(square('a',20,20,20),square('b',40,20,20))
    assert evaluate(d)['summary']['groups'][0]['valid']


def test_stale_review_missing_group_and_exclusions_do_not_enter_summary():
    d=document(square('a',20,20,20),square('b',80,20,20))
    d.measurements[0].group_label='Changed';d.measurements[1].include_in_summary=False;d.measurements[1].exclusion_reason='Border cropped'
    s=evaluate(d)['summary'];assert not s['groups'] and len(s['omitted'])==2
    assert s['omitted'][1]['reason']=='Border cropped'
    d=document(square('a',20,20,20,group=''))
    assert not evaluate(d)['summary']['groups']


def test_group_statistics_and_region_origin_roundtrip(client):
    meta=client.post('/api/demos/micrograph').json()
    r=client.post('/api/region-candidate',json={'image_id':meta['id'],'point':{'x':300,'y':300},'radius':160,'tolerance':24})
    assert r.status_code==200 and r.json()['points']
    proposal=r.json();points=proposal.pop('points');proposal.pop('reason')
    d=document(square('a',20,20,20));d.image_id=meta['id'];d.measurements[0].points=[Point(**p) for p in points]
    d.measurements[0].region_origin=RegionOrigin.model_validate(proposal);d.measurements[0].acquisition='region_assist'
    # Re-validate nested evidence as a real incoming request would.
    d=Document.model_validate(d.model_dump());review(d)
    url='/api/projects/'+str(uuid4())
    saved=client.put(url,json={'expected_revision':0,'document':d.model_dump()});assert saved.status_code==200,saved.text
    restored=client.get(url).json()
    assert restored['document']['measurements'][0]['region_origin']['pixel_count']==proposal['pixel_count']
    assert restored['results']['summary']['groups'][0]['n_objects']==1
    exported=client.post(url+'/export-file?format=summary_csv').json()
    assert exported['path'].endswith('.summary.csv')
    from pathlib import Path
    content=Path(exported['path']).read_text('utf-8-sig')
    assert 'sample_sd_ecd' in content and 'independent specimens' in content


def test_region_request_is_bounded(client):
    meta=client.post('/api/demos/micrograph').json()
    assert client.post('/api/region-candidate',json={'image_id':meta['id'],'point':{'x':-1,'y':20}}).status_code==422
    assert client.post('/api/region-candidate',json={'image_id':meta['id'],'point':{'x':20,'y':20},'radius':10000}).status_code==422
