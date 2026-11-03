import json
from uuid import uuid4
import pytest
from backend.models import Candidate,Document
from backend.geometry import evaluate
from backend.candidates import candidate_results
from backend.references import catalog
from test_api import client,setup_document
from test_references import master


def graph_doc():
    d=Document(image_id='a'*64,reference_id='b'*64,source='Test Fig.1; synthetic only',
        graph={'x':{'points':[{'x':0,'y':100},{'x':100,'y':100}],'values':[0,100],'unit':'deg'},
               'y':{'points':[{'x':0,'y':100},{'x':0,'y':0}],'values':[0,100],'unit':'MPa*sqrt(m)'},'use_y':True},
        measurements=[{'id':'p','kind':'point','points':[{'x':35.6,'y':58.61}],'sample':'A / B','status':'accepted'}])
    d.measurements[0].review_basis=evaluate(d)['measurements'][0]['review_basis']
    d.candidates=[Candidate(id='c',source_kind='measurement',measurement_id='p',metric='y',
        target_path='/sample_property_value_records/0/value',sample_ids=['A','B'],definition='Fracture toughness at test condition',
        evidence='Synthetic mapping fixture; both samples and source checked',sample_checked=True,definition_checked=True,unit_checked=True,source_checked=True)]
    return d


def result(d,data=None):return candidate_results(d,evaluate(d),catalog(data or master()))[0]


def test_mapping_review_is_distinct_and_source_change_invalidates():
    d=graph_doc();r=result(d)
    assert r['status']=='needs_mapping_review' and not r['blockers']
    assert r['target']['sample_ids']==['A','B']
    assert r['conversion']['value']==pytest.approx(41.39)
    d.candidates[0].review_basis=r['review_basis'];assert result(d)['status']=='mapping_checked'
    d.measurements[0].points[0].y=60
    assert 'Source geometry needs current review.' in result(d)['blockers']
    d.measurements[0].review_basis=evaluate(d)['measurements'][0]['review_basis']
    assert result(d)['status']=='needs_mapping_review'


def test_no_five_percent_acceptance_and_multi_sample_not_truncated():
    d=graph_doc();d.candidates[0].definition_checked=False
    assert result(d)['status']=='blocked' # Equal values do not bypass checks.
    d.candidates[0].definition_checked=True;d.candidates[0].sample_ids=['A']
    assert any('complete target sample set' in s for s in result(d)['blockers'])
    d.candidates[0].sample_ids=['A','A','B']
    assert any('unique existing' in s for s in result(d)['blockers'])


def test_peak_x_only_intensity_and_property_x_rejected():
    d=graph_doc();c=d.candidates[0];c.metric='x';c.sample_ids=['A'];c.target_path='/xrd_records/0/peaks/0/two_theta_deg'
    assert not result(d)['blockers']
    c.target_path='/xrd_records/0/peaks/0/normalized_intensity'
    assert any('normalized intensity' in b for b in result(d)['blockers'])
    c.target_path='/sample_property_value_records/0/value';c.sample_ids=['A','B']
    assert any('graph Y' in b for b in result(d)['blockers'])


def test_units_ranges_and_zero_reference_are_not_silently_coerced():
    d=graph_doc();data=master();data['sample_property_value_records'][0]['value']=0
    assert result(d,data)['comparison']['relative_difference_percent'] is None
    data['sample_property_value_records'][0]['value']='40–45'
    assert result(d,data)['comparison'] is None
    data['sample_property_value_records'][0]['unit']='unknown'
    assert any('Units' in b for b in result(d,data)['blockers'])
    d.graph.y.unit='GPa';data['sample_property_value_records'][0]['unit']='MPa'
    d.measurements[0].review_basis=evaluate(d)['measurements'][0]['review_basis']
    assert result(d,data)['conversion']['factor']==1000


def test_summary_cannot_be_fraction_or_single_object_aggregate(client):
    d=Document.model_validate(setup_document(client));d.source='Synthetic only';d.reference_id='b'*64
    m=d.measurements[0];m.sample='A';m.group_label='QA';m.include_in_summary=True
    m.review_basis=evaluate(d)['measurements'][0]['review_basis']
    d.candidates=[Candidate(id='c',source_kind='summary',sample='A',group='QA',metric='mean_ecd',target_path='/microstructure_quantification_records/0/value',sample_ids=['A'],definition='Mean ECD',evidence='Synthetic',sample_checked=True,definition_checked=True,unit_checked=True,source_checked=True)]
    r=result(d);assert r['value']>0 and any('phase fractions' in b for b in r['blockers'])
    d.candidates[0].source_kind='measurement';d.candidates[0].measurement_id='square';d.candidates[0].metric='ecd'
    assert any('single object' in b for b in result(d)['blockers'])
    d.candidates[0].target_path=None
    assert result(d)['value']>0 and any('unmapped supplement' in b for b in result(d)['blockers'])


def test_summary_membership_invalidates_mapping_checks(client):
    d=Document.model_validate(setup_document(client));d.source='Synthetic only';d.reference_id='b'*64
    m=d.measurements[0];m.sample='A';m.group_label='QA';m.include_in_summary=True;m.review_basis=evaluate(d)['measurements'][0]['review_basis']
    data=master();data['microstructure_quantification_records'][0].update(quantification_target='mean_ecd',unit='µm')
    d.candidates=[Candidate(id='c',source_kind='summary',sample='A',group='QA',metric='mean_ecd',target_path='/microstructure_quantification_records/0/value',sample_ids=['A'],definition='Mean ECD synthetic',evidence='Synthetic',sample_checked=True,definition_checked=True,unit_checked=True,source_checked=True)]
    d.candidates[0].review_basis=result(d,data)['review_basis'];assert result(d,data)['status']=='mapping_checked'
    m.include_in_summary=False;assert result(d,data)['status']=='blocked'


def test_attachment_persistence_includes_blocked_and_master_unchanged(client):
    raw=json.dumps(master()).encode();ref=client.post('/api/references',files={'file':('master.json',raw)}).json()
    d=setup_document(client);d['reference_id']=ref['id'];d['source']='Synthetic source'
    d['candidates']=[{'id':'c','source_kind':'measurement','measurement_id':'square','metric':'area','sample_ids':['A'],'definition':'Single object area'}]
    url='/api/projects/'+str(uuid4());saved=client.put(url,json={'expected_revision':0,'document':d})
    assert saved.status_code==200,saved.text
    attachment=client.get(url+'/export?format=candidates_json').json()
    assert attachment['artifact_type']=='review_supplement_not_TiFM_master'
    assert attachment['candidates'][0]['status']=='blocked'
    assert attachment['candidates'][0]['members'][0]['measurement']['points']==d['measurements'][0]['points']
    assert 'calibration' in attachment and attachment['reference']['id']==ref['id']
    output=client.post(url+'/export-file?format=candidates_json').json()
    assert output['path'].endswith('.review-supplement.json')
    import backend.app as backend
    assert (backend.REFERENCES/(ref['id']+'.json')).read_bytes()==raw
    assert client.get(url).json()['document']['candidates'][0]['id']=='c'


def test_duplicate_candidate_id_rejected(client):
    d=setup_document(client);c={'id':'c','source_kind':'measurement','measurement_id':'square','metric':'area'};d['candidates']=[c,c]
    assert client.post('/api/evaluate',json=d).status_code==422


def test_original_record_context_and_source_hash_are_checked(client):
    data=master();data['samples'][0]['processing_route']=[{'temperature_C':800,'hold_time_h':2}]
    raw=json.dumps(data).encode();ref=client.post('/api/references',files={'file':('master.json',raw)}).json()
    assert ref['targets'][0]['record_context']['test_specimen_context']=='preserve me'
    assert ref['samples'][0]['context']['processing_route'][0]['temperature_C']==800
    import backend.app as backend
    (backend.REFERENCES/(ref['id']+'.json')).write_bytes(raw+b' ')
    assert client.get('/api/references/'+ref['id']).status_code==422
