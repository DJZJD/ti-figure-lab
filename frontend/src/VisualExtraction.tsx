import React,{useState} from 'react';

type Panel={candidate_overlay?:string;region_candidates?:{candidates:{id:string;area_px2:number}[]};local_recompute?:{status:string;error?:string};remeasure?:{status:string;error?:string};id:string;page:number;locator:string;status:string;error?:string;crop?:string;overlay?:string;project_id?:string;result?:{calibration_check?:{image_first_candidates?:{id:string;x:number;y:number;two_theta_deg:number}[]};measurements:{id:string;values:Record<string,number>;units:Record<string,string>}[];provenance:{id:string;method:string;warnings:string[]}[];warnings:string[];deferred:{id:string;reason:string}[]}};

export type VisualRun={id:string;kind:string;status:string;calls_attempted:number;max_calls:number;panels:Panel[];deferred:string[];error?:string};

type VisualJob={id:string;revision:number;status:string;vision_runs?:Record<string,VisualRun>;graph_run?:{status:string}};

const objectives={micrograph:'Find one clear micrograph with a readable scale bar. Propose individual visible object regions, their sample context and identity evidence. Exclude inset scales, labels and overlapping objects. Do not infer phase identity from brightness alone.',xrd:'Find one XRD panel. Identify each trace from this paper’s legend and context, including stacked traces. Propose visible 2-theta peak positions with curve/sample labels. Defer overlaps or unclear peaks; do not measure arbitrary vertical offsets.'};

export function VisualExtraction<T extends VisualJob>({job,onChanged,onOpen}:{job:T;onChanged:(j:T)=>void;onOpen:(id:string)=>Promise<void>}){

  const [kind,setKind]=useState<'micrograph'|'xrd'>('micrograph'),[objective,setObjective]=useState(objectives.micrograph),[limit,setLimit]=useState(1);

  const [consent,setConsent]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');

  const [regions,setRegions]=useState<Record<string,string[]>>({});
  const running=job.status==='running'||job.graph_run?.status==='running'||Object.values(job.vision_runs||{}).some(r=>r.status==='running');

  async function post<R>(path:string,body:unknown):Promise<R>{const r=await fetch(`/api/paper-jobs/${job.id}/vision${path}`,{method:'POST',headers:{'Content-Type':'application/json','X-TiFigure-Client':'workbench'},body:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:'Invalid visual request.');return d;}

  async function act(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}

  return <section className="paper-graph-section"><h3>Automatic micrograph / XRD extraction · Experimental</h3>

    <p>LLM selects panels, calibration and objects or peaks. Local tools propose gray-region contours or nearby apex positions and calculate values. Every result opens in the editable review workspace. No model training.</p>

    <fieldset disabled={busy||running}><label>Visual task<select value={kind} onChange={e=>{const k=e.target.value as typeof kind;setKind(k);setObjective(objectives[k]);setConsent(false);}}><option value="micrograph">Micrograph regions and areas</option><option value="xrd">XRD peak positions</option></select></label>

      {!job.vision_runs?.[kind]&&<><label>Visual extraction objective<textarea rows={3} value={objective} maxLength={1500} onChange={e=>{setObjective(e.target.value);setConsent(false);}}/></label>

      <label>Visual panel limit<select value={limit} onChange={e=>{setLimit(Number(e.target.value));setConsent(false);}}>{[1,2,3].map(n=><option key={n} value={n}>{n}</option>)}</select></label>

      <p>Up to {1+(kind==='xrd'?3:2)*limit} DeepSeek calls, at most 8192 output tokens each, no automatic retries. Sends full native text, then selected page images and crops. This is not a total cost cap.</p>

      <label className="check"><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)}/>Send this paper's text and selected images to DeepSeek for this visual run.</label>

      <button disabled={!consent||objective.trim().length<5} onClick={()=>void act(async()=>{onChanged(await post<T>('/run',{expected_revision:job.revision,kind,objective,max_panels:limit}));setConsent(false);})}>Run automatic {kind==='xrd'?'XRD':'micrograph'} extraction</button></>}

    </fieldset>

    <p className="hint">Model outlines and unsnapped peaks remain explicit model proposals. Region area is 2D, not phase fraction or population grain size. XRD extracts positions only, not intensity, phase identification or crystallite size. Missing source detail cannot be recovered.</p>

    {Object.values(job.vision_runs||{}).map(run=><div key={run.id}><h4>{run.kind} · {run.status}</h4><p>{run.calls_attempted} / {run.max_calls} calls attempted. Keep the local server running.</p>

      {run.error&&<p className="warning-text">{run.error}</p>}

      {run.panels.map(p=><div className="paper-candidate" key={p.id}><h4>p.{p.page} · {p.locator} · {p.status}</h4>{p.error&&<p className="warning-text">{p.error}</p>}

        <button disabled={busy||running} onClick={()=>void act(async()=>{const r=await post<{job:T;project_id:string}>(`/${run.kind}/${p.id}/open-source`,{});onChanged(r.job);await onOpen(r.project_id);})}>Open archived crop for manual fallback</button>

        {p.remeasure?<p>Crop remeasurement: {p.remeasure.status} {p.remeasure.error||''}</p>:<button disabled={busy||running} onClick={()=>void act(async()=>onChanged(await post<T>(`/${run.kind}/${p.id}/remeasure`,{expected_revision:job.revision})))}>Remeasure archived crop with grid · 1 DeepSeek call</button>}

        {(p.local_recompute?<p>Local calibration recomputation: {p.local_recompute.status} {p.local_recompute.error||''}</p>:<button disabled={busy||running} onClick={()=>void act(async()=>onChanged(await post<T>(`/${run.kind}/${p.id}/recompute`,{expected_revision:job.revision})))}>Refine {run.kind==='xrd'?'X calibration':'scale bar'} locally · no model call</button>)}

        {run.kind==='micrograph'&&!p.candidate_overlay&&<button disabled={busy||running} onClick={()=>void act(async()=>onChanged(await post<T>(`/micrograph/${p.id}/propose-regions`,{expected_revision:job.revision})))}>Find local regions in archived ROI - no model call</button>}
        {p.candidate_overlay&&<details><summary>Independent region candidates - pixel geometry only</summary><p>No phase or physical scale has been validated. These are segmentation proposals, not accepted measurements.</p><img className="graph-overlay" alt="Unclassified local region candidates" src={`/api/paper-jobs/${job.id}/assets/${p.candidate_overlay}`}/><table><tbody>{p.region_candidates?.candidates.map(c=><tr key={c.id}><td><label><input type="checkbox" checked={(regions[p.id]||[]).includes(c.id)} onChange={e=>setRegions(v=>({...v,[p.id]:e.target.checked?[...(v[p.id]||[]),c.id]:(v[p.id]||[]).filter(id=>id!==c.id)}))}/>{c.id}</label></td><td>{c.area_px2.toFixed(1)} px²</td><td>Pending</td></tr>)}</tbody></table><button disabled={busy||running||!(regions[p.id]?.length)} onClick={()=>void act(async()=>{const r=await post<{job:T;project_id:string}>(`/micrograph/${p.id}/select-regions`,{expected_revision:job.revision,candidate_ids:regions[p.id]});onChanged(r.job);await onOpen(r.project_id);})}>Edit selected regions and calibrate scale</button><p>No candidates are selected automatically. Unselected candidates remain in the archive. Mark the original scale bar in the workspace before using physical areas.</p></details>}
        {p.result&&<><a target="_blank" rel="noreferrer" href={`/api/paper-jobs/${job.id}/assets/${p.crop}`}>Open original crop</a>

          <img className="graph-overlay" alt="Pending visual proposals: blue calibration, red objects or peaks" src={`/api/paper-jobs/${job.id}/assets/${p.overlay}`}/>

          {p.result.calibration_check?.image_first_candidates&&<details><summary>Independent pixel candidates  {p.result.calibration_check.image_first_candidates.length}  unassigned</summary><p>These candidates were located without model peak coordinates. Text and intersections may cause false positives. Verify before using.</p><img className="graph-overlay" alt="Independent pixel peak candidates" src={`/api/paper-jobs/${job.id}/assets/${p.crop?.replace(/crop\.png$/, 'candidates.png')}`}/><table><tbody>{p.result.calibration_check.image_first_candidates.map(c=><tr key={c.id}><td>{c.id}</td><td>{c.two_theta_deg.toFixed(3)} deg</td><td>Pending  no sample assigned</td></tr>)}</tbody></table></details>}

          <p>Blue: calibration · Red: proposed geometry. Check the source and every curve/object identity.</p>

          <table><thead><tr><th>Object / peak</th><th>Local result</th><th>Geometry source</th></tr></thead><tbody>{p.result.measurements.map(m=><tr key={m.id}><td>{m.id}</td><td>{Object.entries(m.values).map(([k,v])=>`${k}: ${Number(v.toPrecision(5))} ${m.units[k]||''}`).join(' · ')}</td><td>{p.result!.provenance.find(v=>v.id===m.id)?.method}</td></tr>)}</tbody></table>

          <details><summary>Warnings and deferred objects</summary>{p.result.warnings.map((s,i)=><p key={i}>{s}</p>)}{p.result.provenance.map(v=><p key={v.id}>{v.id}: {v.warnings.join(' ')}</p>)}{p.result.deferred.map(d=><p key={d.id}>{d.id}: {d.reason}</p>)}</details>

          <button disabled={busy||running} onClick={()=>void act(async()=>{const r=await post<{job:T;project_id:string}>(`/${run.kind}/${p.id}/open`,{});onChanged(r.job);await onOpen(r.project_id);})}>{p.project_id?'Reopen visual review workspace':'Open for review / correction'}</button>

        </>}

      </div>)}{run.deferred.map((s,i)=><p className="warning-text" key={i}>{s}</p>)}

    </div>)}{error&&<p role="alert" className="warning-text">{error}</p>}

  </section>;

}

