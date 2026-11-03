import copy
import json
from pathlib import Path
from uuid import uuid4
import pytest
from test_api import client
from test_plans import setup_plan, assess, review_plan


def setup_report(client):
    d=setup_plan(client);d['plans']=[];d['spatial']['points']=[]
    d['evidence'][0].update(kind='table',locator='Synthetic test table, row QA',text='QA: strength 850 ± 25 MPa (SD), n=3 specimens, room temperature. Synthetic test only.')
    d['evidence'][0]['review_basis']=assess(client,d)['planning']['evidence'][0]['review_basis']
    d['reported_values']=[{'id':'r','quantity':'Strength','definition':'Synthetic tensile strength; test fixture only',
        'raw_value':'850 ± 25 MPa','value_text':'850','unit':'MPa','uncertainty_kind':'sd','uncertainty_text':'± 25 MPa (SD)',
        'n_text':'3','scope_note':'Three specimens, room temperature; synthetic only','source_class':'experimental',
        'sample_ids':['QA'],'evidence_ids':['e'],'source_checked':True,'sample_checked':True,'quantity_checked':True,'value_checked':True}]
    return d


def row(client,d):return assess(client,d)['reported_values'][0]


def test_report_without_calibration_roundtrip_and_export(client):
    d=setup_report(client);r=row(client,d)
    assert r['status']=='needs_review' and not r['blockers']
    assert r['parsed_value']=={'value':850,'qualifier':'eq'}
    d['reported_values'][0]['review_basis']=r['review_basis']
    url='/api/projects/'+str(uuid4())
    saved=client.put(url,json={'document':d,'expected_revision':0});assert saved.status_code==200,saved.text
    assert client.get(url).json()['results']['reported_values'][0]['status']=='reviewed'
    packet=client.get(url+'/export?format=reported_json').json()
    assert packet['artifact_type']=='reported_value_attachment_not_TiFM_master'
    record=packet['records'][0];assert record['record']['uncertainty_text']=='± 25 MPa (SD)'
    assert record['evidence'][0]['locator']=='Synthetic test table, row QA'
    assert record['sample_context'][0]['sample_id']=='QA'
    path=client.post(url+'/export-file?format=reported_json').json()['path']
    assert path.endswith('.reported-values.json') and json.loads(Path(path).read_text('utf-8'))==packet


def test_review_invalidates_on_evidence_sample_context_or_record_change(client):
    d=setup_report(client);d['reported_values'][0]['review_basis']=row(client,d)['review_basis']
    changed=copy.deepcopy(d);changed['evidence'][0]['text']+=' Correction.'
    assert row(client,changed)['status']=='blocked'
    changed['evidence'][0]['review_basis']=assess(client,changed)['planning']['evidence'][0]['review_basis']
    assert row(client,changed)['status']=='needs_review'
    for key,value in [('value_text','851'),('scope_note','Different condition'),('uncertainty_text','± 26 MPa (SD)')]:
        changed=copy.deepcopy(d);changed['reported_values'][0][key]=value
        assert row(client,changed)['status']=='needs_review'
    d['spatial']['length']=20
    assert row(client,d)['status']=='reviewed' # Pixel calibration is irrelevant to a transcription.


@pytest.mark.parametrize('value',['NaN','Infinity','1e999','1,000','5 MPa','', '1+2'])
def test_malformed_or_nonfinite_numbers_block_review(client,value):
    d=setup_report(client);d['reported_values'][0]['value_text']=value
    assert row(client,d)['status']=='blocked'


def test_ranges_bounds_text_and_unknown_uncertainty_are_preserved(client):
    d=setup_report(client);v=d['reported_values'][0]
    v.update(value_kind='range',value_text='0.2',upper_text='0.3',qualifier='approx',raw_value='approximately 0.2–0.3 µm',unit='µm',uncertainty_kind='none',uncertainty_text='',n_text='')
    r=row(client,d);assert not r['blockers'] and r['parsed_value']=={'lower':0.2,'upper':0.3,'qualifier':'approx'}
    v['upper_text']='0.1';assert row(client,d)['status']=='blocked'
    v.update(value_kind='scalar',upper_text='',qualifier='ge',value_text='200',raw_value='≥200',uncertainty_kind='unspecified',uncertainty_text='± value; meaning not given')
    r=row(client,d);assert not r['blockers'] and r['parsed_value']['qualifier']=='ge' and r['comparison'] is None
    assert any('not assumed' in w for w in r['warnings'])
    v.update(value_kind='text',qualifier='eq',value_text='',unit='',unit_status='not_reported',raw_value='Not resolved')
    assert row(client,d)['parsed_value'] is None and not row(client,d)['blockers']


def test_missing_context_and_conflicting_fields_block(client):
    d=setup_report(client)
    for patch in [{'sample_ids':['bad']},{'sample_ids':['QA','QA']},{'evidence_ids':['missing']},{'source_class':'unknown'},
                  {'definition':''},{'scope_note':''},{'value_checked':False},{'n_text':'0'},{'n_text':'2.5'},
                  {'uncertainty_text':''},{'unit':''},{'unit_status':'dimensionless'},{'upper_text':'99'}]:
        x=copy.deepcopy(d);x['reported_values'][0].update(patch);assert row(client,x)['status']=='blocked',patch
    d['evidence'][0]['kind']='user_note'
    d['evidence'][0]['review_basis']=assess(client,d)['planning']['evidence'][0]['review_basis']
    assert any('Personal notes' in e for e in row(client,d)['blockers'])


def test_optional_mapping_units_and_no_threshold(client):
    d=setup_report(client)
    master={'schema_version':'TiFM_master_self_contained_v0.1','paper_short_id':'QA','samples':[{'sample_id':'QA'}],
            'sample_property_value_records':[{'property_record_id':'PR','linked_sample_ids':['QA'],'property_name':'strength','value':850,'unit':'MPa'}]}
    raw=json.dumps(master).encode();ref=client.post('/api/references',files={'file':('qa.json',raw)}).json()
    assert ref['targets'],ref
    d['reference_id']=ref['id'];v=d['reported_values'][0];v.update(target_path=ref['targets'][0]['path'],value_text='0.85',unit='GPa')
    r=row(client,d);assert not r['blockers'] and r['comparison']['signed_difference']==0 and r['status']=='needs_review'
    v['qualifier']='gt';assert row(client,d)['comparison'] is None
    v.update(qualifier='eq',unit='µm');assert any('units' in e for e in row(client,d)['blockers'])
    v.update(target_path=None);assert not row(client,d)['blockers']
    import backend.app as app
    assert (app.REFERENCES/(ref['id']+'.json')).read_bytes()==raw


def test_transcription_plan_does_not_require_image_quality_or_calibration(client):
    d=setup_plan(client);d['plans'][0].update(task='reported_value',tool='transcription',image_quality='not_measurable',quality_note='')
    d['spatial']['points']=[];review_plan(client,d)
    p=assess(client,d)['planning']['plans'][0];assert p['can_start'] and p['required_calibration']==[]
    d['reported_values']=setup_report(client)['reported_values'];d['reported_values'][0]['plan_id']='p'
    assert not row(client,d)['blockers']
    d['measurements'][0].update(plan_id='p',sample='QA')
    assert not assess(client,d)['measurements'][0]['valid']
    d['plans'][0]['definition']+=' Changed.';assert row(client,d)['status']=='blocked'


def test_duplicate_record_ids_rejected_and_nonexperimental_origin_retained(client):
    d=setup_report(client);d['reported_values'][0]['source_class']='cited'
    r=row(client,d);assert not r['blockers'] and r['warnings'] and r['record']['source_class']=='cited'
    d['reported_values'].append(d['reported_values'][0]);assert client.post('/api/evaluate',json=d).status_code==422
