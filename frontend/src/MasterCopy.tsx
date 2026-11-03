import React,{useEffect,useState} from 'react';

type ExportRow={key:string;kind:string;label:string;path:string|null;sample_ids:string[];old_value:number|null;new_value:number|null;unit:string|null;eligible:boolean;blockers:string[];retained_metadata:unknown};
type Preview={preview_hash:string;paper:string;revision:number;rows:ExportRow[];summary:{total:number;eligible:number;blocked:number;known_conflicting_fields:number};notice:string};
type Choice={reason:string;retained_metadata_checked:boolean};
type Exported={path:string;adopted_fields:number;changed_values:number};

async function call<T>(projectId:string,body?:unknown):Promise<T>{
  const response=await fetch(`/api/projects/${projectId}/${body?'master-copy':'master-copy-preview'}`,{
    method:body?'POST':'GET',headers:{'X-TiFigure-Client':'workbench',...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});
  const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Cannot export this selection.');return data;
}

export function MasterCopy({projectId,onClose}:{projectId:string;onClose:()=>void}){
  const [preview,setPreview]=useState<Preview|null>(null),[choices,setChoices]=useState<Record<string,Choice>>({});
  const [reviewer,setReviewer]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[exported,setExported]=useState<Exported|null>(null);
  async function refresh(){setBusy(true);setError('');setPreview(null);setChoices({});setExported(null);
    try{setPreview(await call<Preview>(projectId));}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  useEffect(()=>{void refresh();},[projectId]);
  const valid=!!reviewer.trim()&&Object.values(choices).every(c=>c.reason.trim()&&c.retained_metadata_checked);
  async function exportCopy(){if(!preview||!valid)return;setBusy(true);setError('');try{
    setExported(await call<Exported>(projectId,{preview_hash:preview.preview_hash,reviewer:reviewer.trim(),
      selections:Object.entries(choices).map(([key,c])=>({key,...c}))}));
  }catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  return <div className="modal-backdrop"><section className="modal batch-dialog" role="dialog" aria-modal="true" aria-labelledby="master-copy-title">
    <div className="section-row"><div><span className="eyebrow">EXPLICIT ADOPTION</span><h2 id="master-copy-title">Export a TiFM master copy</h2></div><button disabled={busy} onClick={onClose}>Close</button></div>
    <p className="batch-warning">The original master will not be changed. Only selected, reviewed scalar values can replace existing fields. Unselected original values — including known conflicts — remain in the copy and are not newly validated. Keep the audit and pending-review files with the copy.</p>
    {error&&<p role="alert" className="batch-warning">{error}</p>}
    {busy&&!preview&&<p role="status">Checking saved project, reference and source conflicts…</p>}
    <button disabled={busy} onClick={()=>void refresh()}>Refresh saved-project preview</button>
    {preview&&<>
      <p>{preview.paper} · saved revision {preview.revision} · {preview.summary.eligible} eligible results · {preview.summary.blocked} blocked · {preview.summary.known_conflicting_fields} known conflicting fields</p>
      {!preview.summary.eligible&&<p className="muted">No results are currently eligible. Review evidence, reported values or measurement mappings in the workbench first. You can still export an unchanged reference copy with pending-review attachments.</p>}
      <label className="master-reviewer">Reviewer name / local identifier<input aria-label="Export reviewer" maxLength={200} value={reviewer} disabled={busy} onChange={e=>setReviewer(e.target.value)}/></label>
      <div className="batch-table-wrap"><table className="batch-table"><thead><tr><th>Adopt</th><th>Result & field</th><th>Existing → proposed</th><th>Checks / adoption reason</th></tr></thead><tbody>
        {preview.rows.map(row=><tr key={row.key}>
          <td><input type="checkbox" aria-label={`Adopt ${row.key}`} disabled={busy||!row.eligible} checked={!!choices[row.key]} onChange={e=>{
            const checked=e.target.checked;setChoices(old=>{const next={...old};if(checked)next[row.key]={reason:'',retained_metadata_checked:false};else delete next[row.key];return next;});setExported(null);}}/></td>
          <td><strong>{row.label}</strong><p>{row.kind} · {row.sample_ids.join(', ')}</p><code>{row.path||'Unmapped'}</code>
            <details><summary>Metadata retained unchanged</summary><pre className="record-context">{JSON.stringify(row.retained_metadata,null,2)}</pre></details></td>
          <td>{row.old_value??'—'} → {row.new_value??'—'} {row.unit||''}</td>
          <td>{!!row.blockers.length&&<><p className="warning-text">{row.blockers.find(b=>b.startsWith('Registered source conflict'))||row.blockers[0]}</p><details><summary>All blockers ({row.blockers.length})</summary>{row.blockers.map((b,i)=><p key={i}>{b}</p>)}</details></>}
            {row.eligible&&!choices[row.key]&&<span>Review is current. Adoption is a separate decision.</span>}
            {choices[row.key]&&<div className="master-adoption"><label>Why adopt this result?<textarea aria-label={`Reason for ${row.key}`} maxLength={2000} disabled={busy} value={choices[row.key].reason} onChange={e=>{const reason=e.target.value;setChoices(old=>({...old,[row.key]:{...old[row.key],reason}}));setExported(null);}}/></label>
              <label className="check"><input type="checkbox" aria-label={`Retained metadata checked for ${row.key}`} disabled={busy} checked={choices[row.key].retained_metadata_checked} onChange={e=>{const checked=e.target.checked;setChoices(old=>({...old,[row.key]:{...old[row.key],retained_metadata_checked:checked}}));setExported(null);}}/>The existing source, sample/test conditions, statistics and uncertainty metadata still apply. This value has no unresolved source conflict.</label>
              <p className="muted">If any retained metadata needs editing, leave this result unselected. This adapter changes numeric values only.</p></div>}
          </td></tr>)}
      </tbody></table></div>
      <div className="batch-actions"><button className="primary" disabled={busy||!valid} onClick={()=>void exportCopy()}>{busy?'Working…':Object.keys(choices).length?`Export copy with ${Object.keys(choices).length} selected results`:'Export unchanged copy + attachments'}</button></div>
      <p className="muted">ZIP contents: master-copy.json, original-master.json, adoption-audit.json, pending-review.json and NOTICE.txt. Structural readback is checked; the downstream training reader and full TiFM schema have not been validated.</p>
      {exported&&<div className="batch-success" role="status"><div><strong>Bundle saved locally</strong><p>{exported.adopted_fields} adopted fields · {exported.changed_values} numeric changes</p><code>{exported.path}</code></div></div>}
    </>}
  </section></div>;
}
