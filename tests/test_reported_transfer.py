import copy
import pytest
from scripts.check_reported_transfer import compare


def fixture():
    m=dict(schema_version='TiFM_master_self_contained_v0.1',paper_short_id='A',samples=[dict(sample_id='s',sample_label='WQ')],
      sample_property_value_records=[dict(property_record_id='r',sample_id='s',property_name='yield_strength',unit='Mpa',value=822)])
    j=dict(id='test',status='awaiting_review',extraction_scope='mechanical_properties',records=[dict(id='r1',kind='property',quantity='yield_strength',sample_label='WQ',value=822,raw_value='822 ± 1',unit='MPa',conditions='Unverified',checks=['quote missing'],page=3,locator='Table 1',evidence_quote='822',review_status='needs_review')])
    return j,m


def test_agreement_keeps_evidence_flags_and_rejects_other_paper():
    j,m=fixture();r=compare(j,m,'A');assert r['summary']['numeric_agreements']==1 and r['summary']['flagged_rows']==1
    with pytest.raises(ValueError):compare(j,m,'B')
    j['status']='response_failed'
    with pytest.raises(ValueError):compare(j,m,'A')


def test_never_choose_nearest_value_from_ambiguous_extractions():
    j,m=fixture();other=copy.deepcopy(j['records'][0]);other.update(id='r2',value=900);j['records'].append(other)
    assert compare(j,m,'A')['summary']['ambiguous']==1
    j['records']=j['records'][1:]
    assert compare(j,m,'A')['summary']['numeric_differences']==1
    j['records'][0]['sample_label']='OQ'
    assert compare(j,m,'A')['summary']['missing']==1
