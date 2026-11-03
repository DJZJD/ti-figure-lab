import copy
import json
from pathlib import Path
from uuid import uuid4
import pytest
from test_api import client
from test_plans import setup_plan, review_plan, assess


def request(client):
    d=setup_plan(client);review_plan(client,d)
    return {'document':d,'config':{'provider':'Offline QA','model':'offline-test','protocol':'responses','objective':'Propose a task for this synthetic square; do not measure it.',
                                  'sample_ids':['QA'],'evidence_ids':['e'],'include_image':True,'max_output_tokens':2048}}


def output():
    return {'summary':'MANUAL TEST FIXTURE','warnings':['Not a model result.'],'proposals':[{
        'task':'object_area','tool':'area','panel':'Synthetic square','object_identity':'Designed square','definition':'2D area only',
        'sample_ids':['QA'],'evidence_ids':['e'],'image_quality':'usable','quality_note':'Manual fixture, not visual model inference.',
        'sampling_rule':'','stop_conditions':'Stop if boundary is ambiguous.','rationale':'Test fixture for pending plan validation.'}]}


def validate(client,r,out=None,**extra):
    p=client.post('/api/llm/preview',json=r);assert p.status_code==200,p.text
    return client.post('/api/llm/validate-response',json={**r,'request_hash':p.json()['request_hash'],
        'response_text':json.dumps(out or output()),'import_kind':'manual_fixture',**extra})


def test_offline_adapters_scoped_export_and_image_manifest(client,monkeypatch):
    import socket
    monkeypatch.setattr(socket,'create_connection',lambda *a,**k:pytest.fail('Offline bridge attempted network access'))
    r=request(client);r['document']['evidence'].append({'id':'private','kind':'body','locator':'Omitted','text':'DO_NOT_INCLUDE'})
    for protocol in ('responses','chat_completions'):
        r['config']['protocol']=protocol
        p=client.post('/api/llm/preview',json=r);assert p.status_code==200,p.text
        data=p.json();assert not data['network_executed'] and 'DO_NOT_INCLUDE' not in p.text
        assert 'data:image/png;base64' not in p.text and data['context']['image']['canonical_sha256']
        exported=client.post('/api/llm/export-request',json=r).json()
        packet=json.loads(Path(exported['path']).read_text('utf-8'));body=packet['request_body']
        assert packet['request_hash']==data['request_hash'] and 'data:image/png;base64' in json.dumps(body)
        if protocol=='responses':assert body['text']['format']['strict'] and body['store'] is False and body['max_output_tokens']==2048
        else:assert body['response_format']['json_schema']['strict'] and body['max_completion_tokens']==2048
        assert 'tools' not in body and 'api_key' not in body


def test_selected_context_is_current_and_no_silent_truncation(client):
    r=request(client)
    for patch in ({'sample_ids':['missing']},{'evidence_ids':['missing']},{'sample_ids':['QA','QA']},{'provider':'   '}):
        x=copy.deepcopy(r);x['config'].update(patch);assert client.post('/api/llm/preview',json=x).status_code==422
    r['document']['evidence'][0]['text']+=' changed';assert client.post('/api/llm/preview',json=r).status_code==422
    review_plan(client,r['document']);r['document']['evidence'][0]['kind']='user_note'
    r['document']['evidence'][0]['review_basis']=assess(client,r['document'])['planning']['evidence'][0]['review_basis']
    assert client.post('/api/llm/preview',json=r).status_code==422


def test_response_import_is_pending_with_provenance_and_survives_save(client):
    r=request(client);res=validate(client,r);assert res.status_code==200,res.text
    result=res.json();p=result['plans'][0]
    assert p['review_basis'] is None and p['image_quality']=='unassessed' and p['origin']=='model_response_import'
    assert p['model_provenance']['import_kind']=='manual_fixture' and not p['model_provenance']['network_executed']
    assert 'MANUAL TEST FIXTURE' in p['model_provenance']['response_json']
    d=r['document'];d['plans'].append(p);assert not assess(client,d)['planning']['plans'][-1]['can_start']
    url='/api/projects/'+str(uuid4());saved=client.put(url,json={'document':d,'expected_revision':0});assert saved.status_code==200,saved.text
    restored=client.get(url).json();assert restored['document']['plans'][-1]['model_provenance']==p['model_provenance']


@pytest.mark.parametrize('patch',[{'tool':'run_shell'},{'tool':'point'},{'sample_ids':['HT1']},{'evidence_ids':['unselected']},{'review_basis':'a'*64},{'origin':'manual'},{'pixel_coordinates':[1,2]}])
def test_unbounded_tools_unknown_ids_and_self_approval_rejected(client,patch):
    r=request(client);out=output();out['proposals'][0].update(patch)
    assert validate(client,r,out).status_code==422


@pytest.mark.parametrize('raw',['{"summary":"a","summary":"b","warnings":[],"proposals":[]}', '{"summary": NaN}', '```json\n{}\n```','{"status":"incomplete","output":[]}'])
def test_malformed_duplicate_refusal_or_api_envelope_rejected(client,raw):
    assert validate(client,request(client),response_text=raw).status_code==422


def test_stale_context_rejected_and_text_only_does_not_certify_image(client):
    r=request(client);old=client.post('/api/llm/preview',json=r).json()['request_hash']
    r['config']['objective']='Different objective'
    assert validate(client,r,request_hash=old).status_code==422
    r['config']['include_image']=False
    p=client.post('/api/llm/preview',json=r).json();assert not p['context']['image']['included']
    assert 'canonical_sha256' not in p['context']['image'] and 'input_image' not in json.dumps(p['request_body'])
    out=output();out['proposals'][0]['image_quality']='not_measurable'
    assert validate(client,r,out).json()['plans'][0]['image_quality']=='unassessed'


def test_empty_response_valid_and_existing_manual_review_preserved(client):
    r=request(client);out={'summary':'Insufficient evidence','warnings':[],'proposals':[]}
    res=validate(client,r,out);assert res.status_code==200 and not res.json()['plans']
    assert assess(client,r['document'])['planning']['plans'][0]['status']=='reviewed'
