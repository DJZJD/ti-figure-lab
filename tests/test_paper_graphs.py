import asyncio
import copy
import importlib
import io
import json
import sys
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from backend import paper_jobs as pj, paper_graphs as pg
from backend.geometry import evaluate


QUOTE='Fig. 1 shows the measured property as a function of aging time for alloy A.'


def plot():
    return dict(plot_bounds=dict(left=100,top=100,right=900,bottom=900),
        x_axis=dict(title='Aging time',unit='h',ticks=[dict(x=100,y=900,value=0),dict(x=900,y=900,value=100),dict(x=500,y=900,value=50)]),
        y_axis=dict(title='Property',unit='MPa',ticks=[dict(x=100,y=900,value=0),dict(x=100,y=100,value=100),dict(x=100,y=500,value=50)]),
        markers=[dict(x=340,y=420,sample_label='A',curve_label='A',binding_evidence='Fixture legend'),
                 dict(x=500,y=580,sample_label='A',curve_label='A',binding_evidence='Fixture legend')])


def selection():return dict(targets=[dict(page=1,locator='Fig. 1',quantity='property',evidence=[dict(page=1,role='caption',quote=QUOTE)],reason='Requested graph')],deferred=[])
def located():return dict(status='located',reason='Fixture only',bbox=dict(left=50,top=50,right=950,bottom=950))
def measured():return dict(status='measured',reason='Offline fixture, not a model result',plot=plot(),warnings=['Fixture only'])
def envelope(data):return dict(choices=[dict(finish_reason='stop',message=dict(content=json.dumps(data)))],usage={'total_tokens':1})


def test_cross_page_evidence_keeps_sources_separate():
    t=selection()['targets'][0]
    t['evidence'].append(dict(page=2,role='conditions',quote='Solution treatment at 880 C.'))
    pages=[{'text':QUOTE},{'text':'Solution treatment at 880 C.'}]
    checked,rejected=pg.validate_evidence(pg.Target.model_validate(t),pages)
    assert rejected==[]
    assert [e['page'] for e in checked]==[1,2]
    t['evidence'][0]['quote']+=' ... Solution treatment at 880 C.'
    with pytest.raises(ValueError,match='contiguous'):
        pg.validate_evidence(pg.Target.model_validate(t),pages)


def test_caption_must_be_contiguous_within_one_supplied_reading():
    target=pg.Target.model_validate(selection()['targets'][0])
    page=dict(text='Unrelated spatial ordering',alternate_streams=[dict(name='pdf_text_flow',text=QUOTE,words=[])])
    assert pg.validate_evidence(target,[page])[1]==[]
    page['alternate_streams'][0]['text']=QUOTE[:20]
    page['text']=QUOTE[20:]
    with pytest.raises(ValueError):pg.validate_evidence(target,[page])


@pytest.mark.parametrize('bad_pages',[[0],[1,1],[2],[1,3]])
def test_figure_page_references_rejected(bad_pages):
    t=selection()['targets'][0];t['figure_pages']=bad_pages
    with pytest.raises(ValueError):pg.validate_evidence(pg.Target.model_validate(t),[{'text':QUOTE},{'text':'Other page'}])


def test_split_figure_is_archived_and_deferred_without_image_calls(graph_setup):
    state=create(graph_setup);folder=pj.job_path(graph_setup,state['id'])
    pages=pj.read_json(folder/'source/pages.json');pages.append(dict(pages[0],page=2))
    pj.write_json(folder/'source/pages.json',pages)
    target=selection();target['targets'][0]['figure_pages']=[1,2];calls=[]
    async def send(body):calls.append(body);return envelope(target)
    pg.begin(graph_setup,state['id'],pg.Run(expected_revision=1,objective='Extract graph'), '# Rules')
    asyncio.run(pg.execute(graph_setup,state['id'],send,'# Rules'))
    graph=pj.snapshot(graph_setup,state['id'])['graph_run']
    assert len(calls)==1 and graph['status']=='deferred'
    assert graph['panels'][0]['figure_pages']==[1,2]
    assert graph['panels'][0]['validated_evidence'][0]['quote']==QUOTE


@pytest.fixture
def graph_setup(tmp_path,monkeypatch):
    def fake_parse(raw,folder):
        Image.new('RGB',(1000,1000),'white').save(folder/'pages/page-001.png')
        return [dict(page=1,text=QUOTE,words=[],width=1000,height=1000,image_width=1000,image_height=1000,image='pages/page-001.png',native_text_usable=True)]
    def fake_crop(folder,page,box,dest):
        img=Image.new('RGB',(1000,1000),'white');d=ImageDraw.Draw(img);d.rectangle((100,100,900,900),outline='black',width=2)
        d.line((340,420,500,580),fill='red',width=2)
        for x,y in [(340,420),(500,580)]:d.ellipse((x-8,y-8,x+8,y+8),outline='red',width=2)
        img.save(dest)
        return dict(page=1,page_render_width=1200,page_render_height=1200,crop_box_pixels=[100,100,1100,1100],
            crop_width=1000,crop_height=1000,pdf_page_width=600,pdf_page_height=600,render_scale=2,crop_sha256=pj.digest(dest.read_bytes()))
    monkeypatch.setattr(pj,'parse_pdf',fake_parse);monkeypatch.setattr(pg,'crop_pdf',fake_crop)
    return tmp_path/'jobs'


def create(root):return pj.create(root,b'%PDF-1.4\nOFFLINE FIXTURE','fixture.pdf','DEMO','# Rules')


def test_geometry_computes_values_not_model_numbers_and_roundtrip():
    cal,rows,checks=pg.convert(pg.Plot.model_validate(plot()),1000,1000)
    assert rows[0]['x']==pytest.approx(30);assert rows[0]['y']==pytest.approx(60)
    assert rows[1]['x']==pytest.approx(50);assert rows[1]['y']==pytest.approx(40)
    assert checks['x']['pixel_residual']==0
    result=dict(plot=plot(),selection={'locator':'Fig.1'},transform={'crop_width':1000,'crop_height':1000})
    doc=pg.workspace(result,'a'*64,'DEMO','Fixture')
    assert all(m.acquisition=='model_vision' and m.status=='needs_review' for m in doc.measurements)
    values=evaluate(doc)['measurements']
    assert values[0]['values']==dict(x=30,y=60)
    doc.measurements[0].points[0].x+=8
    assert evaluate(doc)['measurements'][0]['values']['x']==pytest.approx(31)


def test_crop_margin_is_relative_and_clamped():
    b=pg.padded_box(pg.Box(left=100,top=200,right=600,bottom=700))
    assert b.model_dump()==dict(left=10,top=110,right=690,bottom=790)
    b=pg.padded_box(pg.Box(left=0,top=0,right=1000,bottom=1000))
    assert b.model_dump()==dict(left=0,top=0,right=1000,bottom=1000)


def test_tick_order_does_not_change_geometry():
    p=plot()
    expected=pg.convert(pg.Plot.model_validate(p),1000,1000)[1]
    for axis in ['x_axis','y_axis']:
        p[axis]['ticks']=[p[axis]['ticks'][2],p[axis]['ticks'][0],p[axis]['ticks'][1]]
    assert pg.convert(pg.Plot.model_validate(p),1000,1000)[1]==expected


@pytest.mark.parametrize('change',[
    lambda p:p['x_axis']['ticks'][2].update(value=65),
    lambda p:p['x_axis']['ticks'][2].update(x=950),
    lambda p:p['x_axis']['ticks'][0].update(y=600),
    lambda p:p['markers'][0].update(x=990),
    lambda p:p['markers'].append(copy.deepcopy(p['markers'][0])),
])
def test_inconsistent_geometry_is_rejected(change):
    p=plot();change(p)
    with pytest.raises(ValueError):pg.convert(pg.Plot.model_validate(p),1000,1000)


def test_bounded_three_stage_requests_archive_and_no_answer_leak(graph_setup):
    state=create(graph_setup);calls=[]
    async def send(body):
        calls.append(body)
        return envelope([selection(),located(),measured()][len(calls)-1])
    pg.begin(graph_setup,state['id'],pg.Run(expected_revision=1,objective='Extract property graph',max_panels=1),'# Graph rules')
    asyncio.run(pg.execute(graph_setup,state['id'],send,'# Graph rules'))
    job=pj.snapshot(graph_setup,state['id']);graph=job['graph_run']
    assert graph['status']=='awaiting_review',graph
    assert graph['calls_attempted']==3
    assert len(calls[0]['messages'][1]['content'])==1
    assert calls[1]['messages'][1]['content'][1]['type']=='image_url'
    assert calls[2]['messages'][1]['content'][1]['image_url']['detail']=='high'
    assert 'reference' not in json.dumps(calls[0]['messages'][1])
    result=graph['panels'][0]['result']
    assert result['points'][0]['pdf_page_point']==dict(x=220,y=260)
    assert result['scientifically_approved'] is False
    with zipfile.ZipFile(io.BytesIO(pj.bundle(graph_setup,state['id']))) as archive:
        prefix=f'graphs/{graph["id"]}/'
        assert prefix+'01-select/input.json' in archive.namelist()
        assert prefix+'panel-001-measure/response.json' in archive.namelist()
        assert prefix+'panel-001/overlay.png' in archive.namelist()
    with pytest.raises(ValueError,match='already attempted'):
        pg.begin(graph_setup,state['id'],pg.Run(expected_revision=job['revision'],objective='Retry'), '# rules')


def test_bad_selection_stops_before_any_image_call(graph_setup):
    state=create(graph_setup);calls=[];bad=selection();bad['targets'][0]['evidence'][0]['quote']='Hallucinated quote'
    async def send(body):calls.append(body);return envelope(bad)
    pg.begin(graph_setup,state['id'],pg.Run(expected_revision=1,objective='Extract graph'),'rules')
    asyncio.run(pg.execute(graph_setup,state['id'],send,'rules'))
    graph=pj.snapshot(graph_setup,state['id'])['graph_run']
    assert graph['status']=='deferred' and len(calls)==1
    assert 'quote not found' in graph['panels'][0]['error']


def test_provider_failure_archived_without_retry(graph_setup):
    state=create(graph_setup);calls=[]
    async def send(body):calls.append(body);raise ValueError('Provider rejected request')
    pg.begin(graph_setup,state['id'],pg.Run(expected_revision=1,objective='Extract graph'),'rules')
    asyncio.run(pg.execute(graph_setup,state['id'],send,'rules'))
    graph=pj.snapshot(graph_setup,state['id'])['graph_run']
    assert graph['status']=='failed' and len(calls)==1
    assert (pj.job_path(graph_setup,state['id'])/'graphs'/graph['id']/'01-select/error.json').is_file()


def test_deferred_crop_and_incomplete_response_never_create_points(graph_setup):
    state=create(graph_setup);calls=[]
    async def send(body):
        calls.append(body)
        return envelope(selection() if len(calls)==1 else dict(status='deferred',reason='Unreadable figure',bbox=None))
    pg.begin(graph_setup,state['id'],pg.Run(expected_revision=1,objective='Extract graph'),'rules')
    asyncio.run(pg.execute(graph_setup,state['id'],send,'rules'))
    graph=pj.snapshot(graph_setup,state['id'])['graph_run']
    assert len(calls)==2 and graph['status']=='deferred'
    assert 'result' not in graph['panels'][0]


def test_api_open_preserves_edits_and_archive_includes_review_revisions(graph_setup,tmp_path,monkeypatch):
    monkeypatch.setenv('TIFIGURE_DATA',str(tmp_path/'api'))
    sys.modules.pop('backend.app',None);mod=importlib.import_module('backend.app');calls=[]
    async def fake_send(body):calls.append(body);return envelope([selection(),located(),measured()][len(calls)-1])
    monkeypatch.setattr(mod.deepseek,'send',fake_send);mod.deepseek.configure('synthetic-test-key')
    with TestClient(mod.app,headers={'X-TiFigure-Client':'workbench'}) as c:
        state=c.post('/api/paper-jobs',files={'file':('fixture.pdf',b'%PDF-1.4\nfixture')},data={'paper':'DEMO'}).json()
        base=f'/api/paper-jobs/{state["id"]}'
        r=c.post(base+'/graphs/run',json=dict(expected_revision=1,objective='Extract graph',max_panels=1));assert r.status_code==200,r.text
        graph=c.get(base).json()['graph_run'];assert graph['status']=='awaiting_review',graph
        opened=c.post(base+'/graphs/panel-001/open',json={}).json();pid=opened['project_id']
        workspace=c.get('/api/projects/'+pid).json();assert len(workspace['document']['measurements'])==2
        workspace['document']['measurements'][0]['points'][0]['x']+=8
        saved=c.put('/api/projects/'+pid,json=dict(expected_revision=workspace['revision'],document=workspace['document']))
        assert saved.status_code==200
        again=c.post(base+'/graphs/panel-001/open',json={}).json();assert again['reused'] and again['project_id']==pid
        assert c.get('/api/projects/'+pid).json()['revision']==2
        # Refinement can replace the active workspace; keep the edited original in archives.
        folder=pj.job_path(mod.PAPER_JOBS,state['id'])
        graph=c.get(base).json()['graph_run']
        panel=graph['panels'][0]
        panel['original_proposal']={'project_id':panel.pop('project_id')}
        pg.update(folder,graph)
        with zipfile.ZipFile(io.BytesIO(c.get(base+'/archive').content)) as archive:
            attachment=json.loads(archive.read(f'workspaces/{pid}.json'))
            assert attachment['current']['revision']==2 and len(attachment['history'])==2
            assert attachment['current']['results']['measurements'][0]['values']['x']==pytest.approx(31)
        assert len(calls)==3

def test_bad_optional_conditions_never_sent_downstream(graph_setup):
    state=create(graph_setup);target=selection()
    target['targets'][0]['evidence'].append(dict(page=1,role='conditions',quote='UNSUPPORTED_PRIVATE_CONDITION'))
    target['targets'][0]['reason']='UNSUPPORTED_INTERPRETATION'
    calls=[]
    async def send(body):
        calls.append(body);return envelope([target,located(),measured()][len(calls)-1])
    pg.begin(graph_setup,state['id'],pg.Run(expected_revision=1,objective='Extract graph'),'# Rules')
    asyncio.run(pg.execute(graph_setup,state['id'],send,'# Rules'))
    graph=pj.snapshot(graph_setup,state['id'])['graph_run']
    assert graph['status']=='awaiting_review'
    assert len(graph['panels'][0]['rejected_evidence'])==1
    for body in calls[1:]:
        assert 'UNSUPPORTED_PRIVATE_CONDITION' not in json.dumps(body)
        assert 'UNSUPPORTED_INTERPRETATION' not in json.dumps(body)
    assert any('failed source checks' in w for w in graph['panels'][0]['result']['model_warnings'])

def test_refinement_keeps_original_and_uses_candidate_ids(graph_setup,monkeypatch):
    from backend import graph_refine as gr
    state=create(graph_setup);responses=[selection(),located(),measured()]
    async def initial(body):return envelope(responses.pop(0))
    pg.begin(graph_setup,state['id'],pg.Run(expected_revision=1,objective='Extract graph'),'rules')
    asyncio.run(pg.execute(graph_setup,state['id'],initial,'rules'))
    folder=pj.job_path(graph_setup,state['id']);g=pj.snapshot(graph_setup,state['id'])['graph_run']
    original=(folder/'graphs'/g['id']/'panel-001/result.json').read_bytes()
    c=dict(width=1000,height=1000,frame=dict(left=100,top=100,right=900,bottom=900),
      ticks=dict(x=[dict(id=f'X{i}',x=x,y=900) for i,x in enumerate([100,500,900])],
                 y=[dict(id=f'Y{i}',x=100,y=y) for i,y in enumerate([900,500,100])]),
      markers=[dict(id='M0',x=300,y=700)],warnings=[])
    monkeypatch.setattr(gr.pixels,'detect',lambda *args:c)
    def axis(prefix):return dict(title='Axis',unit='u',ticks=[dict(candidate_id=f'{prefix}{i}',value=v) for i,v in enumerate([0,50,100])])
    calls=[]
    async def send(body):
        calls.append(body)
        return envelope(dict(status='bound',reason='Fixture',x_axis=axis('X'),y_axis=axis('Y'),markers=[dict(candidate_id='M0',sample_label='A',curve_label='',binding_evidence='Fixture')]))
    job=asyncio.run(gr.execute(graph_setup,state['id'],'panel-001',send))
    assert len(calls)==1

    assert len(calls[0]['messages'][1]['content'])==3
    assert (folder/'graphs'/g['id']/'panel-001/result.json').read_bytes()==original
    p=job['graph_run']['panels'][0]
    assert p['refinement']['status']=='needs_review' and p['result']['pixel_refinement']
    _,_,_,result,path=pg.panel(graph_setup,state['id'],'panel-001')
    assert path.name==p['refinement']['id'] and result['points'][0]['x']==25
    with pytest.raises(ValueError):asyncio.run(gr.execute(graph_setup,state['id'],'panel-001',send))
    assert len(calls)==1


def test_mapping_api_keeps_geometry_pending_and_archives_master(graph_setup,tmp_path,monkeypatch):
    from tests.test_graph_mapping import master
    monkeypatch.setenv('TIFIGURE_DATA',str(tmp_path/'mapping-api'))
    sys.modules.pop('backend.app',None);mod=importlib.import_module('backend.app')
    output=measured();output['plot']['y_axis'].update(title='KIC',unit='MPa m^1/2')
    for marker in output['plot']['markers']:marker['sample_label']='Solution treatment: 880 °C×1.5 h; Aging temperature: 620 °C'
    responses=[selection(),located(),output]
    async def fake(body):return envelope(responses.pop(0))
    monkeypatch.setattr(mod.deepseek,'send',fake);mod.deepseek.configure('synthetic-test-key')
    with TestClient(mod.app,headers={'X-TiFigure-Client':'workbench'}) as c:
        state=c.post('/api/paper-jobs',files={'file':('fixture.pdf',b'%PDF-1.4\nfixture')},data={'paper':'TEST'}).json()
        base=f'/api/paper-jobs/{state["id"]}'
        c.post(base+'/graphs/run',json=dict(expected_revision=1,objective='Extract graph',max_panels=1))
        opened=c.post(base+'/graphs/panel-001/open',json={}).json();pid=opened['project_id']
        before=c.get('/api/projects/'+pid).json()
        raw=json.dumps(master((30,50))).encode()
        ref=c.post('/api/references',files={'file':('master.json',raw)}).json()
        payload=dict(reference_id=ref['id'],paper_short_id='TEST',expected_revision=before['revision'])
        result=c.post(base+'/graphs/panel-001/mapping',json=payload)
        assert result.status_code==200,result.text
        assert result.json()['saved'] and result.json()['proposal']['proposed_fields']==2
        after=c.get('/api/projects/'+pid).json()
        assert after['document']['measurements']==before['document']['measurements']
        assert all(not x['sample_checked'] for x in after['document']['candidates'])
        assert c.post(base+'/graphs/panel-001/mapping',json=payload).status_code==422
        with zipfile.ZipFile(io.BytesIO(c.get(base+'/archive').content)) as z:
            assert z.read('references/'+ref['id']+'.json')==raw
            assert any(n.endswith('/mapping.json') for n in z.namelist())
