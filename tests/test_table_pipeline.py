import copy
import json
import sys
from types import SimpleNamespace

import pytest
from backend.table_pipeline import (SourceSpec, TableCase, contained_path, sha256, source_signature,
    parse_source, extract_table, compare_table, compare_records, match_toughness)
from scripts.table_pipeline import load_cases, run_batch

TEXT = 'Samples YS (MPa) UTS (MPa) EL (%) Impact energy (J)\nA1 900 ± 12 1030 ± 15 13 ± 1 28 ± 2'


def case():
    return TableCase.model_validate(dict(id='example-table', source=dict(paper='TEST', pdf='paper.pdf',
        inspected_pdf_sha256=sha256(b'fake-pdf'), table='Table 1', page=1, bbox=[0,.1,1,.5],
        parser='mechanical', expected_rows=1, interpretation_note='Synthetic parser test only.'),
        comparison=dict(master='master.json')))


def master():
    return dict(paper_short_id='TEST', samples=[dict(sample_id='S1',sample_label='A1')],
        sample_property_value_records=[dict(sample_id='S1',property_name='yield_strength',value=900,unit='MPa')])


def artifact(c):
    return dict(schema_version='tifigure-table-extraction/1', paper='TEST',
        source_spec=c.source.model_dump(mode='json'), source_spec_sha256=source_signature(c.source),
        pdf_sha256=c.source.inspected_pdf_sha256, reference_supplied_to_extractor=False,
        raw_table_text=TEXT, **parse_source(c.source,TEXT))


def setup_files(root):
    (root/'paper.pdf').write_bytes(b'fake-pdf')
    (root/'master.json').write_text(json.dumps(master()),encoding='utf-8')


@pytest.mark.parametrize('field,value', [('bbox',[.5,0,.4,1]),('bbox',[0,0,1,1.01]),
    ('bbox',[0,0,float('nan'),1]),('page',0),('page',True),('expected_rows',0),('parser','guess'),
    ('master','answer.json')])
def test_source_config_rejects_invalid_regions_and_reference_fields(field,value):
    data=case().source.model_dump();data[field]=value
    with pytest.raises(ValueError):SourceSpec.model_validate(data)


@pytest.mark.parametrize('path',['../escape.pdf','C:/outside.pdf'])
def test_paths_cannot_escape_source_root(tmp_path,path):
    with pytest.raises(ValueError):contained_path(tmp_path,path)


def test_empty_text_and_wrong_row_count_fail():
    with pytest.raises(ValueError,match='native PDF text'):parse_source(case().source,'')
    spec=case().source.model_copy(update={'expected_rows':2})
    with pytest.raises(ValueError,match='Row count'):parse_source(spec,TEXT)


def test_extract_does_not_open_master(tmp_path,monkeypatch):
    (tmp_path/'paper.pdf').write_bytes(b'fake-pdf')
    class Page:
        width=100; height=200
        def crop(self,box):
            assert box==(0,20,100,100)
            return self
        def extract_text(self):return TEXT
    class PDF:
        pages=[Page()]
        def __enter__(self):return self
        def __exit__(self,*args):pass
    monkeypatch.setitem(sys.modules,'pdfplumber',SimpleNamespace(open=lambda path:PDF(),__version__='test'))
    result=extract_table(case().source,tmp_path)
    assert result['reference_supplied_to_extractor'] is False
    assert len(result['records'])==4
    assert not (tmp_path/'master.json').exists()


def test_compare_preserves_extras_and_pending_uncertainty(tmp_path):
    setup_files(tmp_path);c=case()
    result=compare_table(c,json.dumps(artifact(c)).encode(),tmp_path,tmp_path)
    assert result['summary']==dict(source_values=4,mapped_drafts=1,exact_center_matches=1,
        outside_existing_targets=3,blocked_mappings=0,source_conflicts=0,human_approved=0)
    draft=result['records'][0]['reported_value_draft']
    assert draft['uncertainty_text']=='12' and draft['uncertainty_kind']=='unspecified'
    assert draft['source_class']=='unknown' and draft['review_basis'] is None
    assert not any(draft[k] for k in ('source_checked','sample_checked','quantity_checked','value_checked'))
    assert all(r['reported_value_draft'] is None for r in result['records'][1:])


@pytest.mark.parametrize('change',['source','config','record','paper','provenance'])
def test_stale_or_changed_extraction_rejected(tmp_path,change):
    setup_files(tmp_path);c=case();data=artifact(c)
    if change=='source':(tmp_path/'paper.pdf').write_bytes(b'changed')
    if change=='config':c.source.page=2
    if change=='record':data['records'][0]['value']=901
    if change=='paper':data['paper']='OTHER'
    if change=='provenance':data['reference_supplied_to_extractor']=True
    with pytest.raises(ValueError):compare_table(c,json.dumps(data).encode(),tmp_path,tmp_path)


def review():
    return dict(paper='TEST',pdf_sha256=case().source.inspected_pdf_sha256, findings=[dict(paper='TEST',
        master_sha256='master-hash',sample_ids=['S1'],target_path='/sample_property_value_records/0/value',
        source_value=900,finding='source_body_table_conflict',conclusion='Table 900 vs body 910; unresolved.')])


def test_conflict_survives_exact_match():
    records=compare_records(case(),artifact(case()),master(),[review()],'master-hash')
    assert records[0]['source_conflict'] and records[0]['mapping']['comparison']['exact_match']
    assert 'unresolved' in records[0]['reported_value_draft']['scope_note']
    assert records[0]['reported_value_draft']['review_basis'] is None


@pytest.mark.parametrize('field,value',[('master_sha256','old'),('sample_ids',['wrong']),
    ('target_path','/sample_property_value_records/99/value'),('source_value',999)])
def test_conflict_binding_cannot_be_silently_discarded(field,value):
    evidence=review();evidence['findings'][0][field]=value
    with pytest.raises(ValueError):compare_records(case(),artifact(case()),master(),[evidence],'master-hash')


def test_batch_failure_keeps_success_and_marks_stale_artifact(tmp_path,monkeypatch):
    from scripts import table_pipeline as cli
    first=case();second=case().model_copy(update={'id':'another-table'})
    def extract(spec,root):
        if spec.paper=='BAD':raise ValueError('source mismatch')
        return dict(records=[{}],sample_count=1)
    second.source=second.source.model_copy(update={'paper':'BAD'})
    monkeypatch.setattr(cli,'extract_table',extract)
    out=tmp_path/'out';out.mkdir();old=out/'another-table-extraction.json';old.write_text('previous')
    result=run_batch('extract',[first,second],tmp_path,out)
    assert result['succeeded']==1 and result['failed']==1
    assert 'stale' in result['items'][1]['notice']
    assert old.read_text()=='previous'


def test_duplicate_case_ids_rejected(tmp_path):
    path=tmp_path/'config.json'
    path.write_text(json.dumps(dict(schema_version='tifigure-table-cases/1',cases=[case().model_dump()]*2)))
    with pytest.raises(ValueError,match='unique'):load_cases(path)


def test_toughness_matches_conditions_not_ht_label():
    spec=case().source.model_copy(update={'parser':'heat_treatment_toughness'})
    row=parse_source(spec,'Fracturetoughness Heattreatmentconditions MPa.m1/2\nHT77 875°C×1.25h+615°C×7h,AC 47.81')['records'][0]
    data=dict(paper_short_id='TEST',samples=[dict(sample_id='S1',sample_label='HT2',processing_route=[
        dict(operation_standardized='solution_treatment',temperature_C=875,hold_time_h=1.25),
        dict(operation_standardized='aging',temperature_C=615,hold_time_h=7,cooling=dict(mode='AC'))])],
        sample_property_value_records=[dict(linked_sample_ids=['S1'],property_name='fracture_toughness',value=47.81,unit='Mpa·m^0.5')])
    assert match_toughness(row,'TEST',data)['comparison']['exact_match']
    changed=copy.deepcopy(data);changed['samples'][0]['processing_route'][1]['hold_time_h']=8
    assert match_toughness(row,'TEST',changed)['target_path'] is None
    changed=copy.deepcopy(data);changed['sample_property_value_records'][0]['unit']='J'
    assert match_toughness(row,'TEST',changed)['status']=='unit_mismatch'


def test_new_source_value_is_preserved_instead_of_copied_from_master():
    c=case();data=artifact(c)
    data['records']=parse_source(c.source,TEXT.replace('900 ± 12','891 ± 12'))['records']
    result=compare_records(c,data,master())
    assert result[0]['reported_value_draft']['value_text']=='891'
    assert result[0]['mapping']['comparison']==dict(exact_match=False,difference=-9,
        scope='central_table_value_transcription_only_not_uncertainty_or_scientific_validity')


def test_multiple_source_cells_must_not_silently_share_target():
    c=case();data=artifact(c);data['records'].append(copy.deepcopy(data['records'][0]))
    with pytest.raises(ValueError,match='Multiple source cells'):
        compare_records(c,data,master())
