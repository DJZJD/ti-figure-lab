import copy
import pytest
from backend.graph_mapping import propose,MappingRequest
from backend.models import Document
from backend.references import catalog


def master(times=(6,8,10)):
    return dict(schema_version='TiFM_master_self_contained_v0.1',paper_short_id='TEST',samples=[dict(sample_id=f'S{i}',sample_label='HT1',processing_route=[
        dict(operation_standardized='solution_treatment',temperature_C=880,hold_time_h=1.5),
        dict(operation_standardized='aging',temperature_C=620,hold_time_h=t)]) for i,t in enumerate(times)],
        sample_property_value_records=[dict(property_record_id=f'R{i}',linked_sample_ids=[f'S{i}'],property_name='fracture_toughness',value=40+i,unit='MPa·m^0.5',source_figure_or_table='Table 1') for i in range(len(times))])


def fixture():
    d=Document(image_id='0'*64,graph=dict(x=dict(unit='h'),y=dict(unit='MPa m^1/2')),measurements=[dict(id='point1',kind='point',points=[dict(x=1,y=1)],sample='Solution treatment: 880 °C×1.5 h; Aging temperature: 620 °C')])
    e=dict(measurements=[dict(id='point1',valid=True,values=dict(x=7.999,y=39.14))])
    return d,e,dict(x_axis=dict(title='Aging time'),y_axis=dict(title='KIC')),catalog(master()),MappingRequest(reference_id='1'*64,paper_short_id='TEST',expected_revision=1)


def test_conditions_not_property_values_choose_sample_and_remain_pending():
    args=fixture();report,cs=propose(*args)
    assert report['rows'][0]['sample_ids']==['S1'] and report['rows'][0]['digitized_time_h']==7.999
    assert cs[0].sample_checked is False and cs[0].review_basis is None
    for t in args[3]['targets']:t['value']=999999
    assert propose(*args)[1][0].target_path==cs[0].target_path


@pytest.mark.parametrize('kind',['paper','wrong_axis','wrong_unit','existing_reference','existing_candidates'])
def test_incompatible_context_rejected(kind):
    d,e,p,r,q=fixture()
    if kind=='paper':q.paper_short_id='OTHER'
    elif kind=='wrong_axis':p['x_axis']['title']='Temperature'
    elif kind=='wrong_unit':d.graph.y.unit='MPa'
    elif kind=='existing_reference':d.reference_id='2'*64
    else:d.candidates=propose(d,e,p,r,q)[1]
    with pytest.raises(ValueError):propose(d,e,p,r,q)


def test_ambiguous_route_unparsed_context_missing_target_and_duplicate_points_defer():
    d,e,p,r,q=fixture()
    r=catalog(master((8,8)))
    report,cs=propose(d,e,p,r,q);assert not cs and report['rows'][0]['status']=='ambiguous'
    d.measurements[0].sample='HT1';assert not propose(d,e,p,r,q)[1]
    d,e,p,r,q=fixture();r['targets']=[];assert not propose(d,e,p,r,q)[1]
    d,e,p,r,q=fixture()
    d.measurements.append(d.measurements[0].model_copy(update={'id':'point2'}));e['measurements'].append({**e['measurements'][0],'id':'point2'})
    report,cs=propose(d,e,p,r,q);assert not cs and all(x['status']=='duplicate_target' for x in report['rows'])


def test_conflict_is_retained_and_outside_time_tolerance_unmatched():
    args=fixture();conflict=dict(target_path='/sample_property_value_records/1/value',finding='source_figure_table_conflict')
    report,cs=propose(*args,findings=[conflict]);assert report['rows'][0]['source_conflicts']==[conflict]
    assert not cs[0].source_checked
    args[1]['measurements'][0]['values']['x']=8.04
    assert not propose(*args)[1]
