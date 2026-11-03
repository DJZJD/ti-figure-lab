import copy
import pytest
from backend import reported_mapping as rm
from backend.references import catalog


def fixture():
    quote='The samples were heated to 900 °C in an electricity-resistant furnace and held for 1.5 h'
    r=dict(id='record-0001',kind='property',sample_label='WQ',quantity='tensile_strength',
        conditions='Annealed 900 °C/1.5 h, water quenching (~80 °C/s); room-temperature tensile test',
        value=987,unit='MPa',raw_value='987 ± 3',page=3,locator='Table 1',evidence_quote='WQ 987 ± 3',
        condition_page=2,condition_quote=quote,checks=['Original quote flag'],review_status='needs_review',review=None,
        table_evidence={'status':'supported','cell':{'expression':'987 ± 3','uncertainty_kind':'unspecified'}})
    state=dict(paper='TEST-LIVE-01',records=[r],pdf_sha256='0'*64,revision=4)
    master=dict(schema_version='TiFM_master_self_contained_v0.1',paper_short_id='TEST',samples=[dict(sample_id='S1',sample_label='WQ',processing_route=[dict(
        operation_standardized='annealing',temperature_C=900,hold_time_h=1.5,cooling=dict(mode='WQ',cooling_rate_C_per_s=80))])],
        sample_property_value_records=[dict(property_record_id='R1',sample_id='S1',property_name='tensile_strength',value=987,unit='Mpa',source_figure_or_table='Table 1')])
    return state,master,rm.Request(reference_id='1'*64,paper_short_id='TEST',expected_revision=4),[dict(page=2,text=quote)]


def result(state,master,req,pages):return rm.propose(state,catalog(master),req,pages)['rows'][0]


def test_value_independent_mapping_and_preserved_uncertainty():
    s,m,q,p=fixture();before=copy.deepcopy(s);r=result(s,m,q,p)
    assert r['status']=='mapping_needs_review' and r['target_path']=='/sample_property_value_records/0/value'
    assert r['raw_value']=='987 ± 3' and r['original_flags']==['Original quote flag'] and s==before
    m['sample_property_value_records'][0]['value']=111
    r=result(s,m,q,p);assert r['status']=='reference_value_conflict' and r['sample_id']=='S1'
    assert r['difference']==876


@pytest.mark.parametrize('field,value',[('temperature_C',901),('hold_time_h',2),('operation_standardized','solution_treatment')])
def test_processing_mismatch_never_links(field,value):
    s,m,q,p=fixture();m['samples'][0]['processing_route'][0][field]=value
    assert result(s,m,q,p)['status']=='condition_mismatch'


def test_cooling_mismatch_duplicate_samples_and_targets():
    s,m,q,p=fixture();m['samples'][0]['processing_route'][0]['cooling']['mode']='OQ'
    assert result(s,m,q,p)['status']=='condition_mismatch'
    s,m,q,p=fixture();other=copy.deepcopy(m['samples'][0]);other['sample_id']='S2';m['samples'].append(other)
    assert result(s,m,q,p)['status']=='ambiguous_sample'
    s,m,q,p=fixture();other=copy.deepcopy(m['sample_property_value_records'][0]);other['property_record_id']='R2';m['sample_property_value_records'].append(other)
    assert result(s,m,q,p)['status']=='ambiguous_target'


def test_paper_and_source_evidence_guards():
    s,m,q,p=fixture();s['paper']='ANOTHER'
    with pytest.raises(ValueError):result(s,m,q,p)
    s,m,q,p=fixture();m['paper_short_id']='ANOTHER'
    with pytest.raises(ValueError):result(s,m,q,p)
    s,m,q,p=fixture();p[0]['text']='unrelated source'
    assert result(s,m,q,p)['status']=='condition_evidence_unresolved'
    s,m,q,p=fixture();s['records'][0]['condition_quote']=p[0]['text']='The samples were heated to 800 °C in an electricity-resistant furnace and held for 1.5 h'
    assert result(s,m,q,p)['status']=='condition_evidence_unresolved'


def test_unsupported_reviewed_cells_and_duplicate_extractions():
    s,m,q,p=fixture();s['records'][0]['conditions']='Annealed 900 °C/1.5 h, water quenching then aged 600 °C'
    assert result(s,m,q,p)['status']=='unsupported'
    s,m,q,p=fixture();s['records'][0]['review_status']='accepted'
    assert result(s,m,q,p)['status']=='reviewed_record_deferred'
    s,m,q,p=fixture();s['records'][0]['table_evidence']['status']='conflict'
    assert result(s,m,q,p)['status']=='cell_evidence_unresolved'
    s,m,q,p=fixture();other=copy.deepcopy(s['records'][0]);other['id']='record-0002';s['records'].append(other)
    assert all(r['status']=='duplicate_target' for r in rm.propose(s,catalog(m),q,p)['rows'])


def test_unit_conversion_and_wrong_dimension():
    s,m,q,p=fixture();s['records'][0].update(value=.987,unit='GPa')
    assert result(s,m,q,p)['status']=='mapping_needs_review'
    s['records'][0]['unit']='J'
    assert result(s,m,q,p)['status']=='unit_or_value_unresolved'


def test_additional_process_after_semicolon_is_not_ignored():
    s,m,q,p=fixture();s['records'][0]['conditions']='Annealed 900 °C/1.5 h, water quenching; aged 600 °C'
    assert result(s,m,q,p)['status']=='unsupported'


def test_quantity_case_spacing_not_physical_quantity_equivalence():
    s,m,q,p=fixture();s['records'][0].update(quantity='impact_toughness',unit='J')
    m['sample_property_value_records'][0].update(property_name='Impact toughness',unit='J')
    assert result(s,m,q,p)['status']=='mapping_needs_review'
    m['sample_property_value_records'][0]['property_name']='fracture_toughness'
    assert result(s,m,q,p)['status']=='missing_target'


def test_api_archives_preview_and_invalidates_stale_mapping(tmp_path,monkeypatch):
    import importlib,sys,json,io,zipfile
    from fastapi.testclient import TestClient
    from backend import paper_jobs as pj
    monkeypatch.setenv('TIFIGURE_DATA',str(tmp_path/'data'))
    sys.modules.pop('backend.app',None);app=importlib.import_module('backend.app')
    s,m,q,p=fixture();ident='a'*32;pdf=b'%PDF-fixture'
    s.update(id=ident,status='awaiting_review',pdf_sha256=pj.digest(pdf),deferred_items=[],limitations=[])
    s['records'][0].update(notes='original note',definition='Ultimate tensile strength')
    folder=app.PAPER_JOBS/ident;(folder/'source').mkdir(parents=True);(folder/'outputs').mkdir()
    (folder/'source.pdf').write_bytes(pdf);pj.write_json(folder/'source/pages.json',p);pj.write_json(folder/'job.json',s)
    raw=json.dumps(m).encode();before=copy.deepcopy(s['records'])
    with TestClient(app.app,headers={'X-TiFigure-Client':'workbench'}) as c:
        reference=c.post('/api/references',files={'file':('master.json',raw,'application/json')}).json()
        body=dict(reference_id=reference['id'],paper_short_id='TEST',expected_revision=4);base=f'/api/paper-jobs/{ident}'
        response=c.post(base+'/reported-mapping',json=body);assert response.status_code==200,response.text
        state=response.json();assert state['records']==before and state['revision']==5
        assert c.post(base+'/reported-mapping',json=body).status_code==422
        preview=c.get(base+'/reported-mapping/preview');assert preview.status_code==200
        assert preview.json()['applicable_patch']==[] and preview.json()['eligible_fields']==0
        with zipfile.ZipFile(io.BytesIO(c.get(base+'/archive').content)) as z:
            assert z.read('reported-mapping/revision-000005/reference-master.json')==raw
            assert 'reported-mapping/revision-000005/pending-field-preview.json' in z.namelist()
            assert all(pj.digest(z.read(f['path']))==f['sha256'] for f in json.loads(z.read('archive-manifest.json'))['files'])
        state['revision']+=1;pj.write_json(folder/'job.json',state)
        assert c.get(base+'/reported-mapping/preview').status_code==409
        body['expected_revision']=6
        assert c.post(base+'/reported-mapping',json=body).status_code==200
        assert (folder/'reported-mapping/revision-000005/mapping.json').is_file()
        assert (folder/'reported-mapping/revision-000007/mapping.json').is_file()
        (folder/'source.pdf').write_bytes(b'changed');body['expected_revision']=7
        assert c.post(base+'/reported-mapping',json=body).status_code==422
