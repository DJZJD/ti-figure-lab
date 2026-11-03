import pytest
from backend.table_extract import parse_heat_treatment_table, match_sample

HEADER='Fracturetoughness Heattreatmentconditions MPa.m1/2\n'
ROW='HT77 875(cid:2)C(cid:4)1.25hþ615(cid:2)C(cid:4)7h,AC 47.81'


def test_source_numbers_are_not_expected_answers():
    row=parse_heat_treatment_table(HEADER+ROW)[0]
    assert (row['solution_temperature_C'],row['solution_time_h'],row['aging_temperature_C'],row['aging_time_h'],row['value'])==(875,1.25,615,7,47.81)
    assert row['solution_cooling'] is None
    assert parse_heat_treatment_table(HEADER+ROW.replace('47.81','12.34'))[0]['value']==12.34


@pytest.mark.parametrize('text',[HEADER+ROW+' extra',HEADER+ROW+'\n'+ROW,HEADER+ROW.replace('47.81','?'),HEADER.replace('MPa.m1/2','J')+ROW])
def test_bad_rows_and_unsupported_units_fail_closed(text):
    with pytest.raises(ValueError):parse_heat_treatment_table(text)


def test_match_conditions_not_ht_label_and_reject_ambiguity():
    row=parse_heat_treatment_table(HEADER+ROW)[0]
    sample={'sample_id':'PAPER_A_SAMPLE','sample_label':'unrelated label','processing_route':[
        {'operation_standardized':'solution_treatment','temperature_C':875,'hold_time_h':1.25},
        {'operation_standardized':'aging','temperature_C':615,'hold_time_h':7,'cooling':{'mode':'AC'}}]}
    assert match_sample(row,[sample])['status']=='unique_reported_conditions_match'
    assert match_sample(row,[sample,sample])['status']=='ambiguous'
    assert match_sample({**row,'final_cooling':'WQ'},[sample])['status']=='unmatched'
    assert match_sample({**row,'aging_time_h':8},[sample])['status']=='unmatched'
