# Synthetic parser fixtures; not measurements from a research paper.
import copy
import pytest
from backend.mechanical_table import parse_mechanical_table, match_reference

TABLE = '''Table 3
Samples YS (MPa) UST (MPa) EL (%) Impact energy (J)
TEST_A 812 ± 15 970 ± 21 11.4 ± 1.1 23 ± 3
TEST_B 803 ± 22 960 ± 30 12.4 ± 0.9 29 ± 2
TEST_C 790 ± 26 940 ± 25 14.6 ± 0.6 27 ± 3'''


def parse(text=TABLE):
    return parse_mechanical_table(text, confirmed_header_aliases={'UST':'UTS'})


def master():
    return dict(paper_short_id='SYNTHETIC_A', samples=[dict(sample_id='S1', sample_label='TEST_A')],
        sample_property_value_records=[dict(sample_id='S1', property_name='yield_strength', value=812, unit='Mpa')])


def test_preserves_uncertainty_without_statistical_inference():
    parsed = parse()
    assert len(parsed['records']) == 12
    assert parsed['sample_count'] == 3
    row = parsed['records'][5]
    assert (row['value_text'], row['uncertainty_text']) == ('960','30')
    assert row['raw_cell'] == '960 ± 30'
    assert row['source_symbol'] == 'UST'
    assert row['uncertainty_kind'] == 'unspecified'
    assert row['n_text'] == ''
    assert parsed['warnings']


def test_typo_not_automatically_assumed():
    with pytest.raises(ValueError):
        parse_mechanical_table(TABLE)


def test_column_reordering_tracks_quantity():
    data = parse('Samples Impact energy (J) EL (%) UTS (MPa) YS (MPa)\nA1 29 ± 2 12.4 ± 0.9 960 ± 30 803 ± 22')
    assert [(r['metric'],r['value']) for r in data['records']] == [
        ('impact_energy',29), ('elongation',12.4), ('tensile_strength',960), ('yield_strength',803)]


def test_absent_uncertainty_not_zero_or_sd():
    row = parse(TABLE.replace('812 ± 15','812'))['records'][0]
    assert row['uncertainty_text'] == '' and row['uncertainty_kind'] == 'none'


@pytest.mark.parametrize('text', [TABLE.replace('YS (MPa)','YS (GPa)'),
    TABLE.replace('EL (%)','YS (MPa)'), TABLE.replace('812 ± 15','-812 ± 15'),
    TABLE.replace('11.4 ± 1.1','120.1 ± 1.1'), TABLE.replace('812 ± 15','812 ± -15'),
    TABLE.replace('23 ± 3','23 ±'), TABLE+'\nTEST_A 1 2 3 4', TABLE+'\ntrailing text'])
def test_rejects_unsupported_input(text):
    with pytest.raises(ValueError):
        parse(text)


def test_mapping_is_paper_scoped_and_does_not_verify_conditions():
    row = parse()['records'][0]
    mapped = match_reference(row,'SYNTHETIC_A',master())
    assert mapped['target_path'] == '/sample_property_value_records/0/value'
    assert mapped['comparison']['exact_match']
    assert mapped['conditions_verified'] is False
    assert mapped['human_review_required'] is True
    with pytest.raises(ValueError):
        match_reference(row,'SYNTHETIC_B',master())


def test_ambiguous_samples_or_targets_block_mapping():
    row = parse()['records'][0]; data=master()
    data['samples'].append(copy.deepcopy(data['samples'][0]))
    assert match_reference(row,'SYNTHETIC_A',data)['status'] == 'sample_ambiguous'
    data=master(); data['sample_property_value_records'] *= 2
    assert match_reference(row,'SYNTHETIC_A',data)['status'] == 'target_ambiguous'


def test_unit_mismatch_and_non_numeric_reference_block_comparison():
    row = parse()['records'][0]; data=master()
    data['sample_property_value_records'][0]['unit']='GPa'
    assert match_reference(row,'SYNTHETIC_A',data)['comparison'] is None
    data=master(); data['sample_property_value_records'][0]['value']=True
    assert match_reference(row,'SYNTHETIC_A',data)['status'] == 'non_numeric_reference'


def test_legacy_impact_alias_requires_opt_in_and_same_unit():
    row=parse()['records'][3]; data=master()
    data['sample_property_value_records']=[dict(sample_id='S1',property_name='impact toughness',value=23,unit='J')]
    assert match_reference(row,'SYNTHETIC_A',data)['target_path'] is None
    result=match_reference(row,'SYNTHETIC_A',data,allow_legacy_impact_label=True)
    assert result['warnings'] and result['comparison']['exact_match']
    data['sample_property_value_records'][0]['unit']='MPa m^1/2'
    assert match_reference(row,'SYNTHETIC_A',data,allow_legacy_impact_label=True)['status']=='unit_mismatch'
