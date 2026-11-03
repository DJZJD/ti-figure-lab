import io
import json
from uuid import uuid4
import pytest
from PIL import Image
from test_api import client
from test_table_pipeline import case, artifact, master
from backend.table_pipeline import compare_table, sha256


@pytest.fixture
def batches(client,tmp_path,monkeypatch):
    import backend.app as server
    root=tmp_path/'registered';root.mkdir()
    (root/'configs').mkdir();out=root/'outputs/table-batch';out.mkdir(parents=True)
    source=root/'sources';source.mkdir()
    (source/'paper.pdf').write_bytes(b'fake-pdf')
    reference=master();reference['schema_version']='TiFM_master_self_contained_v0.1'
    reference['sample_property_value_records'][0]['property_record_id']='PROP1'
    (source/'master.json').write_text(json.dumps(reference),encoding='utf-8')
    c=case()
    (root/'configs/table-cases.json').write_text(json.dumps(dict(schema_version='tifigure-table-cases/1',cases=[c.model_dump()])),encoding='utf-8')
    image=io.BytesIO();Image.new('RGB',(20,20),'white').save(image,format='PNG');image=image.getvalue()
    (root/'page.png').write_bytes(image)
    pid=str(uuid4())
    (root/'configs/table-workspaces.json').write_text(json.dumps({c.id:dict(image='page.png',image_sha256=sha256(image),project_id=pid)}))
    extraction=json.dumps(artifact(c)).encode()
    (out/'example-table-extraction.json').write_bytes(extraction)
    comparison=compare_table(c,extraction,source,root)
    (out/'example-table-comparison.json').write_text(json.dumps(comparison),encoding='utf-8')
    monkeypatch.setattr(server,'ROOT',root);monkeypatch.setattr(server,'TABLE_SOURCE_ROOT',source)
    return dict(client=client,root=root,out=out,source=source,pid=pid)


def preview(batches):
    response=batches['client'].get('/api/table-batches/example-table')
    assert response.status_code==200,response.text
    return response.json()


def import_batch(batches):
    p=preview(batches)
    return batches['client'].post('/api/table-batches/example-table/import',json={'preview_hash':p['preview_hash']})


def test_preview_read_only_and_registered_image(batches):
    c=batches['client'];p=preview(batches)
    assert not p['existing_project'] and p['summary']['outside_existing_targets']==3
    assert c.get('/api/projects').json()==[]
    assert c.get(p['image_url']).headers['content-type']=='image/png'
    assert c.get('/api/table-batches').json()['items'][0]['status']=='ready'
    assert c.get('/api/table-batches/unknown').status_code==422


def test_import_attaches_evidence_without_approval_or_extra_values(batches):
    c=batches['client'];response=import_batch(batches)
    assert response.status_code==200,response.text
    assert response.json()['reused'] is False
    saved=c.get('/api/projects/'+batches['pid']).json()
    assert len(saved['document']['reported_values'])==1
    r=saved['document']['reported_values'][0]
    assert r['evidence_ids']==['batch-source','batch-interpretation']
    assert r['uncertainty_text']=='12' and r['uncertainty_kind']=='unspecified'
    assert r['review_basis'] is None and r['source_class']=='unknown'
    assert not any(r[k] for k in ('source_checked','sample_checked','quantity_checked','value_checked'))
    assert all(e['review_basis'] is None for e in saved['document']['evidence'])
    assert saved['results']['reported_values'][0]['status']!='reviewed'


def test_repeat_import_preserves_user_edits_and_revision(batches):
    c=batches['client'];assert import_batch(batches).status_code==200
    url='/api/projects/'+batches['pid'];saved=c.get(url).json()
    saved['document']['reported_values'][0]['value_text']='899'
    assert c.put(url,json={'document':saved['document'],'expected_revision':1}).status_code==200
    result=import_batch(batches).json()
    assert result['reused'] is True and result['revision']==2
    assert c.get(url).json()['document']['reported_values'][0]['value_text']=='899'
    assert len(c.get('/api/projects').json())==1


@pytest.mark.parametrize('change',['pdf','image','comparison','master','failed-run'])
def test_stale_sources_and_modified_artifacts_block_import(batches,change):
    c=batches['client'];p=preview(batches)
    if change=='pdf':(batches['source']/'paper.pdf').write_bytes(b'changed')
    if change=='image':(batches['root']/'page.png').write_bytes(b'changed')
    if change=='master':(batches['source']/'master.json').write_text('{}')
    if change=='comparison':
        path=batches['out']/'example-table-comparison.json';data=json.loads(path.read_text())
        data['records'][0]['reported_value_draft']['source_checked']=True;path.write_text(json.dumps(data))
    if change=='failed-run':
        (batches['out']/'extract-batch.json').write_text(json.dumps({'items':[{'case_id':'example-table','status':'failed'}]}))
    assert c.post('/api/table-batches/example-table/import',json={'preview_hash':p['preview_hash']}).status_code==422
    assert c.get('/api/projects').json()==[]
    assert c.get('/api/table-batches').json()['items'][0]['status']=='unavailable'


def test_preview_token_required_and_cross_origin_blocked(batches):
    c=batches['client'];url='/api/table-batches/example-table/import'
    assert c.post(url,json={'preview_hash':'0'*64}).status_code==409
    assert c.post(url,json={'preview_hash':preview(batches)['preview_hash']},headers={'Origin':'https://other.example'}).status_code==403
    assert c.get('/api/projects').json()==[]


def test_existing_different_image_is_not_overwritten(batches):
    c=batches['client'];assert import_batch(batches).status_code==200
    url='/api/projects/'+batches['pid'];saved=c.get(url).json()
    saved['document']['image_id']=c.post('/api/demos/graph').json()['id']
    assert c.put(url,json={'document':saved['document'],'expected_revision':1}).status_code==200
    assert import_batch(batches).status_code==409
    assert c.get(url).json()['revision']==2


def test_source_conflict_evidence_is_attached(batches):
    from backend.table_pipeline import TableCase
    root=batches['root'];out=batches['out'];source=batches['source']
    config=json.loads((root/'configs/table-cases.json').read_text())
    config['cases'][0]['comparison']['source_reviews']=['review.json']
    (root/'configs/table-cases.json').write_text(json.dumps(config))
    finding=dict(paper='TEST',master_sha256=sha256((source/'master.json').read_bytes()),sample_ids=['S1'],
        target_path='/sample_property_value_records/0/value',source_value=900,finding='source_body_table_conflict',conclusion='900 vs 910 unresolved.')
    (root/'review.json').write_text(json.dumps(dict(paper='TEST',pdf_sha256=sha256(b'fake-pdf'),findings=[finding])))
    updated=compare_table(TableCase.model_validate(config['cases'][0]),(out/'example-table-extraction.json').read_bytes(),source,root)
    (out/'example-table-comparison.json').write_text(json.dumps(updated))
    assert preview(batches)['summary']['source_conflicts']==1
    assert import_batch(batches).status_code==200
    doc=batches['client'].get('/api/projects/'+batches['pid']).json()['document']
    assert 'finding-0-0' in doc['reported_values'][0]['evidence_ids']
    assert any('900 vs 910' in e['text'] for e in doc['evidence'])


def test_duplicate_registered_project_id_is_rejected(batches):
    path=batches['root']/'configs/table-workspaces.json'
    registry=json.loads(path.read_text());registry['another-table']=registry['example-table']
    path.write_text(json.dumps(registry))
    assert batches['client'].get('/api/table-batches/example-table').status_code==422
    assert batches['client'].get('/api/projects').json()==[]
