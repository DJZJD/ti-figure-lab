import React,{useState} from 'react';
import {GraphMapping,Mapping} from './GraphMapping';

type GraphPoint={id:string;x:number;y:number;sample_label:string;curve_label:string;flags:string[]};
type GraphPanel={id:string;page:number;locator:string;status:string;error:string|null;crop?:string;overlay?:string;project_id?:string;mapping?:Mapping;refinement?:{status:string;calls_attempted?:number;error?:string};
  result?:{pixel_refinement?:boolean;points:GraphPoint[];calibration:{x:{unit:string};y:{unit:string}};model_warnings:string[];tick_checks:Record<string,{pixel_residual:number}>}};
export type GraphRun={id:string;status:string;objective:string;calls_attempted:number;max_calls:number;active_stage?:string;panels:GraphPanel[];deferred:string[];error:string|null;notice:string};
type GraphJob={id:string;revision:number;status:string;graph_run?:GraphRun};
async function post<T>(path:string,body:unknown):Promise<T>{
  const r=await fetch('/api/paper-jobs'+path,{method:'POST',headers:{'X-TiFigure-Client':'workbench','Content-Type':'application/json'},body:JSON.stringify(body)});
  const result=await r.json();if(!r.ok)throw new Error(typeof result.detail==='string'?result.detail:'Invalid graph request.');return result;
}

export function GraphExtraction<T extends GraphJob>({job,onChanged,onOpen}:{job:T;onChanged:(j:T)=>void;onOpen:(id:string)=>Promise<void>}){
  const [objective,setObjective]=useState('Extract visible data marker centers from one clear material-property graph. Identify its axis ticks, units, curve labels and sample conditions. Defer ambiguous plots.');
  const [limit,setLimit]=useState(1),[consent,setConsent]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const graph=job.graph_run;
  async function act(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  return <section className="paper-graph-section"><h3>Automatic graph extraction</h3>
    <p>Select relevant figures from text → locate panels on page images → read ticks and marker centers from crops → calculate coordinates locally → open an editable review workspace.</p>
    <p className="hint">Experimental: single unbroken linear X/Y axes and visible markers only. This branch does not trace continuous curves. Use the separate micrograph / XRD branch for visual proposals. Matching a third tick checks consistency, not scientific accuracy.</p>
    {!graph&&<fieldset disabled={busy||job.status==='running'}>
      <label>Graph objective<textarea aria-label="Graph extraction objective" rows={3} value={objective} maxLength={1500} onChange={e=>{setObjective(e.target.value);setConsent(false);}}/></label>
      <label>Maximum panels<select value={limit} onChange={e=>{setLimit(Number(e.target.value));setConsent(false);}}>{[1,2,3].map(n=><option key={n} value={n}>{n}</option>)}</select></label>
      <p>Up to {1+2*limit} DeepSeek calls, each capped at 8192 output tokens, no retries. The first call sends full native text; later calls send only selected page images / target crops and associated text. Token limits are not a total cost cap.</p>
      <label className="check"><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)}/>Send this paper's text and automatically selected images to DeepSeek for this bounded graph run.</label>
      <button className="primary" disabled={!consent||objective.trim().length<5} onClick={()=>void act(async()=>{onChanged(await post<T>(`/${job.id}/graphs/run`,{expected_revision:job.revision,objective,max_panels:limit}));setConsent(false);})}>Run automatic graph extraction</button>
    </fieldset>}
    {graph&&<><p role="status"><strong>{graph.status}</strong> · Initial run: {graph.calls_attempted}/{graph.max_calls} calls attempted {graph.active_stage?'· '+graph.active_stage:''}</p><p>{graph.panels.some(p=>p.result?.pixel_refinement)?'Original model geometry and subsequent pixel refinements are retained separately. All results require review.':graph.notice}</p>
      {graph.error&&<p className="warning-text">{graph.error}</p>}
      {graph.panels.map(p=><div className="paper-candidate" key={p.id}><h4>p.{p.page} · {p.locator} · {p.status}</h4>
        {p.error&&<p className="warning-text">{p.error}</p>}
        {!p.refinement&&graph.status!=='running'&&<><p>Optional pixel refinement: detects dark rectangular frames and colored hollow markers locally, then sends the crop and numbered candidates in one model call to read labels. Unsupported images may be deferred.</p><button disabled={busy} onClick={()=>void act(async()=>{onChanged(await post<T>(`/${job.id}/graphs/${p.id}/refine`,{}));})}>Refine with local pixels · 1 DeepSeek call</button></>}
        {p.refinement&&<p className="hint">Pixel refinement: {p.refinement.status} · {p.refinement.calls_attempted??0} additional call(s) attempted. {p.refinement.error||'Original proposal retained; refined values still require review.'}</p>}
        {p.result&&<>
          <a href={`/api/paper-jobs/${job.id}/assets/${p.crop}`} target="_blank" rel="noreferrer">Open original crop</a>{' · '}
          <a href={`/api/paper-jobs/${job.id}/assets/${p.overlay}`} target="_blank" rel="noreferrer">Open geometry overlay</a>
          <img className="graph-overlay" src={`/api/paper-jobs/${job.id}/assets/${p.overlay}`} alt="Unreviewed graph proposal: blue reference ticks and red marker centers"/>
          <p>Blue: reference ticks · Red: proposed marker centers. {p.result.pixel_refinement?'Positions come from local pixels; the model assigns tick values and labels.':'Marker positions come from the model.'} Derived X/Y values are computed locally; all points require review.</p>
          <div className="graph-point-table"><table><thead><tr><th>Marker</th><th>Sample / curve</th><th>X ({p.result.calibration.x.unit})</th><th>Y ({p.result.calibration.y.unit})</th></tr></thead><tbody>{p.result.points.map(r=><tr key={r.id}><td>{r.id}</td><td>{r.sample_label||'Unresolved'} / {r.curve_label}</td><td>{Number(r.x.toPrecision(5))}</td><td>{Number(r.y.toPrecision(5))}</td></tr>)}</tbody></table></div>
          {p.result.model_warnings.map((w,i)=><p className="warning-text" key={i}>{w}</p>)}
          {p.result.points.some(r=>r.flags.length>0)&&<p className="warning-text">Some sample bindings are unresolved. Review them before accepting any measurement.</p>}
          <p>Third-tick residuals: {Object.entries(p.result.tick_checks).map(([axis,c])=>`${axis.toUpperCase()} ${c.pixel_residual.toFixed(2)} crop px`).join(' · ')}. This is not a measurement-error estimate.</p>
          <button disabled={busy||graph.status==='running'} onClick={()=>void act(async()=>{const r=await post<{project_id:string;job:T}>(`/${job.id}/graphs/${p.id}/open`,{});onChanged(r.job);await onOpen(r.project_id);})}>{p.project_id?'Reopen review workspace':'Open points for review / correction'}</button>
          <p className="hint">Opening preserves the automatic proposal and creates a separate pending workspace. Later opens retain your edits. Save workspace changes; the paper archive includes its saved revisions.</p>
          <GraphMapping mapping={p.mapping} disabled={busy||!p.project_id} onMap={async(reference_id,paper_short_id)=>{
            const response=await fetch('/api/projects/'+p.project_id);if(!response.ok)throw new Error('Reopen the saved workspace first.');
            const workspace=await response.json();
            const mapped=await post<{job:T;proposal:Mapping;saved:boolean}>(`/${job.id}/graphs/${p.id}/mapping`,{reference_id,paper_short_id,expected_revision:workspace.revision,time_tolerance_h:0.02});
            onChanged(mapped.job);if(!mapped.saved)throw new Error('No unique supported target found. Check conditions, units and existing samples; nothing was saved.');
          }}/>
        </>}
      </div>)}
      {graph.deferred.map((d,i)=><p className="warning-text" key={i}>{d}</p>)}
      {graph.status!=='running'&&<p>To explicitly retry with another objective, import the PDF as a new job. This preserves the original run and avoids automatic paid retries.</p>}
    </>}
    {error&&<p role="alert" className="warning-text">{error}</p>}
  </section>;
}
