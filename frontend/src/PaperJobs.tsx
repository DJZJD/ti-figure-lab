import React,{useEffect,useState} from 'react';
import {GraphExtraction,GraphRun} from './GraphExtraction';
import {ReportedMapping,ReportedMap} from './ReportedMapping';
import {VisualExtraction,VisualRun} from './VisualExtraction';

type Review={corrected_value:number|null;corrected_unit:string;corrected_sample_label:string;reviewer:string;note:string};
type CellEvidence={status:string;reason?:string;notice?:string;source_image?:string;overlay_image?:string;cell?:{expression:string;unit:string;sample_label:string;quantity:string;uncertainty_kind:string}};
type Row={id:string;kind:string;quantity:string;definition:string;sample_label:string;conditions:string;value:number|null;raw_value:string;unit:string;page:number;locator:string;evidence_quote:string;condition_page:number|null;condition_quote:string;notes:string;checks:string[];evidence_image:string|null;review_status:string;review:Review|null;table_evidence?:CellEvidence};
type Job={vision_runs?:Record<string,VisualRun>;reported_mapping?:ReportedMap;extraction_scope?:string;id:string;paper:string;filename:string;status:string;revision:number;request_hash?:string;page_count?:number;textless_pages?:number[];records:Row[];deferred_items:{page:number;locator:string;reason:string}[];limitations:string[];model_limitations?:string[];summary?:string;error:string|null;usage?:Record<string,number>;graph_run?:GraphRun;table_evidence_run?:{summary?:Record<string,number>;error?:string}};
async function request<T>(path:string,body?:unknown):Promise<T>{
  const response=await fetch('/api/paper-jobs'+path,{method:body?'POST':'GET',headers:{'X-TiFigure-Client':'workbench',...(body&&!(body instanceof FormData)?{'Content-Type':'application/json'}:{})},body:body instanceof FormData?body:body?JSON.stringify(body):undefined});
  const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Invalid paper request. Check fields.');return data;
}
function Candidate({job,row,onSaved}:{job:Job;row:Row;onSaved:(j:Job)=>void}){
  const [reviewer,setReviewer]=useState(row.review?.reviewer||''),[note,setNote]=useState(row.review?.note||'');
  const [value,setValue]=useState(String(row.review?.corrected_value??row.value??'')),[unit,setUnit]=useState(row.review?.corrected_unit??row.unit),[sample,setSample]=useState(row.review?.corrected_sample_label??row.sample_label);
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  async function decide(decision:string){setBusy(true);setError('');try{
    if(value.trim()!==''&&!Number.isFinite(Number(value)))throw new Error('Use a finite numeric value, or leave blank and defer.');
    onSaved(await request<Job>(`/${job.id}/records/${row.id}/review`,{expected_revision:job.revision,decision,reviewer,note,
      corrected_value:value.trim()===''?null:Number(value),corrected_unit:unit,corrected_sample_label:sample}));
  }catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  return <details className="paper-candidate"><summary><strong>{row.quantity}</strong> · {row.raw_value} {row.unit} · {row.sample_label||'Unbound sample'} · {row.review_status}{row.checks.length?' · Flags':''}</summary>
    <p>{row.definition}<br/>Conditions: {row.conditions||'Unresolved'}</p>
    <p>p.{row.page} · {row.locator} · <a target="_blank" rel="noreferrer" href={`/api/paper-jobs/${job.id}/assets/pages/page-${String(row.page).padStart(3,'0')}.png`}>Open full page</a></p>
    {row.table_evidence&&<section><h4>Independent PDF cell evidence · {row.table_evidence.status}</h4>
      <p>{row.table_evidence.cell?.sample_label} · {row.table_evidence.cell?.quantity} · {row.table_evidence.cell?.expression} {row.table_evidence.cell?.unit}</p>
      {row.table_evidence.overlay_image&&<><img className="paper-evidence" alt="Original PDF table with highlighted header, sample label and numeric cell" src={`/api/paper-jobs/${job.id}/assets/${row.table_evidence.overlay_image}`}/><p>Orange: column header · Blue: sample label · Red: center and uncertainty cell. <a target="_blank" rel="noreferrer" href={`/api/paper-jobs/${job.id}/assets/${row.table_evidence.source_image}`}>Open unmarked table crop</a></p></>}
      <p>{row.table_evidence.reason||row.table_evidence.notice}</p><p>Uncertainty type and sample conditions remain unverified. Original model quote warnings below are retained; a supported cell does not repair the quote or approve the result.</p>
    </section>}
    {row.evidence_image&&<img className="paper-evidence" alt="Automatically cropped source text evidence" src={`/api/paper-jobs/${job.id}/assets/${row.evidence_image}`}/>}
    <blockquote>{row.evidence_quote}</blockquote>
    {row.condition_quote&&<p>Condition evidence (p.{row.condition_page}): {row.condition_quote}</p>}
    {row.checks.map((w,i)=><p className="warning-text" key={i}>{w}</p>)}<p>{row.notes}</p>
    <fieldset disabled={busy}><div className="reported-grid"><label>Reviewed scalar<input value={value} onChange={e=>setValue(e.target.value)}/></label><label>Reviewed unit<input value={unit} onChange={e=>setUnit(e.target.value)}/></label><label>Reviewed sample label<input value={sample} onChange={e=>setSample(e.target.value)}/></label><label>Reviewer<input value={reviewer} onChange={e=>setReviewer(e.target.value)}/></label></div>
      <label>Review note / reason for correction<textarea value={note} onChange={e=>setNote(e.target.value)} placeholder="Check sample, units, definition, numerical source and any flags before accepting."/></label>
      <div className="candidate-actions"><button disabled={!note.trim()||!reviewer.trim()} onClick={()=>void decide('accepted')}>Accept reviewed value</button><button disabled={!note.trim()||!reviewer.trim()} onClick={()=>void decide('rejected')}>Reject</button><button disabled={!note.trim()||!reviewer.trim()} onClick={()=>void decide('needs_review')}>Keep pending</button></div>
    </fieldset>{error&&<p role="alert" className="warning-text">{error}</p>}
  </details>;
}

export function PaperJobs({onClose,onOpen}:{onClose:()=>void;onOpen:(id:string)=>Promise<void>}){
  const [scope,setScope]=useState('all');
  const [jobs,setJobs]=useState<Job[]>([]),[selected,setSelected]=useState<Job|null>(null),[paper,setPaper]=useState('PNEW'),[file,setFile]=useState<File|null>(null);
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[packet,setPacket]=useState<unknown>(null),[consent,setConsent]=useState(false);
  async function reload(){const r=await request<{items:Job[]}>('');setJobs(r.items);return r.items;}
  useEffect(()=>{void reload().catch(e=>setError(e.message));},[]);
  useEffect(()=>{if(!selected)return;if(selected?.status!=='running'&&selected?.graph_run?.status!=='running'&&!Object.values(selected?.vision_runs||{}).some(r=>r.status==='running'))return;let disposed=false;const timer=setInterval(()=>{
    void request<Job>('/'+selected.id).then(j=>{if(!disposed)setSelected(j);}).catch(e=>{if(!disposed)setError(e.message);});
  },2000);return()=>{disposed=true;clearInterval(timer);};},[selected?.id,selected?.status,selected?.graph_run?.status,selected?.vision_runs]);
  async function act(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  function choose(job:Job){setSelected(job);setPacket(null);setConsent(false);setError('');}
  return <div className="modal-backdrop"><section className="modal paper-dialog" role="dialog" aria-modal="true" aria-labelledby="paper-title">
    <div className="section-row"><div><span className="eyebrow">AUTOMATED PAPER INTAKE · EXPERIMENTAL</span><h2 id="paper-title">Paper extraction</h2></div><button disabled={busy} onClick={onClose}>Close</button></div>
    <p>Import a PDF, then choose reported-value extraction or automatic graph extraction. Both retain source evidence and pending results for human review. Neither creates a final TiFM master.</p>
    <fieldset disabled={busy}><div className="reported-grid"><label>Paper label<input value={paper} maxLength={40} onChange={e=>setPaper(e.target.value)}/></label><label>Source PDF (up to 40 MiB / 60 pages)<input type="file" accept=".pdf" onChange={e=>setFile(e.target.files?.[0]||null)}/></label></div>
      <button className="primary" disabled={!file} onClick={()=>void act(async()=>{const form=new FormData();form.append('file',file!);form.append('paper',paper);form.append('scope',scope);choose(await request<Job>('',form));await reload();})}>{busy?'Working…':'Import PDF & prepare locally'}</button>
      <label>Reported-value scope<select value={scope} onChange={e=>setScope(e.target.value)}><option value="all">All supported fields (may exceed one response)</option><option value="mechanical_properties">Mechanical properties · up to 16 records</option></select></label>
      <button onClick={()=>void act(async()=>{const rows=await reload();if(selected){const fresh=rows.find(j=>j.id===selected.id);if(fresh)setSelected(fresh);}})}>Refresh jobs</button>
    </fieldset>
    <p className="hint">Importing makes no model call. Configure your key via API settings before running extraction. Source PDF, requirements, requests, responses, page images, evidence crops and review history stay in the paper-job archive.</p>
    <div className="paper-layout"><nav aria-label="Paper jobs">{jobs.map(j=><button disabled={busy} key={j.id} aria-pressed={selected?.id===j.id} onClick={()=>choose(j)}><b>{j.paper}</b><small>{j.filename}<br/>{j.status}</small></button>)}{!jobs.length&&<p>No paper jobs yet.</p>}</nav>
    <div>{selected&&<><h3>{selected.paper} · {selected.status}</h3><p>Scope: {selected.extraction_scope??'all'} · {selected.page_count??0} pages · {selected.records.length} candidates · {selected.records.filter(r=>r.review_status==='accepted').length} accepted</p>
      <a target="_blank" rel="noreferrer" href={`/api/paper-jobs/${selected.id}/assets/source.pdf`}>Open archived PDF</a>{' · '}
      {selected.status!=='running'&&selected.graph_run?.status!=='running'&&!Object.values(selected.vision_runs||{}).some(r=>r.status==='running')&&<a href={`/api/paper-jobs/${selected.id}/archive`}>Download complete archive ZIP</a>}
      {!!selected.page_count&&<GraphExtraction key={selected.id} job={selected} onChanged={setSelected} onOpen={onOpen}/>}
      {!!selected.page_count&&<VisualExtraction key={selected.id} job={selected} onChanged={setSelected} onOpen={onOpen}/>}
      {selected.status==='ready'&&selected.graph_run?.status!=='running'&&!Object.values(selected.vision_runs||{}).some(r=>r.status==='running')&&<div className="reference-panel"><h3>Automatic reported-value extraction</h3><p>One bounded call, max 8192 output tokens, no automatic retry. Full native paper text is transmitted to DeepSeek; page images are not. An output-token limit is not a price cap.</p>
        <button disabled={busy} onClick={()=>void act(async()=>setPacket(await request('/'+selected.id+'/request')))}>Preview exact request & requirements</button>
        {packet!==null&&<details><summary>Request JSON</summary><pre className="record-context">{JSON.stringify(packet,null,2)}</pre></details>}
        <label className="check"><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)}/>Send this paper's native text to DeepSeek and run extraction (may use API balance).</label>
        <button className="primary" disabled={busy||!consent} onClick={()=>void act(async()=>{setSelected(await request<Job>('/'+selected.id+'/run',{expected_revision:selected.revision,request_hash:selected.request_hash}));setConsent(false);})}>Run automatic reported-value extraction</button>
      </div>}
      {selected.status==='running'&&<p role="status">Extraction is running. You may close this panel; keep the local server running. A stopped/interrupted server does not automatically retry paid requests.</p>}
      {selected.error&&<p className="warning-text">{selected.error}</p>}
      {selected.status==='awaiting_review'&&!selected.table_evidence_run&&<button disabled={busy} onClick={()=>void act(async()=>setSelected(await request<Job>(`/${selected.id}/table-evidence`,{expected_revision:selected.revision})))}>Locate table cells locally · no model call</button>}
      {selected.table_evidence_run&&<p>Local table evidence: {selected.table_evidence_run.error||JSON.stringify(selected.table_evidence_run.summary)}. Matching cell expressions is not scientific approval.</p>}
      {selected.status==='awaiting_review'&&<ReportedMapping key={selected.id} mapping={selected.reported_mapping} revision={selected.revision} jobId={selected.id} disabled={busy} onMap={async(reference_id,paper_short_id)=>{
        setSelected(await request<Job>(`/${selected.id}/reported-mapping`,{reference_id,paper_short_id,expected_revision:selected.revision}));
      }}/>}
      <p>{selected.summary}</p>{selected.usage&&<p>API usage: {JSON.stringify(selected.usage)}</p>}
      {selected.records.map(r=><Candidate key={selected.id+':'+r.id+':'+selected.revision} job={selected} row={r} onSaved={setSelected}/>)}
      <h3>Deferred / unsupported tasks ({selected.deferred_items.length})</h3>{selected.deferred_items.map((d,i)=><p key={i}>p.{d.page} · {d.locator}: {d.reason}</p>)}
      <details><summary>Coverage limitations</summary>{[...selected.limitations,...(selected.model_limitations||[])].map((l,i)=><p key={i}>{l}</p>)}</details>
    </>}</div></div>{error&&<p role="alert" className="warning-text">{error}</p>}
  </section></div>;
}
