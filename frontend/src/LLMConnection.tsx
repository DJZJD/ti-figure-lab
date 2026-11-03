import React,{useState} from 'react';

export function LLMConnection(){
  const [open,setOpen]=useState(false),[key,setKey]=useState(''),[configured,setConfigured]=useState(false),[busy,setBusy]=useState(false),[message,setMessage]=useState('');
  async function request(method='GET',body?:unknown){
    const r=await fetch('/api/llm/connection',{method,headers:{'Content-Type':'application/json','X-TiFigure-Client':'workbench'},body:body?JSON.stringify(body):undefined});
    const result=await r.json();
    if(!r.ok)throw new Error(typeof result.detail==='string'?result.detail:'Could not configure the local connection.');
    setConfigured(result.configured);
  }
  async function action(fn:()=>Promise<void>){setBusy(true);setMessage('');try{await fn();}catch(e){setMessage((e as Error).message);}finally{setBusy(false);}}
  return <><button onClick={()=>{setOpen(true);void action(()=>request());}}>API settings</button>
    {open&&<div className="connection-backdrop"><section role="dialog" aria-modal="true" aria-labelledby="connection-title" className="connection-dialog">
      <h2 id="connection-title">DeepSeek connection</h2>
      <p>Endpoint: <code>https://api.deepseek.com</code><br/>Model: <code>deepseek-flash</code></p>
      <p>Key status: <strong>{configured?'Configured for this server session':'Not configured'}</strong>. Saving a key does not verify it or call a model.</p>
      <label>API Key<input aria-label="DeepSeek API Key" type="password" autoComplete="off" spellCheck={false} maxLength={512} value={key} onChange={e=>setKey(e.target.value)} placeholder="Paste your key here"/></label>
      <p className="hint">Stored only in local server memory. Never included in project exports. Restarting the server clears it; closing this browser tab does not. No browser storage is used.</p>
      <div className="candidate-actions"><button className="primary" disabled={busy||!key.trim()} onClick={()=>void action(async()=>{try{await request('POST',{api_key:key});setMessage('Key saved for this server session. No API call was made.');}finally{setKey('');}})}>Save for this session</button>
      <button disabled={busy||!configured} onClick={()=>void action(async()=>{await request('DELETE');setKey('');setMessage('Key removed. An already running request cannot be recalled.');})}>Forget key</button>
      <button disabled={busy} onClick={()=>{setOpen(false);setKey('');setMessage('');}}>Close</button></div>
      {message&&<p role="status">{message}</p>}
    </section></div>}
  </>;
}
