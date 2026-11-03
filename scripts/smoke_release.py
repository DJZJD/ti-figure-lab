"""Offline source-package smoke check using only synthetic data in a new database."""
import csv
import importlib
import io
import json
import math
import os
import sys
import zipfile
from pathlib import Path
from uuid import uuid4
from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]


def main():
    if 'backend.app' in sys.modules:raise RuntimeError('Run this module in its own process.')
    folder=ROOT/'tmp'/('release-smoke-'+uuid4().hex)
    folder.mkdir(parents=True,exist_ok=False)
    os.environ['TIFIGURE_DATA']=str(folder/'data')
    app=importlib.import_module('backend.app')
    checks=[]
    with TestClient(app.app,headers={'X-TiFigure-Client':'workbench'}) as client:
        def good(response):
            response.raise_for_status();return response.json()
        assert good(client.get('/api/health'))['project_root']==str(ROOT)
        raw=(ROOT/'examples/synthetic-reference.json').read_bytes()
        reference=good(client.post('/api/references',files={'file':('synthetic-reference.json',raw)}))
        assert len(reference['targets'])==3
        for mode in ('micrograph','graph'):
            image=good(client.post('/api/demos/'+mode))
            document=dict(name='SYNTHETIC DELIVERY CHECK '+mode,image_id=image['id'],mode=mode,
                          source='Built-in synthetic practice; no experimental data',reference_id=reference['id'])
            if mode=='micrograph':
                document.update(spatial=dict(points=[dict(x=100,y=600),dict(x=200,y=600)],length=10,unit='µm'),
                    measurements=[dict(id='square',kind='area',sample='DEMO-SAMPLE',points=[dict(x=200,y=200),dict(x=400,y=200),dict(x=400,y=400),dict(x=200,y=400)])])
            else:
                document.update(graph=dict(x=dict(points=[dict(x=100,y=600),dict(x=900,y=600)],values=[10,90],unit='degrees'),
                    y=dict(points=[dict(x=100,y=600),dict(x=100,y=100)],values=[0,100],unit='a.u.'),use_y=True),
                    measurements=[dict(id='point',kind='point',sample='DEMO-SAMPLE',points=[dict(x=500,y=300)])])
            url='/api/projects/'+str(uuid4())
            saved=good(client.put(url,json=dict(document=document,expected_revision=0)))
            restored=good(client.get(url));assert restored['document']==saved['document']
            values=restored['results']['measurements'][0]['values']
            if mode=='micrograph':assert math.isclose(values['area'],400,rel_tol=1e-12) and math.isclose(values['ecd'],2*math.sqrt(400/math.pi),rel_tol=1e-12)
            else:assert values==dict(x=50,y=60)
            exported=good(client.get(url+'/export'));assert exported['document']==saved['document']
            rows=list(csv.DictReader(io.StringIO(client.get(url+'/export?format=csv').text.lstrip('\ufeff'))))
            assert {r['metric']:float(r['value']) for r in rows}==values
            preview=good(client.get(url+'/master-copy-preview'))
            bundle=good(client.post(url+'/master-copy',json=dict(preview_hash=preview['preview_hash'],selections=[],reviewer='Synthetic delivery check; no adoption')))
            with zipfile.ZipFile(bundle['path']) as archive:
                assert archive.read('original-master.json')==raw
                assert json.loads(archive.read('master-copy.json'))==json.loads(raw)
            assert good(client.get(url))['revision']==1
            checks.append(dict(mode=mode,values=values,status='passed'))
    result=dict(artifact_type='synthetic_release_software_check',all_passed=True,checks=checks,paid_model_calls=0)
    (folder/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result));print('Evidence: '+str(folder/'result.json'))


if __name__=='__main__':main()
