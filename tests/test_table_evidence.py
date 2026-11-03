import copy
import pytest
from backend import table_evidence as te


def layout(shift=0,reverse=False):
    keys=list(te.HEADERS)
    if reverse:keys.reverse()
    headers=['Cooling\nmedium',*keys];boxes=[(shift+i*100,10,shift+(i+1)*100,20) for i in range(5)]
    words=[]
    def word(text,x,y):return dict(text=text,x0=shift+x,x1=shift+x+max(8,len(text)*4),top=y,bottom=y+6)
    for ri,label in enumerate(('A1','B2')):
        y=30+ri*22;words.append(word(label,5,y))
        for i,key in enumerate(keys,1):
            words.extend([word(str(100+ri*10+list(te.HEADERS).index(key)),i*100+5,y),word('±',i*100+30,y-1.5),word('2.5',i*100+5,y+9)])
    return words,headers,boxes,(shift,10,shift+500,70),3


@pytest.mark.parametrize('shift,reverse',[(0,False),(125,False),(0,True)])
def test_spatial_cells_follow_headers_rows_and_continuation_lines(shift,reverse):
    cells=te.parse_table(*layout(shift,reverse))
    assert len(cells)==8
    row=next(r for r in cells if r['sample_label']=='B2' and r['quantity']=='yield_strength')
    assert row['value']==111 and row['uncertainty']==2.5
    assert row['expression']=='111 ± 2.5' and len(row['word_boxes'])==3


@pytest.mark.parametrize('corruption',['duplicate_sample','unrecognized_header','footnote','boundary'])
def test_ambiguous_or_unsupported_geometry_is_not_guessed(corruption):
    args=list(layout());words=args[0]
    if corruption=='duplicate_sample':next(w for w in words if w['text']=='B2')['text']='A1'
    elif corruption=='unrecognized_header':args[1][1]='Unknown'
    elif corruption=='footnote':next(w for w in words if w['text']=='100')['text']='100*'
    else:next(w for w in words if w['text']=='100')['x1']=205
    with pytest.raises(ValueError):te.parse_table(*args)


def test_cell_selection_never_uses_expected_value_and_retains_conflicts():
    cells=te.parse_table(*layout());r=dict(locator='Table 3, A1 row',sample_label='A1',quantity='tensile_strength',unit='MPa',value=100,raw_value='100 ± 2.5')
    assert te.match(r,cells)['status']=='supported'
    r['value']=110;r['raw_value']='110 ± 2.5'
    result=te.match(r,cells);assert result['status']=='conflict' and result['cell']['sample_label']=='A1'
    r['value']=100;r['raw_value']='100 ± 9'
    assert te.match(r,cells)['status']=='conflict'
    r['raw_value']='100 ± 2.5';r['unit']='J'
    assert te.match(r,cells)['status']=='conflict'
    r['unit']='MPa'
    assert te.match(r,cells+copy.deepcopy(cells))['status']=='ambiguous'
    r['locator']='Figure 3'
    assert te.match(r,cells)['status']=='unresolved'
