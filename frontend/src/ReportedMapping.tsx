import React,{useState} from 'react';
export type ReportedMap={saved_revision:number;summary:Record<string,number>;rows:{record_id:string;sample_label:string;quantity:string;raw_value:string;unit:string;status:string;sample_id?:string;target_path?:string;reference_value?:unknown;reference_unit?:string;conditions:string;missing_checks:string[];reason?:string}[]};
export function ReportedMapping({mapping,revision,jobId,disabled,onMap}:{mapping?:ReportedMap;revision:number;jobId:string;disabled:boolean;onMap:(reference:string,paper:string)=>Promise<void>}){
  const [file,setFile]=useState<File|null>(null),[paper,setPaper]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
  async function run(){if(!file)return;setBusy(true);setError('');try{
    const body=new FormData();body.append('file',file);
    const response=await fetch('/api/references',{method:'POST',headers:{'X-TiFigure-Client':'workbench'},body});
    const ref=await response.json();if(!response.ok)throw new Error(ref.detail||'Reference import failed.');
    await onMap(ref.id,paper.trim());
  }catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  const stale=!!mapping&&mapping.saved_revision!==revision;
  return <section className="reference-panel"><h3>Reported values → TiFM field suggestions</h3>
    <p>Match sample labels, annealing/cooling conditions and quantities locally. Reference values never choose a target. Limited to supported mechanical tables; no model call and no master replacement.</p>
    {(!mapping||stale)&&<fieldset disabled={disabled||busy}>
      {stale&&<p className="warning-text">The job changed. Prepare a new mapping snapshot before downloading; previous versions remain archived.</p>}
      <label>Reported-value reference master JSON<input type="file" accept=".json" onChange={e=>setFile(e.target.files?.[0]||null)}/></label>
      <label>Confirm source paper short ID<input value={paper} placeholder="e.g. PAPER_A" onChange={e=>setPaper(e.target.value)}/></label>
      <button disabled={!file||!paper.trim()} onClick={()=>void run()}>{busy?'Preparing…':'Prepare pending reported-field mappings'}</button>
    </fieldset>}
    {mapping&&<><p>{Object.entries(mapping.summary).map(([k,v])=>`${k}: ${v}`).join(' · ')}. All links require review.</p>
      {!stale&&<a href={`/api/paper-jobs/${jobId}/reported-mapping/preview`}>Download pending field preview JSON</a>}
      <p>Preview includes original ± expressions, cell evidence, reference context and unresolved checks. It contains no applicable patch and is not a final master or training dataset.</p>
      <details><summary>Inspect field mappings ({mapping.rows.length})</summary>
        {mapping.rows.map(r=><div className="paper-candidate" key={r.record_id}><strong>{r.sample_label} · {r.quantity} · {r.status}</strong>
          <p>{r.raw_value} {r.unit} → Existing master: {r.reference_value==null?'—':String(r.reference_value)} {r.reference_unit}</p>
          <p>{r.sample_id||'No unique sample'}<br/>{r.target_path||'No unique target'}</p><p>Conditions: {r.conditions}</p>
          {r.reason&&<p>{r.reason}</p>}<p>Still to check: {r.missing_checks.join('; ')}.</p></div>)}
      </details></>}
    {error&&<p role="alert" className="warning-text">{error}</p>}
  </section>;
}
