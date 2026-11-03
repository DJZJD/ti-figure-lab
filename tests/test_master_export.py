import copy
import io
import json
import zipfile
from pathlib import Path
from uuid import uuid4
import pytest
from test_api import client
from test_reported import setup_report
from test_plans import assess


@pytest.fixture
def example(client):
    d=setup_report(client)
    master=dict(schema_version='TiFM_master_self_contained_v0.1',paper_short_id='QA',samples=[dict(sample_id='QA')],
        sample_property_value_records=[dict(property_record_id='PROP1',sample_id='QA',property_name='strength',
            value=800,unit='MPa',source_figure_or_table='Synthetic table',unknown_context={'keep':[True,None,3.0]})],
        custom_extension={'must':'survive'})
    raw=json.dumps(master).encode()
    d['reference_id']=client.post('/api/references',files={'file':('fixture.json',raw)}).json()['id']
    d['reported_values'][0]['target_path']='/sample_property_value_records/0/value'
    d['reported_values'][0]['review_basis']=assess(client,d)['reported_values'][0]['review_basis']
    pid=str(uuid4());response=client.put('/api/projects/'+pid,json={'document':d,'expected_revision':0})
    assert response.status_code==200,response.text
    assert response.json()['results']['reported_values'][0]['status']=='reviewed'
    return dict(client=client,pid=pid,raw=raw,master=master)


def preview(e):
    response=e['client'].get('/api/projects/'+e['pid']+'/master-copy-preview')
    assert response.status_code==200,response.text
    return response.json()


def selection(key='reported:r'):
    return dict(key=key,reason='Synthetic test adoption only; retained metadata still applies.',retained_metadata_checked=True)


def export(e,selections=None,p=None):
    return e['client'].post('/api/projects/'+e['pid']+'/master-copy',json=dict(preview_hash=(p or preview(e))['preview_hash'],
        selections=[selection()] if selections is None else selections,reviewer='Automated test fixture; not a scientific reviewer'))


def read_bundle(response):
    assert response.status_code==200,response.text
    with zipfile.ZipFile(response.json()['path']) as archive:
        assert archive.testzip() is None
        return {name:json.loads(archive.read(name)) for name in ('master-copy.json','original-master.json','adoption-audit.json','pending-review.json')}


def test_explicit_adoption_copy_roundtrip_and_original_untouched(example):
    e=example;c=e['client'];p=preview(e)
    assert p['summary']['eligible']==1
    old_project=c.get('/api/projects/'+e['pid']).json()
    result=read_bundle(export(e))
    expected=copy.deepcopy(e['master']);expected['sample_property_value_records'][0]['value']=850
    assert result['master-copy.json']==expected
    assert result['original-master.json']==e['master']
    assert result['adoption-audit.json']['adopted_fields'][0]['old_value']==800
    assert result['adoption-audit.json']['validation']['downstream_reader_tested'] is False
    assert result['pending-review.json']['unadopted']==[]
    import backend.app as app
    assert (app.REFERENCES/(old_project['document']['reference_id']+'.json')).read_bytes()==e['raw']
    assert old_project==c.get('/api/projects/'+e['pid']).json()


def test_no_selection_copies_original_and_keeps_all_pending(example):
    result=read_bundle(export(example,[]))
    assert result['master-copy.json']==example['master']
    assert result['adoption-audit.json']['adopted_fields']==[]
    assert len(result['pending-review.json']['unadopted'])==1


def test_duplicate_selection_rejected(example):
    assert export(example,[selection(),selection()]).status_code==422


@pytest.mark.parametrize('patch',[{'retained_metadata_checked':False},{'reason':'  '},{'key':'reported:missing'}])
def test_adoption_requires_explicit_valid_decision(example,patch):
    item={**selection(),**patch}
    assert export(example,[item]).status_code==422


def update_report(e,changes):
    c=e['client'];url='/api/projects/'+e['pid'];saved=c.get(url).json();d=saved['document']
    d['reported_values'][0].update(changes)
    d['reported_values'][0]['review_basis']=assess(c,d)['reported_values'][0]['review_basis']
    result=c.put(url,json={'document':d,'expected_revision':saved['revision']})
    assert result.status_code==200,result.text


@pytest.mark.parametrize('patch',[{'qualifier':'approx'},{'value_kind':'range','upper_text':'900'},
    {'source_class':'derived'},{'sample_checked':False},{'target_path':None}])
def test_unsupported_or_unreviewed_values_do_not_enter_copy(example,patch):
    update_report(example,patch)
    assert preview(example)['summary']['eligible']==0
    assert export(example).status_code==422
    result=read_bundle(export(example,[]))
    assert result['master-copy.json']==example['master']


def test_changed_project_invalidates_preview(example):
    p=preview(example);update_report(example,{'value_text':'860'})
    assert export(example,p=p).status_code==409


def test_known_source_conflict_blocks_even_reviewed_value(example,monkeypatch):
    import backend.app as app
    findings=[dict(target_path='/sample_property_value_records/0/value',finding='source_body_table_conflict',conclusion='Unresolved 800 vs 850')]
    monkeypatch.setattr(app.master_export,'source_findings',lambda *args:(findings,{'review':'hash'}))
    p=preview(example)
    assert p['summary']['eligible']==0 and p['summary']['known_conflicting_fields']==1
    assert export(example).status_code==422
    result=read_bundle(export(example,[]))
    assert result['master-copy.json']==example['master']
    assert result['pending-review.json']['source_findings']==findings


def test_unit_conversion_and_uncertainty_original_preserved(example):
    update_report(example,{'value_text':'0.85','unit':'GPa','uncertainty_text':'± 0.025 GPa (SD)','raw_value':'0.85 ± 0.025 GPa'})
    result=read_bundle(export(example))
    assert result['master-copy.json']['sample_property_value_records'][0]['value']==850
    assert result['master-copy.json']['sample_property_value_records'][0]['unit']=='MPa'
    adopted=result['adoption-audit.json']['adopted_fields'][0]['source_result']['record']
    assert adopted['uncertainty_text']=='± 0.025 GPa (SD)' and adopted['unit']=='GPa'


def test_reference_corruption_and_cross_origin_blocked(example):
    c=example['client'];p=preview(example)
    assert c.post('/api/projects/'+example['pid']+'/master-copy',json={'preview_hash':p['preview_hash'],'selections':[],'reviewer':'test'},headers={'Origin':'https://other.example'}).status_code==403
    import backend.app as app
    ref=c.get('/api/projects/'+example['pid']).json()['document']['reference_id']
    (app.REFERENCES/(ref+'.json')).write_bytes(b'{}')
    assert c.get('/api/projects/'+example['pid']+'/master-copy-preview').status_code==422


def test_graph_candidate_export_preserves_multiple_sample_binding(client):
    from test_candidates import graph_doc
    from test_references import master
    data=master();raw=json.dumps(data).encode();ref=client.post('/api/references',files={'file':('fixture.json',raw)}).json()
    d=graph_doc().model_dump();d['reference_id']=ref['id'];d['image_id']=client.post('/api/demos/graph').json()['id']
    d['measurements'][0]['review_basis']=assess(client,d)['measurements'][0]['review_basis']
    d['candidates'][0]['review_basis']=assess(client,d)['candidates'][0]['review_basis']
    pid=str(uuid4());saved=client.put('/api/projects/'+pid,json={'document':d,'expected_revision':0})
    assert saved.status_code==200,saved.text
    e=dict(client=client,pid=pid)
    assert preview(e)['summary']['eligible']==1
    result=read_bundle(export(e,[selection('candidate:c')]))
    row=result['master-copy.json']['sample_property_value_records'][0]
    assert row['linked_sample_ids']==['A','B'] and row['value']==pytest.approx(41.39)


def test_two_reviewed_sources_for_one_target_cannot_both_be_adopted(example):
    c=example['client'];url='/api/projects/'+example['pid'];saved=c.get(url).json();d=saved['document']
    second=copy.deepcopy(d['reported_values'][0]);second['id']='r2';d['reported_values'].append(second)
    second['review_basis']=assess(c,d)['reported_values'][1]['review_basis']
    assert c.put(url,json={'document':d,'expected_revision':saved['revision']}).status_code==200
    assert preview(example)['summary']['eligible']==2
    assert export(example,[selection(),selection('reported:r2')]).status_code==422


def test_xrd_peak_update_does_not_change_intensity_or_ids(client):
    from test_candidates import graph_doc
    from test_references import master
    data=master();raw=json.dumps(data).encode();ref=client.post('/api/references',files={'file':('fixture.json',raw)}).json()
    d=graph_doc().model_dump();d['reference_id']=ref['id'];d['image_id']=client.post('/api/demos/graph').json()['id']
    d['measurements'][0]['points'][0]['x']=36.2
    d['candidates'][0].update(metric='x',target_path='/xrd_records/0/peaks/0/two_theta_deg',sample_ids=['A'],definition='Synthetic peak position')
    d['measurements'][0]['review_basis']=assess(client,d)['measurements'][0]['review_basis']
    d['candidates'][0]['review_basis']=assess(client,d)['candidates'][0]['review_basis']
    pid=str(uuid4());assert client.put('/api/projects/'+pid,json={'document':d,'expected_revision':0}).status_code==200
    result=read_bundle(export(dict(client=client,pid=pid),[selection('candidate:c')]))
    expected=copy.deepcopy(data);expected['xrd_records'][0]['peaks'][0]['two_theta_deg']=36.2
    assert result['master-copy.json']==expected
