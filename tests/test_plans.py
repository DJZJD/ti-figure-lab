import copy
import json
from uuid import uuid4
from test_api import client,setup_document


def setup_plan(client):
    master={'schema_version':'TiFM_master_self_contained_v0.1','paper_short_id':'QA','samples':[{'sample_id':'QA'}]}
    ref=client.post('/api/references',files={'file':('qa.json',json.dumps(master).encode())}).json()
    d=setup_document(client);d['reference_id']=ref['id'];d['source']='Synthetic source; no scientific data'
    d['evidence']=[{'id':'e','kind':'caption','locator':'Synthetic fixture caption','text':'Square: 200 x 200 source pixels. Scale bar: 100 pixels = 10 um.'}]
    d['plans']=[{'id':'p','task':'object_area','tool':'area','panel':'Synthetic square','object_identity':'Square interior',
                 'definition':'2D projected area and ECD, one synthetic object','sample_ids':['QA'],'evidence_ids':['e'],
                 'image_quality':'usable','quality_note':'Known pixels and clear scale; no phase inference','stop_conditions':'Stop if edges or scale cannot be resolved'}]
    return d


def assess(client,d):
    r=client.post('/api/evaluate',json=d);assert r.status_code==200,r.text
    return r.json()


def review_plan(client,d):
    d['evidence'][0]['review_basis']=assess(client,d)['planning']['evidence'][0]['review_basis']
    p=assess(client,d)['planning']['plans'][0];assert not p['blockers'],p
    d['plans'][0]['review_basis']=p['review_basis']


def test_evidence_needs_locator_and_notes_cannot_be_only_source(client):
    d=setup_plan(client);d['evidence'][0]['locator']=''
    assert assess(client,d)['planning']['evidence'][0]['status']=='blocked'
    d['evidence'][0]['locator']='Personal thought';d['evidence'][0]['kind']='user_note'
    d['evidence'][0]['review_basis']=assess(client,d)['planning']['evidence'][0]['review_basis']
    assert any('Personal notes alone' in b for b in assess(client,d)['planning']['plans'][0]['blockers'])


def test_review_and_calibration_are_distinct(client):
    d=setup_plan(client);d['spatial']['points']=[];review_plan(client,d)
    p=assess(client,d)['planning']['plans'][0];assert p['status']=='reviewed' and not p['can_start']
    d['spatial']['points']=[{'x':100,'y':600},{'x':200,'y':600}]
    p=assess(client,d)['planning']['plans'][0];assert p['status']=='reviewed' and p['can_start']


def test_low_quality_blocks_geometry_but_transcription_uses_source_evidence(client):
    d=setup_plan(client);review_plan(client,d)
    d['plans'][0]['image_quality']='needs_better_source'
    p=assess(client,d)['planning']['plans'][0];assert not p['can_start'] and any('better source' in b for b in p['blockers'])
    d['plans'][0].update(image_quality='usable',task='reported_value',tool='transcription')
    assert not assess(client,d)['planning']['plans'][0]['blockers']


def test_task_samples_evidence_and_sampling_are_validated(client):
    d=setup_plan(client);review_plan(client,d)
    d['plans'][0].update(task='mean_ecd',tool='point',sample_ids=['missing'],evidence_ids=['missing'])
    errors=assess(client,d)['planning']['plans'][0]['blockers']
    assert any('Tool' in b for b in errors) and any('sample IDs' in b for b in errors)
    assert any('evidence entries' in b for b in errors) and any('sampling scope' in b for b in errors)


def test_evidence_change_invalidates_only_linked_measurement(client):
    d=setup_plan(client);review_plan(client,d)
    m=d['measurements'][0];m.update(plan_id='p',sample='QA',group_label='Square',include_in_summary=True)
    other=copy.deepcopy(m);other.update(id='other',plan_id=None,include_in_summary=False);d['measurements'].append(other)
    rows=assess(client,d)['measurements']
    for obj,row in zip(d['measurements'],rows):obj.update(review_basis=row['review_basis'],status='accepted')
    assert assess(client,d)['summary']['groups'][0]['n_objects']==1
    d['evidence'][0]['text']+=' Modified source text.'
    r=assess(client,d)
    assert not r['measurements'][0]['valid'] and r['measurements'][0]['values']=={}
    assert r['measurements'][1]['valid'] and not r['summary']['groups']
    review_plan(client,d)
    assert assess(client,d)['measurements'][0]['valid']
    assert not assess(client,d)['summary']['groups'] # Geometry needs fresh review too.


def test_linked_measurement_kind_and_sample_must_match(client):
    d=setup_plan(client);review_plan(client,d);d['measurements'][0]['plan_id']='p'
    assert 'sample label' in assess(client,d)['measurements'][0]['error']
    d['measurements'][0]['sample']='QA';d['plans'][0]['task']='length';d['plans'][0]['tool']='line';review_plan(client,d)
    assert not assess(client,d)['measurements'][0]['valid']


def test_plan_packet_roundtrip_and_literal_text(client):
    d=setup_plan(client);d['evidence'][0]['text']+='<script>not executable</script>';review_plan(client,d)
    url='/api/projects/'+str(uuid4());saved=client.put(url,json={'document':d,'expected_revision':0})
    assert saved.status_code==200,saved.text
    restored=client.get(url).json();assert restored['results']['planning']['plans'][0]['status']=='reviewed'
    packet=client.get(url+'/export?format=plan_json').json()
    assert packet['artifact_type']=='manual_task_packet_not_model_output'
    assert packet['evidence'][0]['text']==d['evidence'][0]['text'] and not packet['validation']['model_connected']
    assert client.post(url+'/export-file?format=plan_json').json()['path'].endswith('.task-packet.json')


def test_duplicate_ids_and_unbounded_model_tool_instructions_rejected(client):
    d=setup_plan(client);d['evidence'].append(d['evidence'][0]);assert client.post('/api/evaluate',json=d).status_code==422
    d=setup_plan(client);d['plans'][0]['origin']='llm';assert client.post('/api/evaluate',json=d).status_code==422
    d['plans'][0]['origin']='manual';d['plans'][0]['tool']='run_shell';assert client.post('/api/evaluate',json=d).status_code==422
