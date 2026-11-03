import importlib
import io
import sys
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from PIL import Image

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('TIFIGURE_DATA',str(tmp_path/'data'))
    sys.modules.pop('backend.app',None)
    app=importlib.import_module('backend.app').app
    with TestClient(app,headers={'X-TiFigure-Client':'workbench'}) as c:yield c

def setup_document(client):
    meta=client.post('/api/demos/micrograph').json()
    return {'name':'Test project','image_id':meta['id'],'mode':'micrograph',
        'spatial':{'points':[{'x':100,'y':600},{'x':200,'y':600}],'length':10,'unit':'µm'},
        'measurements':[{'id':'square','kind':'area','points':[{'x':200,'y':200},{'x':400,'y':200},{'x':400,'y':400},{'x':200,'y':400}],
                         'label':'Square','sample':'Synthetic only','status':'accepted'}]}

def test_persistence_conflict_and_calibration_invalidation(client):
    d=setup_document(client);pid=str(uuid4());url='/api/projects/'+pid
    d['measurements'][0]['review_basis']=client.post('/api/evaluate',json=d).json()['measurements'][0]['review_basis']
    r=client.put(url,json={'expected_revision':0,'document':d});assert r.status_code==200,r.text
    assert r.json()['results']['measurements'][0]['values']['area']==pytest.approx(400)
    restored=client.get(url).json()
    assert restored['document']['measurements'][0]['status']=='accepted'
    assert client.put(url,json={'expected_revision':0,'document':d}).status_code==409
    d['spatial']['length']=20
    changed=client.put(url,json={'expected_revision':1,'document':d}).json()
    assert changed['results']['measurements'][0]['values']['area']==pytest.approx(1600)
    assert changed['document']['measurements'][0]['status']=='needs_review'
    assert client.get(url+'/export').json()['artifact_type']=='measurement_workspace_not_TiFM_master'
    assert '1600' in client.get(url+'/export?format=csv').text
    exported=client.post(url+'/export-file?format=csv').json()
    from pathlib import Path
    assert Path(exported['path']).is_file()
    assert Path(exported['path']).parent.name=='exports'

def test_disguised_png_is_read_by_content_and_bad_file_is_rejected(client):
    raw=io.BytesIO();Image.new('RGB',(20,20)).save(raw,format='PNG')
    r=client.post('/api/images',files={'file':('actually.png.jpg',raw.getvalue(),'image/jpeg')})
    assert r.status_code==200 and r.json()['width']==20
    assert client.post('/api/images',files={'file':('bad.jpg',b'not an image','image/jpeg')}).status_code==422

def test_invalid_geometry_cannot_be_saved_as_accepted(client):
    d=setup_document(client);d['measurements'][0]['points']=[{'x':0,'y':0},{'x':100,'y':100},{'x':0,'y':100},{'x':100,'y':0}]
    r=client.put('/api/projects/'+str(uuid4()),json={'expected_revision':0,'document':d})
    assert r.status_code==200
    assert r.json()['document']['measurements'][0]['status']=='needs_review'
    assert not r.json()['results']['measurements'][0]['valid']

def test_cross_origin_mutation_blocked(client):
    assert client.post('/api/demos/graph',headers={'Origin':'https://unrelated.example'}).status_code==403

def test_nan_invalid_coordinates_and_missing_sample(client):
    d=setup_document(client);d['measurements'][0]['sample']=''
    url='/api/projects/'+str(uuid4())
    r=client.put(url,json={'expected_revision':0,'document':d})
    assert r.json()['document']['measurements'][0]['status']=='needs_review'
    d['measurements'][0]['points'][0]['x']=-1
    assert client.post('/api/evaluate',json=d).status_code==422
