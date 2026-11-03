import React,{useState} from 'react';
export type Mapping={proposed_fields:number;rows:{measurement_id:string;status:string;digitized_time_h?:number;value?:number;unit?:string;sample_ids?:string[];target_path?:string|null;reference_value?:unknown;source_conflicts?:unknown[]}[]};
export function GraphMapping({mapping,disabled,onMap}:{mapping?:Mapping;disabled:boolean;onMap:(id:string,paper:string)=>Promise<void>}){
  const [file,setFile]=useState<File|null>(null),[paper,setPaper]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
  async function run(){if(!file)return;setBusy(true);setError('');try{
    const body=new FormData();body.append('file',file);
    const r=await fetch('/api/references',{method:'POST',headers:{'X-TiFigure-Client':'workbench'},body});
    const data=await r.json();if(!r.ok)throw new Error(data.detail||'Reference import failed.');
    await onMap(data.id,paper.trim());
  }catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  return <section><h4>TiFM condition mapping</h4>
    <p>Local suggestions for Aging time / KIC graphs. Matches solution and aging conditions, never the nearest property value. Cooling, alloy identity and test conditions still require review. No model call.</p>
    {!mapping&&<fieldset disabled={disabled||busy}>
      <label>Reference master JSON<input type="file" accept=".json" onChange={e=>setFile(e.target.files?.[0]||null)}/></label>
      <label>Confirm this paper's short ID<input value={paper} placeholder="e.g. PAPER_A" onChange={e=>setPaper(e.target.value)}/></label>
      <p>Open the review workspace first. This adds pending supplements to its saved version. Save any edits before continuing. Time matching tolerance: ±0.02 h; measured X values remain unchanged.</p>
      <button disabled={!file||!paper.trim()} onClick={()=>void run()}>{busy?'Preparing…':'Prepare pending TiFM mappings'}</button>
    </fieldset>}
    {mapping&&<><p>{mapping.proposed_fields} pending field suggestions. Snapshot at preparation time; reopen the workspace for current values and review.</p>
      <table><thead><tr><th>Point</th><th>Time (h)</th><th>Graph value</th><th>Sample / target</th><th>Existing master</th><th>Status</th></tr></thead>
        <tbody>{mapping.rows.map(r=><tr key={r.measurement_id}><td>{r.measurement_id}</td><td>{r.digitized_time_h?.toFixed(4)}</td><td>{r.value?.toFixed(3)} {r.unit}</td><td>{r.sample_ids?.join(', ')}<br/>{r.target_path}</td><td>{r.reference_value==null?'—':String(r.reference_value)}</td><td>{r.status}{r.source_conflicts?.length?' · Source conflict; replacement blocked':''}</td></tr>)}</tbody></table></>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}
