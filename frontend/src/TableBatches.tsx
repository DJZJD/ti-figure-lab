import React,{useEffect,useState} from 'react';

type Summary={source_values:number;mapped_drafts:number;exact_center_matches:number;outside_existing_targets:number;blocked_mappings:number;source_conflicts:number};
type Item={case_id:string;paper:string;table:string;status:string;summary?:Summary;error?:string};
type Row={extracted:{sample_label?:string;row_label?:string;definition:string;value_text:string;unit:string;uncertainty_text?:string;uncertainty_kind?:string;raw_row:string};mapping:{sample_ids:string[];target_path:string|null;status:string;reference_value?:number;reference_unit?:string;warnings?:string[]};source_conflict:boolean;scope:string;source_findings:Array<{conclusion?:string;finding:string}>};
type Preview={case_id:string;project_id:string;preview_hash:string;existing_project:boolean;source:{paper:string;table:string;page:number;interpretation_note:string};summary:Summary;records:Row[];raw_table_text:string;image_url:string};

async function request<T>(path:string,body?:unknown,signal?:AbortSignal):Promise<T>{
  const response=await fetch('/api/table-batches'+path,{method:body?'POST':'GET',signal,
    headers:{'X-TiFigure-Client':'workbench',...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});
  const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'The batch request failed.');return data;
}

export function TableBatches({onClose,onOpen}:{onClose:()=>void;onOpen:(id:string)=>Promise<void>}){
  const [items,setItems]=useState<Item[]>([]),[selected,setSelected]=useState('');
  const [preview,setPreview]=useState<Preview|null>(null),[error,setError]=useState('');
  const [loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[refresh,setRefresh]=useState(0);
  const [imported,setImported]=useState<{project_id:string;reused:boolean;revision:number}|null>(null);
  useEffect(()=>{const controller=new AbortController();
    request<{items:Item[]}>('',undefined,controller.signal).then(data=>{setItems(data.items);setSelected(s=>s||data.items.find(i=>i.status==='ready')?.case_id||'');setLoading(false);})
      .catch(e=>{if(e.name!=='AbortError'){setError(e.message);setLoading(false);}});return()=>controller.abort();},[refresh]);
  useEffect(()=>{setPreview(null);setImported(null);setError('');if(!selected)return;
    const controller=new AbortController();setLoading(true);
    request<Preview>('/'+selected,undefined,controller.signal).then(data=>{setPreview(data);setLoading(false);})
      .catch(e=>{if(e.name!=='AbortError'){setError(e.message);setLoading(false);}});return()=>controller.abort();},[selected,refresh]);
  async function importBatch(){if(!preview)return;setBusy(true);setError('');try{
    setImported(await request('/'+preview.case_id+'/import',{preview_hash:preview.preview_hash}));
  }catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  return <div className="modal-backdrop"><section className="modal batch-dialog" role="dialog" aria-modal="true" aria-labelledby="batch-title">
    <div className="section-row"><div><span className="eyebrow">LOCAL TABLE PIPELINE</span><h2 id="batch-title">Table batches</h2></div><button disabled={busy} onClick={onClose}>Close</button></div>
    <p className="muted">Inspect configured PDF table results, then import mapped values as pending records. Exact matches do not establish experimental truth. No model call is made here.</p>
    <div className="batch-layout"><nav aria-label="Table cases"><button disabled={busy||loading} onClick={()=>{setLoading(true);setRefresh(v=>v+1);}}>Refresh batches</button>
      {items.map(item=><button key={item.case_id} disabled={busy} aria-pressed={selected===item.case_id} onClick={()=>setSelected(item.case_id)}>
        <strong>{item.paper} · {item.table}</strong><small>{item.status==='ready'?`${item.summary?.mapped_drafts} mapped · ${item.summary?.source_conflicts} conflicts`:'Unavailable — inspect details'}</small></button>)}
      {!loading&&!items.length&&<p>No configured tables available.</p>}</nav>
      <div className="batch-content" aria-busy={loading||busy}>
        {loading&&<p role="status">Checking local source files and batch provenance…</p>}
        {error&&<p className="batch-warning" role="alert">{error}</p>}
        {preview&&!loading&&<>
          <h3>{preview.source.paper} · {preview.source.table} <span className="muted"> / PDF page {preview.source.page}</span></h3>
          <div className="batch-stats"><span><b>{preview.summary.source_values}</b> source values</span><span><b>{preview.summary.mapped_drafts}</b> mapped drafts</span><span><b>{preview.summary.outside_existing_targets}</b> outside scope</span><span><b>{preview.summary.source_conflicts}</b> source conflicts</span></div>
          {!!preview.summary.source_conflicts&&<p className="batch-warning">Source conflicts require a human decision. Import preserves both sources and does not select a winner.</p>}
          {!!preview.summary.blocked_mappings&&<p className="batch-warning">{preview.summary.blocked_mappings} mappings are blocked and will not be imported.</p>}
          <p className="muted">{preview.source.interpretation_note}</p>
          <div className="batch-actions"><button className="primary" disabled={busy||!preview.summary.mapped_drafts} onClick={()=>void importBatch()}>{busy?'Working…':preview.existing_project?'Use existing project':'Import pending project'}</button>
            <span className="muted">{preview.existing_project?'Existing project edits will be preserved.':'Only mapped drafts are imported. All review checks remain unset.'}</span></div>
          {imported&&<div className="batch-success" role="status"><span>{imported.reused?'Existing project preserved':'Pending project created'} · revision {imported.revision}</span><button disabled={busy} onClick={async()=>{setBusy(true);try{await onOpen(imported.project_id);}finally{setBusy(false);}}}>Open project in workbench</button></div>}
          <div className="batch-table-wrap"><table className="batch-table"><thead><tr><th>Sample / row</th><th>Quantity</th><th>Source value</th><th>Master value</th><th>Review status</th></tr></thead><tbody>
            {preview.records.map((row,index)=><tr key={index} className={row.source_conflict?'batch-conflict':''}>
              <td>{row.extracted.sample_label||row.extracted.row_label}<details><summary>Sample binding</summary>{row.mapping.sample_ids.join(', ')||'Unresolved'}</details></td>
              <td>{row.extracted.definition}</td>
              <td>{row.extracted.value_text}{row.extracted.uncertainty_text&&<> ± {row.extracted.uncertainty_text}</>} {row.extracted.unit}
                {row.extracted.uncertainty_text&&<small>Uncertainty: {row.extracted.uncertainty_kind||'unspecified'}</small>}</td>
              <td>{row.mapping.reference_value===undefined?'—':`${row.mapping.reference_value} ${row.mapping.reference_unit||''}`}</td>
              <td><strong>{row.source_conflict?'Source conflict':row.scope==='outside_existing_targets'?'Outside scope — not imported':row.mapping.status==='mapping_needs_review'?'Pending review':'Mapping blocked'}</strong>
                {row.source_findings.filter(f=>f.conclusion).map((f,j)=><p key={j}>{f.conclusion}</p>)}
                {row.mapping.warnings?.map((w,j)=><p key={j}>{w}</p>)}
                <details><summary>Source row & field</summary><p>{row.extracted.raw_row}</p><code>{row.mapping.target_path||'No target field'}</code></details></td>
            </tr>)}
          </tbody></table></div>
          <details className="batch-source"><summary>Original page image & extracted text</summary><img src={preview.image_url} alt={`${preview.source.paper} ${preview.source.table} source PDF page ${preview.source.page}`}/><pre>{preview.raw_table_text}</pre></details>
        </>}
      </div></div>
  </section></div>;
}
