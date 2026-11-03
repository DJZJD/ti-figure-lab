import copy
import pytest
from backend.microstructure_table import parse_microstructure_table, match_reference

HEADER='Samples Vαp (%) Vβt (%) Dαp (μm) Dαs (nm)'
TEXT=HEADER+'\nSTA777 32.5 67.5 4.7 88.2'


def record():
    return parse_microstructure_table(TEXT)['records'][2]


def master():
    return {'paper_short_id':'P_TEST','samples':[{'sample_id':'P_TEST_A','sample_label':'STA777'}],
            'microstructure_quantification_records':[{'sample_id':'P_TEST_A','quantification_target':'primary_alpha_size','unit':'μm','value':4.7}]}


def test_unseen_values_and_column_reordering():
    expected=parse_microstructure_table(TEXT)
    reordered=parse_microstructure_table('Samples Dαs (nm) Vβt (%) Dαp (µm) Vαp (%)\nSTA777 88.2 67.5 4.7 32.5')
    assert {r['metric']:r['value'] for r in expected['records']}=={r['metric']:r['value'] for r in reordered['records']}
    assert record()['metric']=='primary_alpha_size' and record()['value']==4.7
    assert all(r['n_objects'] is None and r['uncertainty'] is None for r in expected['records'])
    assert parse_microstructure_table(TEXT.replace('4.7','5.9'))['records'][2]['value']==5.9


@pytest.mark.parametrize('text',[
    TEXT.replace('Dαp (μm)','Dαp (nm)'),
    TEXT.replace('Dαs (nm)','Vαp (%)'),
    TEXT+'\nSTA777 32.5 67.5 4.7 88.2',
    TEXT.replace(' 88.2',''),
    TEXT.replace('32.5','101'),
    TEXT.replace('4.7','0'),
    TEXT.replace('4.7','4.7±0.2'),
    TEXT.replace('88.2','1e999'),
    TEXT.replace('Vβt (%)','Vβ (%)'),
])
def test_unsupported_or_incomplete_source_is_rejected(text):
    with pytest.raises(ValueError):parse_microstructure_table(text)


def test_fraction_check_preserves_noncomplementary_values():
    result=parse_microstructure_table(TEXT.replace('67.5','61.5'))
    assert result['warnings'] and result['records'][1]['value']==61.5


def test_mapping_is_paper_scoped_and_does_not_infer_conditions():
    mapped=match_reference(record(),'P_TEST',master())
    assert mapped['comparison']['exact_match'] and mapped['human_review_required']
    assert not mapped['conditions_verified']
    with pytest.raises(ValueError):match_reference(record(),'P_OTHER',master())


def test_ambiguity_unit_and_missing_target():
    m=master();m['samples'].append(copy.deepcopy(m['samples'][0]))
    assert match_reference(record(),'P_TEST',m)['status']=='sample_ambiguous'
    m=master();m['microstructure_quantification_records']*=2
    assert match_reference(record(),'P_TEST',m)['status']=='target_ambiguous'
    m=master();m['microstructure_quantification_records'][0]['unit']='nm'
    assert match_reference(record(),'P_TEST',m)['status']=='unit_mismatch'
    extra=parse_microstructure_table(TEXT)['records'][3]
    assert match_reference(extra,'P_TEST',master())['status']=='outside_existing_targets'
    m=master();m['microstructure_quantification_records'][0]['value']=True
    assert match_reference(record(),'P_TEST',m)['status']=='non_numeric_reference'
