import json
from uuid import uuid4
import httpx
import pytest
from test_api import client
from test_llm_bridge import request, output


def live_request(client):
    r=request(client)
    r['config'].update(provider='DeepSeek',model='deepseek-flash',protocol='chat_completions')
    p=client.post('/api/llm/preview',json=r)
    assert p.status_code==200,p.text
    return {**r,'request_hash':p.json()['request_hash'],'call_id':str(uuid4())}


def configure(client):
    return client.post('/api/llm/connection',json={'api_key':'TEST_ONLY_NOT_A_REAL_KEY'})


def test_key_session_and_secret_errors(client,monkeypatch):
    import socket
    monkeypatch.setattr(socket,'create_connection',lambda *a,**k:pytest.fail('Saving key must not call provider'))
    assert not client.get('/api/llm/connection').json()['configured']
    assert configure(client).json()['configured']
    assert 'TEST_ONLY' not in client.get('/api/llm/connection').text
    for body in ({'api_key':'secret invalid'}, {'api_key':['secret']}, {'api_key':'secret','extra':1}):
        r=client.post('/api/llm/connection',json=body)
        assert r.status_code==422 and 'secret' not in r.text
    assert client.post('/api/llm/connection',json={'api_key':'TEST_ONLY_NOT_A_REAL_KEY'},headers={'X-TiFigure-Client':'wrong'}).status_code==403
    assert client.post('/api/llm/connection',json={'api_key':'TEST_ONLY_NOT_A_REAL_KEY'},headers={'Origin':'https://evil.example'}).status_code==403
    assert not client.delete('/api/llm/connection').json()['configured']


def test_send_requires_key_current_context_and_deepseek(client,monkeypatch):
    from backend.llm_live import DeepSeekSession
    async def forbidden(*a,**k):pytest.fail('Invalid request reached provider')
    monkeypatch.setattr(DeepSeekSession,'send',forbidden)
    r=live_request(client)
    assert client.post('/api/llm/send',json=r).status_code==422
    configure(client)
    r['request_hash']='0'*64
    assert client.post('/api/llm/send',json=r).status_code==409
    r['config']['provider']='Other'
    assert client.post('/api/llm/send',json=r).status_code==422


def test_live_result_pending_usage_roundtrip_and_duplicate_call(client,monkeypatch):
    from backend.llm_live import DeepSeekSession
    calls=[]
    async def fake(self,body):
        calls.append(body)
        return {'model':'test-deepseek-snapshot','usage':{'prompt_tokens':100,'completion_tokens':80,'total_tokens':180,'evil':'secret'},
                'choices':[{'finish_reason':'stop','message':{'content':json.dumps(output())}}]}
    monkeypatch.setattr(DeepSeekSession,'send',fake)
    configure(client);r=live_request(client)
    res=client.post('/api/llm/send',json=r);assert res.status_code==200,res.text
    data=res.json();p=data['validated']['plans'][0]
    assert p['review_basis'] is None and p['image_quality']=='unassessed'
    assert p['model_provenance']['network_executed'] and p['model_provenance']['import_kind']=='live_api'
    assert p['model_provenance']['response_model']=='test-deepseek-snapshot'
    assert data['usage']=={'prompt_tokens':100,'completion_tokens':80,'total_tokens':180}
    assert 'TEST_ONLY' not in res.text
    body=calls[0]
    assert body['thinking']=={'type':'disabled'} and body['response_format']=={'type':'json_object'}
    assert body['max_tokens']==2048 and 'max_completion_tokens' not in body
    assert 'Required JSON schema' in body['messages'][0]['content']
    assert client.post('/api/llm/send',json=r).status_code==409 and len(calls)==1
    d=r['document'];d['plans'].append(p)
    url='/api/projects/'+str(uuid4())
    assert client.put(url,json={'document':d,'expected_revision':0}).status_code==200
    assert client.get(url).json()['document']['plans'][-1]['model_provenance']==p['model_provenance']


@pytest.mark.parametrize('finish,content',[('length',json.dumps(output())),('stop',''),('stop','{}'),('stop','{"summary":"Bad","warnings":[],"proposals":[{"tool":"run_shell"}]}')])
def test_invalid_output_keeps_usage_and_never_imports(client,monkeypatch,finish,content):
    from backend.llm_live import DeepSeekSession
    async def fake(*a):return {'usage':{'total_tokens':42},'choices':[{'finish_reason':finish,'message':{'content':content}}]}
    monkeypatch.setattr(DeepSeekSession,'send',fake)
    configure(client)
    result=client.post('/api/llm/send',json=live_request(client)).json()
    assert result['validated'] is None and result['error'] and result['usage']['total_tokens']==42


@pytest.mark.parametrize('status',[401,402,429,500,307])
def test_transport_fixed_endpoint_no_redirect_secret_or_retry(client,monkeypatch,status):
    from backend import llm_live
    real_client=httpx.AsyncClient;calls=[]
    def handle(req):
        calls.append(req)
        assert str(req.url)=='https://api.deepseek.com/chat/completions'
        return httpx.Response(status,headers={'location':'https://other.example'},json={'error':'TEST_ONLY_NOT_A_REAL_KEY'})
    monkeypatch.setattr(llm_live.httpx,'AsyncClient',lambda **kw:real_client(transport=httpx.MockTransport(handle),**kw))
    configure(client)
    res=client.post('/api/llm/send',json=live_request(client))
    assert res.status_code==502 and 'TEST_ONLY' not in res.text and len(calls)==1


def test_transport_timeout_is_not_retried(client,monkeypatch):
    from backend import llm_live
    real_client=httpx.AsyncClient;calls=[]
    def handle(req):
        calls.append(req)
        raise httpx.ReadTimeout('secret transport diagnostics')
    monkeypatch.setattr(llm_live.httpx,'AsyncClient',lambda **kw:real_client(transport=httpx.MockTransport(handle),**kw))
    configure(client);r=live_request(client)
    res=client.post('/api/llm/send',json=r)
    assert res.status_code==502 and 'secret' not in res.text and len(calls)==1
    assert client.post('/api/llm/send',json=r).status_code==409
