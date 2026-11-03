import json
from uuid import uuid4
import pytest
from backend.references import catalog
from test_api import client,setup_document


def master():
    return {'schema_version':'TiFM_master_self_contained_v0.1','paper_short_id':'TEST',
        'samples':[{'sample_id':'A'},{'sample_id':'B'}],
        'sample_property_value_records':[{'property_record_id':'P1','linked_sample_ids':['A','B'],
          'property_name':'fracture_toughness','value':41.39,'unit':'Mpa·m^0.5','test_specimen_context':'preserve me'}],
        'microstructure_quantification_records':[{'quantification_record_id':'Q1','sample_id':'A','quantification_target':'primary_alpha_fraction','value':52.06,'unit':'%'}],
        'xrd_records':[{'xrd_record_id':'X1','sample_id':'A','peaks':[{'peak_id':1,'two_theta_deg':35.6,'normalized_intensity':.32}]}]}


def test_native_paths_multi_sample_and_units_are_preserved():
    d=master();before=json.dumps(d);c=catalog(d)
    assert c['targets'][0]['sample_ids']==['A','B']
    assert c['targets'][0]['unit']=='Mpa·m^0.5'
    assert c['targets'][2]['path']=='/xrd_records/0/peaks/0/two_theta_deg'
    assert c['targets'][3]['definition']=='normalized_intensity'
    assert json.dumps(d)==before
    d['samples'].append({'sample_id':'A'})
    with pytest.raises(ValueError):catalog(d)


def test_upload_link_roundtrip_and_readonly_export(client):
    raw=json.dumps(master(),ensure_ascii=False).encode();ref=client.post('/api/references',files={'file':('master.json',raw)}).json()
    import backend.app as backend
    assert (backend.REFERENCES/(ref['id']+'.json')).read_bytes()==raw
    d=setup_document(client);d['reference_id']=ref['id'];d['measurements'][0]['reference_path']=ref['targets'][0]['path']
    d['measurements'][0]['review_basis']=client.post('/api/evaluate',json=d).json()['measurements'][0]['review_basis']
    url='/api/projects/'+str(uuid4());r=client.put(url,json={'expected_revision':0,'document':d})
    assert r.status_code==200 and r.json()['document']['measurements'][0]['status']=='accepted'
    exported=client.get(url+'/export').json()
    assert exported['reference']['targets'][0]['sample_ids']==['A','B']
    d['measurements'][0]['reference_path']=ref['targets'][1]['path']
    updated=client.put(url,json={'expected_revision':1,'document':d}).json()
    assert updated['document']['measurements'][0]['status']=='needs_review'
    d['measurements'][0]['reference_path']='/invented/value'
    assert client.post('/api/evaluate',json=d).status_code==422
    assert (backend.REFERENCES/(ref['id']+'.json')).read_bytes()==raw


def test_bad_references_fail_and_dangling_samples_are_not_linkable(client):
    assert client.post('/api/references',files={'file':('bad.json',b'{broken')}).status_code==422
    d=master();d['xrd_records'][0]['sample_id']='missing'
    c=catalog(d);assert len(c['targets'])==2 and len(c['warnings'])==2
    assert client.post('/api/references',files={'file':('bad.json',b'{"n":1e999}')}).status_code==422


def test_peak_api_does_not_create_or_accept_measurements(client):
    image=client.post('/api/demos/graph').json()
    result=client.post('/api/peak-candidates',json={'image_id':image['id'],'point':{'x':504,'y':151},'radius':40})
    assert result.status_code==200 and result.json()['candidates']
    assert client.get('/api/projects').json()==[]
    assert client.post('/api/peak-candidates',json={'image_id':image['id'],'point':{'x':-1,'y':151},'radius':40}).status_code==422


def test_legacy_workspace_migrates_without_changing_values(client):
    d=setup_document(client);d['schema_version']='tifigure-workspace/0.1'
    r=client.put('/api/projects/'+str(uuid4()),json={'expected_revision':0,'document':d})
    assert r.status_code==200
    assert r.json()['document']['schema_version']=='tifigure-workspace/0.7'
    assert r.json()['results']['measurements'][0]['values']['area']==pytest.approx(400)

