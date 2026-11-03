import asyncio
import copy
import importlib
import io
import json
import sys
import zipfile
import pytest
from PIL import Image,ImageDraw
from fastapi.testclient import TestClient
from backend import paper_jobs as pj,paper_graphs as pg,paper_vision as pv,vision_tools as vt

QUOTE='Fig. 1 shows a micrograph and XRD patterns of sample A.'


def target():return dict(page=1,locator='Fig. 1',quantity='visual',evidence=[dict(page=1,role='caption',quote=QUOTE)],reason='Synthetic fixture only')


def micro():
    return dict(status='measured',reason='Synthetic offline fixture, not a model response',quality='usable',
        scale=dict(endpoints=[dict(x=100,y=900),dict(x=300,y=900)],length=10,unit='µm'),
        roi=dict(left=100,top=100,right=900,bottom=800),objects=[dict(seed=dict(x=400,y=400),radius_px=80,tolerance=10,
        fallback_outline=[],label='Unclassified object',sample_label='A',identity_evidence='No phase identity inferred')],warnings=[])


def xrd():
    return dict(status='measured',reason='Synthetic offline fixture, not a model response',quality='usable',
        plot_bounds=dict(left=100,top=100,right=900,bottom=900),x_axis=dict(title='2θ',unit='deg',ticks=[dict(x=100,y=900,value=20),dict(x=900,y=900,value=100),dict(x=500,y=900,value=60)]),
        peaks=[dict(x=402,y=402,sample_label='A',curve_label='lower trace',binding_evidence='Fixture label A',allow_local_snap=True,radius_px=20),
               dict(x=650,y=250,sample_label='B',curve_label='upper trace',binding_evidence='Fixture label B',allow_local_snap=False,radius_px=20)],warnings=[])


def image(kind):
    im=Image.new('RGB',(1000,1000),'white');d=ImageDraw.Draw(im)
    if kind=='micrograph':
        d.rectangle((380,380,419,419),fill=(50,50,50));d.line((100,900,300,900),fill='black',width=3)
    else:
        d.line((100,900,900,900),fill='black',width=2)
        d.line((100,700,370,700,400,400,430,700,900,700),fill='black',width=2)
        d.line((100,500,620,500,650,250,680,500,900,500),fill='red',width=2)
    return im


def build(tmp_path,kind,data=None):
    image(kind).save(tmp_path/'crop.png')
    transform=dict(crop_width=1000,crop_height=1000,crop_sha256=pj.digest((tmp_path/'crop.png').read_bytes()))
    return vt.build_result(tmp_path,pg.Target.model_validate(target()),(vt.Micrograph if kind=='micrograph' else vt.XRD).model_validate(data or (micro() if kind=='micrograph' else xrd())),transform,kind)


def test_micro_local_area_and_pending_editable_geometry(tmp_path):
    r=build(tmp_path,'micrograph')
    assert r['measurements'][0]['values']['area']==pytest.approx(4)
    assert r['provenance'][0]['method']=='local_gray_region'
    doc=vt.workspace(r,'1'*64,'TEST','fixture')
    assert doc.mode=='micrograph' and doc.measurements[0].status=='needs_review'
    assert not doc.measurements[0].include_in_summary and not r['scientifically_approved']


def test_xrd_stacked_peak_positions_not_intensities(tmp_path):
    r=build(tmp_path,'xrd')
    assert r['measurements'][0]['values']['x']==pytest.approx(50,abs=.2)
    assert r['measurements'][1]['values']=={'x':75}
    assert [p['method'] for p in r['provenance']]==['local_peak_snap','model_peak']
    assert r['document']['measurements'][1]['sample']=='B'
    assert not r['document']['graph']['use_y']


def test_fallback_outline_explicit_and_not_automatic_acceptance(tmp_path,monkeypatch):
    monkeypatch.setattr(vt,'region_candidate',lambda *a:dict(points=[],warnings=[],reason='Hole or leakage'))
    data=micro();data['objects'][0]['fallback_outline']=[dict(x=380,y=380),dict(x=420,y=380),dict(x=420,y=420),dict(x=380,y=420)]
    r=build(tmp_path,'micrograph',data)
    assert r['provenance'][0]['method']=='model_outline'
    assert 'not pixel-verified' in r['document']['measurements'][0]['note']
    data['objects'][0]['fallback_outline']=[dict(x=380,y=380),dict(x=420,y=420),dict(x=380,y=420),dict(x=420,y=380)]
    with pytest.raises(ValueError,match='No usable'):build(tmp_path,'micrograph',data)


@pytest.mark.parametrize('kind',['micrograph','xrd'])
def test_unreadable_quality_defers_before_measurement(tmp_path,kind):
    data=micro() if kind=='micrograph' else xrd();data['quality']='needs_better_source'
    with pytest.raises(ValueError):build(tmp_path,kind,data)


def test_xrd_bad_tick_or_axis_never_computes_numbers(tmp_path):
    data=xrd();data['x_axis']['ticks'][2]['value']=75
    with pytest.raises(ValueError,match='third-tick'):build(tmp_path,'xrd',data)
    data=xrd();data['x_axis']['title']='q'
    with pytest.raises(ValueError,match='2-theta'):build(tmp_path,'xrd',data)


def test_ambiguous_local_peaks_keep_model_position(tmp_path,monkeypatch):
    monkeypatch.setattr(vt,'peak_candidates',lambda *a:dict(candidates=[dict(x=400,y=400,kind='supported_apex',distance_px=2),dict(x=405,y=403,kind='supported_apex',distance_px=3)]))
    r=build(tmp_path,'xrd');assert r['provenance'][0]['method']=='model_peak'
    assert r['measurements'][0]['values']['x']==pytest.approx(50.2)


@pytest.fixture
def setup(tmp_path,monkeypatch):
    def parse(raw,folder):
        image('micrograph').save(folder/'pages/page-001.png')
        return [dict(page=1,text=QUOTE,words=[],width=1000,height=1000,image='pages/page-001.png',native_text_usable=True)]
    monkeypatch.setattr(pj,'parse_pdf',parse)
    def crop(folder,page,box,dest):
        job=pj.read_json(folder/'job.json');kind=next(k for k,v in job['vision_runs'].items() if v['status']=='running')
        image(kind).save(dest)
        return dict(crop_width=1000,crop_height=1000,crop_sha256=pj.digest(dest.read_bytes()))
    monkeypatch.setattr(pv,'crop_pdf',crop)
    return tmp_path/'jobs'


def response(data):return dict(choices=[dict(finish_reason='stop',message=dict(content=json.dumps(data)))],usage={'total_tokens':1})


@pytest.mark.parametrize('kind',['micrograph','xrd'])
def test_full_pipeline_api_workspace_archive_and_no_overwrite(setup,tmp_path,monkeypatch,kind):
    monkeypatch.setenv('TIFIGURE_DATA',str(tmp_path/'api'));sys.modules.pop('backend.app',None);app=importlib.import_module('backend.app')
    calls=[]
    async def send(body):
        calls.append(body);stage=json.loads(body['messages'][1]['content'][0]['text'])['stage']
        return response(dict(targets=[target()],deferred=[]) if stage=='selection' else dict(status='located',reason='fixture',bbox=dict(left=10,top=10,right=990,bottom=990)) if stage=='locate' else micro() if kind=='micrograph' else xrd())
    monkeypatch.setattr(app.deepseek,'send',send);app.deepseek.configure('synthetic-test-key-not-real')
    with TestClient(app.app,headers={'X-TiFigure-Client':'workbench'}) as c:
        s=c.post('/api/paper-jobs',files={'file':('fixture.pdf',b'%PDF-fixture')},data={'paper':'TEST'}).json();base=f'/api/paper-jobs/{s["id"]}'
        r=c.post(base+'/vision/run',json=dict(kind=kind,bind_candidates=False,objective='Measure fixture only',expected_revision=1,max_panels=1));assert r.status_code==200,r.text
        state=c.get(base).json();run=state['vision_runs'][kind]
        assert run['status']=='awaiting_review',run
        assert len(calls)==3 and all('Response JSON schema' in b['messages'][0]['content'] for b in calls)
        assert len(calls[0]['messages'][1]['content'])==1 and len(calls[1]['messages'][1]['content'])==2
        url=base+f'/vision/{kind}/panel-001/open';opened=c.post(url).json();pid=opened['project_id']
        assert c.post(url).json()['reused'] is True
        project=c.get('/api/projects/'+pid).json();assert len(project['document']['measurements'])>0
        project['document']['measurements'][0]['label']='Human correction retained'
        saved=c.put('/api/projects/'+pid,json=dict(expected_revision=project['revision'],document=project['document']))
        assert saved.status_code==200,saved.text
        assert c.post(url).json()['reused'] is True
        assert c.get('/api/projects/'+pid).json()['document']['measurements'][0]['label']=='Human correction retained'
        assert c.get(base+'/assets/'+run['panels'][0]['overlay']).status_code==200
        with zipfile.ZipFile(io.BytesIO(c.get(base+'/archive').content)) as z:
            assert f'workspaces/{pid}.json' in z.namelist()
            assert any(n.endswith('panel-001-measure/input.json') for n in z.namelist())
            assert all(pj.digest(z.read(f['path']))==f['sha256'] for f in json.loads(z.read('archive-manifest.json'))['files'])
        latest=c.get(base).json()
        assert c.post(base+'/vision/run',json=dict(kind=kind,bind_candidates=False,objective='Measure fixture only',expected_revision=latest['revision'])).status_code==409
        assert len(calls)==3
        fallback=c.post(base+f'/vision/{kind}/panel-001/open-source').json()
        manual=c.get('/api/projects/'+fallback['project_id']).json()['document']
        assert manual['measurements']==[] and manual['spatial']['points']==[] and manual['graph']['x']['points']==[]
        latest=c.get(base).json()
        retry_url=base+f'/vision/{kind}/panel-001/remeasure'
        retried=c.post(retry_url,json={'expected_revision':latest['revision']});assert retried.status_code==200,retried.text
        current=c.get(base).json();assert current['vision_runs'][kind]['panels'][0]['remeasure']['status']=='needs_review'
        assert len(calls)==4 and len(calls[-1]['messages'][1]['content'])==3
        new=c.post(url).json();assert new['project_id']!=pid
        assert c.get('/api/projects/'+pid).json()['document']['measurements'][0]['label']=='Human correction retained'
        with zipfile.ZipFile(io.BytesIO(c.get(base+'/archive').content)) as z:
            assert f'workspaces/{pid}.json' in z.namelist() and f'workspaces/{new["project_id"]}.json' in z.namelist()
        assert c.post(retry_url,json={'expected_revision':new['job']['revision']}).status_code==409
        assert len(calls)==4
        if kind in ('xrd','micrograph'):
            before=c.get(base).json()
            calc=c.post(base+f'/vision/{kind}/panel-001/recompute',json={'expected_revision':before['revision']})
            assert calc.status_code==200 and calc.json()['vision_runs'][kind]['panels'][0]['local_recompute']['status']=='needs_review'
            reopened=c.post(url).json();assert reopened['project_id']!=new['project_id']
            with zipfile.ZipFile(io.BytesIO(c.get(base+'/archive').content)) as z:
                assert all(f'workspaces/{p}.json' in z.namelist() for p in [pid,new['project_id'],reopened['project_id']])
            assert len(calls)==4


def test_failed_caption_stops_before_image_calls_and_busy_guard(setup):
    s=pj.create(setup,b'%PDF-fixture','fixture.pdf','TEST','# rules');req=pv.Run(kind='xrd',expected_revision=1,objective='Extract XRD')
    pv.begin(setup,s['id'],req,'# visual rules')
    with pytest.raises(ValueError):pj.bundle(setup,s['id'])
    with pytest.raises(ValueError):pg.begin(setup,s['id'],pg.Run(expected_revision=2,objective='Extract graph'),'# graph')
    bad=target();bad['evidence'][0]['quote']='Fabricated caption'
    calls=[]
    async def send(body):calls.append(body);return response(dict(targets=[bad],deferred=[]))
    asyncio.run(pv.execute(setup,s['id'],'xrd',send,'# visual rules'))
    run=pj.snapshot(setup,s['id'])['vision_runs']['xrd']
    assert run['status']=='deferred' and len(calls)==1


def test_xrd_title_unit_suffix_and_y_disagreement_are_explicit(tmp_path):
    data=xrd();data['x_axis']['title']='2θ/degree'
    for t in data['x_axis']['ticks']:t['y']=950
    r=build(tmp_path,'xrd',data)
    assert r['calibration_check']['axis_edge_disagreement']
    assert any('tick Y positions disagree' in w for w in r['warnings'])


@pytest.mark.parametrize('size',[700,1000])
def test_local_ticks_correct_positions_without_fitting_values(size):
    from backend.xrd_pixels import refine_axis
    im=Image.new('RGB',(size,size),'white');d=ImageDraw.Draw(im)
    d.rectangle((size*.1,size*.1,size*.9,size*.9),outline='black',width=2)
    d.line((size*.1,size*.7,size*.9,size*.7),fill='black',width=2)
    for f in [.3,.5,.7]:d.line((size*f,size*.88,size*f,size*.9),fill='black',width=2)
    r=vt.XRD.model_validate(xrd());r.x_axis.ticks[0].x+=12;r.x_axis.ticks[1].x-=10;r.x_axis.ticks[2].x-=5
    a,b,audit=refine_axis(im,r.x_axis,r.plot_bounds)
    assert [t.value for t in a.ticks]==[t.value for t in r.x_axis.ticks]
    axis,check=vt.x_calibration(a,b,size,size)
    assert check['pixel_residual']<2 and audit['frame_pixels'][3]>.85*size

def test_image_first_binding_stage_preserves_pixels_and_archives(setup):
    s=pj.create(setup,b'%PDF-fixture','fixture.pdf','TEST','# rules')
    pv.begin(setup,s['id'],pv.Run(kind='xrd',expected_revision=1,objective='Bind candidates'), '# rules')
    captured=[]
    async def send(body):
        data=json.loads(body['messages'][1]['content'][0]['text']);stage=data['stage'];captured.append(data)
        if stage=='selection':out=dict(targets=[target()],deferred=[])
        elif stage=='locate':out=dict(status='located',reason='fixture',bbox=dict(left=10,top=10,right=990,bottom=990))
        elif stage=='measure':out=xrd()
        else:
            out=dict(decisions=[dict(candidate_id=c['id'],decision='keep',sample_label='A',curve_label='fixture trace',evidence='Synthetic figure label A') for c in data['context']['candidates']],warnings=[])
        return response(out)
    asyncio.run(pv.execute(setup,s['id'],'xrd',send,'# rules'))
    run=pj.snapshot(setup,s['id'])['vision_runs']['xrd'];assert run['calls_attempted']==run['max_calls']==4
    result=run['panels'][0]['result'];cs={c['id']:c for c in captured[-1]['context']['candidates']}
    assert result['document']['measurements']
    for m in result['document']['measurements']:
        assert m['points']==[dict(x=cs[m['id']]['x'],y=cs[m['id']]['y'])]
        assert m['status']=='needs_review'
    assert result['provenance'][0]['method']=='local_candidate_model_binding'
    from backend.xrd_binding import Bindings,apply_bindings
    bad=Bindings.model_validate(dict(decisions=[dict(candidate_id='invented',decision='keep',sample_label='A',curve_label='x',evidence='bad')],warnings=[]))
    folder=setup/s['id']/'vision'/run['id']/'panel-001'
    with pytest.raises(ValueError):apply_bindings(folder,result,bad)
    assert (folder/'before-binding-result.json').is_file()

def test_binding_uncertain_reject_and_invalid_ids_never_adopt(tmp_path):
    from backend.xrd_binding import Bindings,apply_bindings
    r=build(tmp_path,'xrd');cs=r['calibration_check']['image_first_candidates'];assert cs
    decisions=[dict(candidate_id=c['id'],decision='uncertain' if i==0 else 'reject',sample_label='',curve_label='',evidence='Ambiguous or artifact') for i,c in enumerate(cs)]
    bad=copy.deepcopy(decisions);bad.append(copy.deepcopy(bad[0]))
    with pytest.raises(ValueError):apply_bindings(tmp_path,r,Bindings(decisions=bad,warnings=[]))
    with pytest.raises(ValueError):apply_bindings(tmp_path,r,Bindings(decisions=decisions[:-1],warnings=[]))
    out=apply_bindings(tmp_path,r,Bindings(decisions=decisions,warnings=[]))
    assert out['document']['measurements']==[] and out['measurements']==[]
    assert len(out['deferred'])==len(cs) and not out['scientifically_approved']
    assert pj.read_json(tmp_path/'before-binding-result.json')['measurements']==r['measurements']

def test_region_selection_workspace_no_scale_no_overwrite_and_archive(setup,tmp_path,monkeypatch):
    monkeypatch.setenv('TIFIGURE_DATA',str(tmp_path/'selection-api'));sys.modules.pop('backend.app',None);app=importlib.import_module('backend.app')
    async def send(body):
        stage=json.loads(body['messages'][1]['content'][0]['text'])['stage']
        return response(dict(targets=[target()],deferred=[]) if stage=='selection' else dict(status='located',reason='fixture',bbox=dict(left=10,top=10,right=990,bottom=990)) if stage=='locate' else micro())
    monkeypatch.setattr(app.deepseek,'send',send);app.deepseek.configure('synthetic-test-key-not-real')
    with TestClient(app.app,headers={'X-TiFigure-Client':'workbench'}) as c:
        job=c.post('/api/paper-jobs',files={'file':('fixture.pdf',b'%PDF-fixture')},data={'paper':'TEST'}).json();base=f'/api/paper-jobs/{job["id"]}'
        c.post(base+'/vision/run',json=dict(kind='micrograph',objective='Measure fixture only',expected_revision=1,max_panels=1))
        state=c.get(base).json();panel=state['vision_runs']['micrograph']['panels'][0];ids=[p['id'] for p in panel['region_candidates']['candidates']];assert ids
        regenerated=c.post(base+'/vision/micrograph/panel-001/propose-regions',json=dict(expected_revision=state['revision']));assert regenerated.status_code==200
        state=regenerated.json()
        url=base+'/vision/micrograph/panel-001/select-regions'
        bad=c.post(url,json=dict(expected_revision=state['revision'],candidate_ids=['invented']));assert bad.status_code==409
        selected=c.post(url,json=dict(expected_revision=state['revision'],candidate_ids=ids[:1]));assert selected.status_code==200,selected.text
        result=selected.json();pid=result['project_id'];project=c.get('/api/projects/'+pid).json()
        assert project['document']['spatial']['points']==[] and len(project['document']['measurements'])==1
        assert not project['results']['measurements'][0]['valid']
        project['document']['measurements'][0]['label']='Human edit'
        assert c.put('/api/projects/'+pid,json=dict(expected_revision=project['revision'],document=project['document'])).status_code==200
        again=c.post(url,json=dict(expected_revision=result['job']['revision'],candidate_ids=ids[:1])).json();assert again['reused']
        assert c.get('/api/projects/'+pid).json()['document']['measurements'][0]['label']=='Human edit'
        with zipfile.ZipFile(io.BytesIO(c.get(base+'/archive').content)) as z:assert f'workspaces/{pid}.json' in z.namelist()
        assert c.post(url,json=dict(expected_revision=1,candidate_ids=ids[:1])).status_code==409

def test_long_descriptive_locator_preserved_but_bounded():
    data=target();data['locator']='Fig. 3(a): '+('main panel with retained scale; '*12)
    parsed=pg.Target.model_validate(data)
    assert parsed.locator==data['locator']
    data['locator']='x'*1001
    with pytest.raises(ValueError):pg.Target.model_validate(data)

def test_visual_drafts_are_unmapped_idempotent_and_never_auto_reviewed(tmp_path):
    from backend.candidates import measurement_drafts,candidate_results
    r=build(tmp_path,'xrd');doc=vt.workspace(r,'1'*64,'TEST','Fixture source')
    assert len(doc.candidates)==len(doc.measurements)
    assert all(c.target_path is None and c.sample_ids==[] and not c.sample_checked and c.review_basis is None for c in doc.candidates)
    assert measurement_drafts(doc,vt.evaluate(doc))==[]
    assert all(c['status']=='blocked' for c in candidate_results(doc,vt.evaluate(doc),None))
    doc.candidates[0].evidence='Human mapping notes';assert measurement_drafts(doc,vt.evaluate(doc))==[]
    assert doc.candidates[0].evidence=='Human mapping notes'
