import copy
import importlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from backend import paper_jobs as pj


TEXT='Sample HT1 aged 8 h at 620 C has tensile strength 1023 MPa in this experiment.'


def test_alternate_reading_preserves_quote_coordinates_without_splicing():
    page=dict(page=1,width=600,height=800,text='Fig. 12 unrelated column Caption ends',words=[],alternate_streams=[
        dict(name='pdf_text_flow',text='Fig. 12 Caption ends',words=[
            dict(text='Fig.',x0=10,x1=20,top=500,bottom=510),dict(text='12',x0=22,x1=30,top=500,bottom=510),
            dict(text='Caption',x0=32,x1=70,top=500,bottom=510),dict(text='ends',x0=10,x1=30,top=512,bottom=522)])])
    assert pj.quote_bounds(page,'Fig. 12 Caption ends')==(2,492,78,530)
    assert pj.matching_stream(page,'Fig. 12 Caption ends')['name']=='pdf_text_flow'
    assert pj.quote_bounds(page,'Caption unrelated column ends') is None
    context=pj.text_context(page)
    assert context['alternate_texts'][0]['text']=='Fig. 12 Caption ends'
    assert 'words' not in context['alternate_texts'][0]


def test_focused_scope_is_archived_and_enforced(intake):
    state=pj.create(intake,b'%PDF-1.4\nfixture','test.pdf','TEST','rules','mechanical_properties')
    folder=pj.job_path(intake,state['id']);packet=pj.read_json(folder/'source/request.json')
    assert state['extraction_scope']=='mechanical_properties'
    assert 'At most 16 records' in packet['body']['messages'][0]['content']
    payload=output();payload['records'][0]['kind']='microstructure';payload['records'][0]['quantity']='primary_alpha_fraction'
    assert complete(intake,state,payload)['status']=='response_failed'
    payload=output();payload['records']*=17
    with pytest.raises(ValueError):pj.parse_output(json.dumps(payload),pj.MechanicalExtraction)
    with pytest.raises(ValueError):pj.create(intake,b'%PDF-1.4','x.pdf','TEST','rules','unknown')


def test_alternate_reading_is_supplied_and_used_in_candidate_evidence(intake,monkeypatch):
    original=pj.parse_pdf
    def with_alternate(raw,folder):
        pages=original(raw,folder);p=pages[0]
        p['alternate_streams']=[dict(name='pdf_text_flow',text=p['text'],words=p['words'])]
        p['text']='Interleaved spatial reading';p['words']=[]
        return pages
    monkeypatch.setattr(pj,'parse_pdf',with_alternate)
    state=create(intake);folder=pj.job_path(intake,state['id'])
    packet=pj.read_json(folder/'source/request.json')
    context=json.loads(packet['body']['messages'][1]['content'])
    assert context['pages'][0]['alternate_texts'][0]['text']==TEXT
    result=complete(intake,state)
    assert result['records'][0]['evidence_text_stream']=='pdf_text_flow'
    assert result['records'][0]['checks']==[]


@pytest.fixture
def intake(tmp_path,monkeypatch):
    def pdf_fixture(raw,folder):
        Image.new('RGB',(800,200),'white').save(folder/'pages/page-001.png')
        words=[];x=5
        for word in TEXT.split():
            words.append(dict(text=word,x0=x,x1=x+len(word)*5,top=20,bottom=30));x+=len(word)*5+5
        return [dict(page=1,text=TEXT,words=words,width=800,height=200,image_width=800,image_height=200,image='pages/page-001.png',native_text_usable=True)]
    monkeypatch.setattr(pj,'parse_pdf',pdf_fixture)
    return tmp_path/'jobs'


def create(intake):
    return pj.create(intake,b'%PDF-1.4\nTEST FIXTURE, NOT A REAL PAPER','test.pdf','PTEST','# Fixture rules')


def output():
    return dict(summary='Manual test fixture, not model inference.',records=[dict(kind='property',quantity='tensile_strength',definition='Reported tensile strength',
        sample_label='HT1',conditions='aged 8 h at 620 C',condition_page=1,condition_quote='Sample HT1 aged 8 h at 620 C',
        value=1023,raw_value='1023',unit='MPa',page=1,locator='Test sentence',evidence_quote=TEXT,notes='Fixture only.')],deferred_items=[],limitations=[])


def complete(intake,state,payload=None):
    pj.claim(intake,state['id'],pj.RunRequest(expected_revision=state['revision'],request_hash=state['request_hash']))
    text=json.dumps(output() if payload is None else payload)
    return pj.finish(intake,state['id'],{'fixture':True},text,{'total_tokens':0})


def test_archive_and_review_preserve_source_model_and_field_columns(intake):
    state=complete(intake,create(intake));ident=state['id'];folder=pj.job_path(intake,ident)
    assert state['status']=='awaiting_review'
    assert state['records'][0]['checks']==[]
    assert state['records'][0]['review_status']=='needs_review'
    assert (folder/'evidence/record-0001.png').is_file()
    assert len((folder/'outputs/property_manual_extraction.reviewed.csv').read_text('utf-8-sig').splitlines())==1
    before=(folder/'rounds/01-extract/output.txt').read_bytes()
    req=pj.ReviewRequest(expected_revision=state['revision'],decision='accepted',reviewer='test',note='Corrected after checking source',
        corrected_value=1024,corrected_unit='MPa',corrected_sample_label='HT1')
    state=pj.review(intake,ident,'record-0001',req)
    assert state['records'][0]['value']==1023
    assert state['records'][0]['review']['corrected_value']==1024
    assert (folder/'rounds/01-extract/output.txt').read_bytes()==before
    with pytest.raises(ValueError,match='changed'):pj.review(intake,ident,'record-0001',req)
    with zipfile.ZipFile(io.BytesIO(pj.bundle(intake,ident))) as z:
        assert z.read('source.pdf').startswith(b'%PDF-1.4')
        assert '1024' in z.read('outputs/property_manual_extraction.reviewed.csv').decode('utf-8-sig')
        assert 'rounds/01-extract/input.json' in z.namelist()
        manifest=json.loads(z.read('archive-manifest.json'))
        assert manifest['final_master'] is False
        assert all(pj.digest(z.read(f['path']))==f['sha256'] for f in manifest['files'])


def test_hallucinated_quote_value_condition_and_conflicts_flagged(intake):
    payload=output();r=payload['records'][0]
    r.update(value=99,condition_quote='No such condition',evidence_quote='invented strength 1023')
    state=complete(intake,create(intake),payload)
    assert len(state['records'][0]['checks'])>=3
    payload=output();other=copy.deepcopy(payload['records'][0]);other.update(value=1050,raw_value='1050');payload['records'].append(other)
    state=complete(intake,create(intake),payload)
    assert all(any('Different values' in c for c in r['checks']) for r in state['records'])


def test_stale_source_no_duplicate_call_and_invalid_output_retained(intake):
    state=create(intake);request=pj.RunRequest(expected_revision=1,request_hash=state['request_hash'])
    pj.claim(intake,state['id'],request)
    with pytest.raises(ValueError):pj.claim(intake,state['id'],request)
    state=pj.finish(intake,state['id'],{'fixture':True},'{bad',{},None)
    assert state['status']=='response_failed'
    assert (pj.job_path(intake,state['id'])/'rounds/01-extract/output.txt').read_text()=='{bad'
    other=create(intake);(pj.job_path(intake,other['id'])/'source.pdf').write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed'):pj.claim(intake,other['id'],pj.RunRequest(expected_revision=1,request_hash=other['request_hash']))


def test_strict_scope_range_and_path_controls(intake):
    assert pj.center_matches(1023,'1023 ± 30')
    assert not pj.center_matches(12,'10–14')
    assert not pj.center_matches(12,'about 12')
    assert not pj.center_matches(1023,'1050')
    with pytest.raises(ValueError):pj.parse_output('{"summary":"a","summary":"b"}')
    payload=output();payload['records'][0]['quantity']='unknown'
    with pytest.raises(ValueError):pj.parse_output(json.dumps(payload))
    state=create(intake)
    with pytest.raises(ValueError):pj.asset(intake,state['id'],'../job.json')
    with pytest.raises(ValueError):pj.job_path(intake,'../outside')


def test_api_runs_transport_once_and_no_reference_answer_required(intake,tmp_path,monkeypatch):
    monkeypatch.setenv('TIFIGURE_DATA',str(tmp_path/'api'))
    sys.modules.pop('backend.app',None)
    mod=importlib.import_module('backend.app')
    calls=[]
    async def fake_send(body):
        calls.append(body)
        return dict(choices=[dict(finish_reason='stop',message=dict(content=json.dumps(output())))],usage={'total_tokens':5})
    monkeypatch.setattr(mod.deepseek,'send',fake_send)
    mod.deepseek.configure('synthetic-test-key-not-a-real-credential')
    with TestClient(mod.app,headers={'X-TiFigure-Client':'workbench'}) as client:
        r=client.post('/api/paper-jobs',files={'file':('test.pdf',b'%PDF-1.4\nfixture','application/pdf')},data={'paper':'PTEST'})
        assert r.status_code==200,r.text
        state=r.json();assert not calls
        packet=client.get(f'/api/paper-jobs/{state["id"]}/request').json()
        assert packet['reference_answers_supplied'] is False
        url=f'/api/paper-jobs/{state["id"]}/run';body=dict(expected_revision=1,request_hash=state['request_hash'])
        assert client.post(url,json=body).status_code==200
        result=client.get(f'/api/paper-jobs/{state["id"]}').json()
        assert result['status']=='awaiting_review',result
        assert client.post(url,json=body).status_code==409
        assert len(calls)==1
        assert client.get(f'/api/paper-jobs/{state["id"]}/archive').status_code==200
        assert client.get(f'/api/paper-jobs/{state["id"]}/assets/evidence/record-0001.png').status_code==200


def test_missing_key_does_not_consume_attempt(intake,tmp_path,monkeypatch):
    monkeypatch.setenv('TIFIGURE_DATA',str(tmp_path/'api'))
    sys.modules.pop('backend.app',None);mod=importlib.import_module('backend.app')
    with TestClient(mod.app,headers={'X-TiFigure-Client':'workbench'}) as c:
        state=c.post('/api/paper-jobs',files={'file':('test.pdf',b'%PDF-1.4\nfixture')},data={'paper':'PTEST'}).json()
        assert c.post(f'/api/paper-jobs/{state["id"]}/run',json=dict(expected_revision=1,request_hash=state['request_hash'])).status_code==422
        assert c.get(f'/api/paper-jobs/{state["id"]}').json()['network_calls_attempted']==0


def test_local_table_evidence_preserves_original_candidates_and_archives(intake,tmp_path,monkeypatch):
    from backend import table_evidence as te
    calls=[]
    def build(folder,state):
        calls.append(state['id']);d=folder/'table-evidence';d.mkdir()
        Image.new('RGB',(40,40),'white').save(d/'record-0001-overlay.png')
        pj.write_json(d/'audit.json',{'fixture':True})
        state['table_evidence_run']={'summary':{'supported':1}}
        state['records'][0]['table_evidence']={'status':'supported','overlay_image':'table-evidence/record-0001-overlay.png'}
    monkeypatch.setattr(te,'build',build)
    monkeypatch.setenv('TIFIGURE_DATA',str(tmp_path/'cell-api'))
    sys.modules.pop('backend.app',None);mod=importlib.import_module('backend.app')
    state=complete(mod.PAPER_JOBS,create(mod.PAPER_JOBS));before=copy.deepcopy(state['records'][0])
    with TestClient(mod.app,headers={'X-TiFigure-Client':'workbench'}) as c:
        base=f'/api/paper-jobs/{state["id"]}'
        body={'expected_revision':state['revision']}
        result=c.post(base+'/table-evidence',json=body);assert result.status_code==200,result.text
        after=result.json()['records'][0]
        assert {k:after[k] for k in before}==before
        assert c.post(base+'/table-evidence',json=body).status_code==422
        assert c.get(base+'/assets/table-evidence/record-0001-overlay.png').status_code==200
        with zipfile.ZipFile(io.BytesIO(c.get(base+'/archive').content)) as z:
            assert 'table-evidence/audit.json' in z.namelist()
    assert len(calls)==1
    automatic=pj.create(intake,b'%PDF-1.4\nfixture','t.pdf','TEST','rules','mechanical_properties')
    automatic=complete(intake,automatic)
    assert automatic['table_evidence_run']['summary']['supported']==1 and len(calls)==2
