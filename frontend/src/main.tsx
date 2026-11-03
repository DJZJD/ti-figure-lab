import React,{useCallback,useEffect,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Doc,Point,Tool,Measurement,ImageMeta,Evaluation,Envelope,Axis,newDoc,ReferenceIndex,PeakSuggestion,RegionSuggestion,ExtractionPlan} from './types';
import {ObjectSummary} from './ObjectSummary';
import {CandidateReview} from './CandidateReview';
import {EvidencePlans} from './EvidencePlans';
import {ReportedValues} from './ReportedValues';
import {LLMPlanner} from './LLMPlanner';
import {LLMConnection} from './LLMConnection';
import {TableBatches} from './TableBatches';
import {PaperJobs} from './PaperJobs';
import {MasterCopy} from './MasterCopy';
import {newReported} from './types';
import './style.css';

async function api<T>(path:string,method='GET',body?:unknown,signal?:AbortSignal):Promise<T>{
  const headers:Record<string,string>={'X-TiFigure-Client':'workbench'};
  if(body && !(body instanceof FormData))headers['Content-Type']='application/json';
  const response=await fetch('/api'+path,{method,headers,body:body instanceof FormData?body:body?JSON.stringify(body):undefined,signal});
  if(!response.ok){const err=await response.json().catch(()=>({detail:'The local service did not respond.'}));
    throw new Error(typeof err.detail==='string'?err.detail:err.detail?.map((e:any)=>e.msg).join('; ')||'Request failed.');}
  return response.json();
}
const clone=<T,>(value:T):T=>structuredClone(value);
const format=(n:number)=>Number.isFinite(n)?n.toLocaleString('en-US',{maximumFractionDigits:4}):'—';
const same=(a:unknown,b:unknown)=>JSON.stringify(a)===JSON.stringify(b);
function NumberField({value,onChange,label}:{value:number;onChange:(v:number)=>void;label:string}){
  const [text,setText]=useState(String(value));
  useEffect(()=>setText(String(value)),[value]);
  return <input aria-label={label} type="number" step="any" value={text} onChange={e=>setText(e.target.value)} onBlur={()=>{
    if(text.trim()!==''&&Number.isFinite(Number(text)))onChange(Number(text));else setText(String(value));
  }} onKeyDown={e=>{if(e.key==='Enter')e.currentTarget.blur();}}/>;
}

function App(){
  const [doc,setDoc]=useState<Doc|null>(null),[image,setImage]=useState<ImageMeta|null>(null);
  const [tool,setTool]=useState<Tool>('select'),[draft,setDraft]=useState<Point[]>([]),[selected,setSelected]=useState<string|null>(null);
  const [results,setResults]=useState<Evaluation|null>(null),[evaluating,setEvaluating]=useState(false);
  const [zoom,setZoom]=useState(1),[fitWidth,setFitWidth]=useState(700),[showMarks,setShowMarks]=useState(true);
  const [fitHeight,setFitHeight]=useState(450);
  const [selectedReport,setSelectedReport]=useState('');
  const [showTableBatches,setShowTableBatches]=useState(false);
  const [showPaperJobs,setShowPaperJobs]=useState(false);
  const [masterCopyProject,setMasterCopyProject]=useState<string|null>(null);
  const [error,setError]=useState(''),[message,setMessage]=useState('Local workspace. Explicit model planning or paper extraction calls transmit the selected content.');
  const [busy,setBusy]=useState(false),[dirty,setDirty]=useState(false),[projectId,setProjectId]=useState<string|null>(null),[revision,setRevision]=useState(0);
  const [historyTick,setHistoryTick]=useState(0),[storage,setStorage]=useState('Local storage');
  const [projectList,setProjectList]=useState<Array<{id:string;name:string;revision:number;updated_at:string}>|null>(null);
  const [dragPoint,setDragPoint]=useState<Point|null>(null),[cursor,setCursor]=useState<Point|null>(null);
  const history=useRef<Doc[]>([]),future=useRef<Doc[]>([]),svg=useRef<SVGSVGElement>(null),stage=useRef<HTMLDivElement>(null),file=useRef<HTMLInputElement>(null);
  const savedDocument=useRef<Doc|null>(null);
  const drag=useRef<{id:string;index:number}|null>(null);
  const referenceFile=useRef<HTMLInputElement>(null);
  const [reference,setReference]=useState<ReferenceIndex|null>(null),[referenceSearch,setReferenceSearch]=useState('');
  const [peaks,setPeaks]=useState<PeakSuggestion|null>(null),[peakRadius,setPeakRadius]=useState(40);
  const [region,setRegion]=useState<RegionSuggestion|null>(null),[regionRadius,setRegionRadius]=useState(80),[regionTolerance,setRegionTolerance]=useState(24);

  useEffect(()=>{
    setReference(null);
    if(!doc?.reference_id)return;
    const controller=new AbortController();
    api<ReferenceIndex>('/references/'+doc.reference_id,'GET',undefined,controller.signal).then(setReference).catch(e=>{if(e.name!=='AbortError')setError(e.message);});
    return()=>controller.abort();
  },[doc?.reference_id]);

  useEffect(()=>{api<{storage:string}>('/health').then(r=>setStorage(r.storage)).catch(e=>setError(e.message));},[]);
  useEffect(()=>{if(!stage.current)return; const el=stage.current;const observer=new ResizeObserver(()=>{setFitWidth(Math.max(120,el.clientWidth-48));setFitHeight(Math.max(120,el.clientHeight-48));});observer.observe(el);return()=>observer.disconnect();},[image]);
  useEffect(()=>{const handler=(e:BeforeUnloadEvent)=>{if(dirty){e.preventDefault();e.returnValue='';}};window.addEventListener('beforeunload',handler);return()=>window.removeEventListener('beforeunload',handler);},[dirty]);
  useEffect(()=>{
    if(!doc){setResults(null);return;}
    const controller=new AbortController();setEvaluating(true);
    const timer=setTimeout(()=>api<Evaluation>('/evaluate','POST',doc,controller.signal).then(r=>{setResults(r);setEvaluating(false);}).catch(e=>{
      if(e.name!=='AbortError'){setError(e.message);setResults(null);setEvaluating(false);}
    }),100);
    return()=>{clearTimeout(timer);controller.abort();};
  },[doc]);

  function commit(next:Doc,keepMessage=false){
    if(!doc||same(doc,next))return;
    const graphChanged=!same(doc.graph,next.graph),spatialChanged=!same(doc.spatial,next.spatial),sourceChanged=doc.source!==next.source;
    const old=new Map(doc.measurements.map(m=>[m.id,m]));
    for(const m of next.measurements){const before=old.get(m.id);
      if(m.plan_id){const context=(d:Doc)=>{const p=d.plans?.find(p=>p.id===m.plan_id);return {p,e:d.evidence?.filter(e=>p?.evidence_ids.includes(e.id))};};if(!same(context(doc),context(next)))m.status='needs_review';}
      if(sourceChanged||doc.reference_id!==next.reference_id||(m.kind==='point'?graphChanged:spatialChanged)||(before&&(!same(before.points,m.points)||before.sample!==m.sample||before.label!==m.label||before.note!==m.note||before.reference_path!==m.reference_path||before.group_label!==m.group_label)))m.status='needs_review';}
    history.current.push(clone(doc));if(history.current.length>80)history.current.shift();future.current=[];
    setDoc(next);setDirty(!same(next,savedDocument.current));setEvaluating(true);setHistoryTick(t=>t+1);setError('');
    if(!keepMessage)setMessage(graphChanged||spatialChanged?'Calibration changed. Affected measurements need review.':'Changes are local. Save your project to keep them.');
  }
  function change(fn:(d:Doc)=>void){if(!doc)return;const next=clone(doc);fn(next);commit(next);}
  function chooseTool(t:Tool){
    const plan=doc?.plans?.find(p=>p.id===doc.active_plan_id);
    if(plan&&t!=='select'&&!t.startsWith('cal-')&&t!==plan.tool)change(d=>d.active_plan_id=null);
    setTool(t);setDraft([]);setPeaks(null);setRegion(null);setError('');
  }
  function startPlan(p:ExtractionPlan){
    if(!results?.planning?.plans.find(v=>v.id===p.id)?.can_start)return;
    if(p.tool==='transcription'){const v=newReported(p);change(d=>{d.reported_values??=[];d.reported_values.push(v);d.active_plan_id=null;});setSelectedReport(v.id);setTool('select');setDraft([]);setPeaks(null);setRegion(null);setMessage('Transcription entry opened. No image calibration is required.');return;}
    change(d=>{d.active_plan_id=p.id;d.mode=['point','peak'].includes(p.tool)?'graph':'micrograph';d.active_sample=p.sample_ids.join(' | ');d.active_group=p.object_identity;});
    setTool(p.tool);setDraft([]);setPeaks(null);setRegion(null);setSelected(null);setMessage('Planned tool opened. New measurements will retain this plan and its evidence.');
  }
  function planAllowsMeasurement(){
    if(!doc?.active_plan_id)return true;
    if(evaluating||!results?.planning?.plans.find(p=>p.id===doc.active_plan_id)?.can_start){setError('The active plan, evidence or calibration needs review. Resolve it or exit the plan before measuring.');return false;}
    return true;
  }
  function undo(){
    if(draft.length){setDraft(draft.slice(0,-1));return;}
    if(!doc||!history.current.length)return;
    future.current.push(clone(doc));const next=history.current.pop()!;setDoc(next);setDirty(!same(next,savedDocument.current));setHistoryTick(t=>t+1);setMessage('Undone.');
  }
  function redo(){if(!doc||!future.current.length)return;history.current.push(clone(doc));const next=future.current.pop()!;setDoc(next);setDirty(!same(next,savedDocument.current));setHistoryTick(t=>t+1);setDraft([]);setMessage('Redone.');}
  const reset=(meta:ImageMeta,mode:Doc['mode'])=>{
    savedDocument.current=null;
    setPeaks(null);setRegion(null);setReferenceSearch('');
    setImage(meta);setDoc(newDoc(meta,mode));setProjectId(null);setRevision(0);setDirty(true);setSelected(null);setResults(null);setZoom(1);setTool('select');setDraft([]);history.current=[];future.current=[];
    setMessage('Image imported. Choose a mode and calibrate before measuring.');
  };
  const mayReplace=()=>!dirty||window.confirm('This project has unsaved changes. Continue without saving them?');
  async function importImage(f:File){if(!mayReplace())return;setBusy(true);setError('');try{const form=new FormData();form.append('file',f);reset(await api<ImageMeta>('/images','POST',form),doc?.mode||'micrograph');}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  async function loadDemo(mode:Doc['mode']){if(!mayReplace())return;setBusy(true);setError('');try{reset(await api<ImageMeta>('/demos/'+mode,'POST',{}),mode);}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  async function save():Promise<Envelope|null>{
    if(!doc)return null;setBusy(true);setError('');
    try{const id=projectId||crypto.randomUUID();const data=await api<Envelope>('/projects/'+id,'PUT',{document:doc,expected_revision:revision});
      savedDocument.current=clone(data.document);setProjectId(data.id);setRevision(data.revision);setDoc(data.document);setResults(data.results);setDirty(false);setMessage('Saved locally · revision '+data.revision);return data;
    }catch(e){setError((e as Error).message);return null;}finally{setBusy(false);}
  }
  async function openProjects(){try{setProjectList(await api('/projects'));}catch(e){setError((e as Error).message);}}
  async function openProject(id:string){
    if(!mayReplace())return false;setBusy(true);setError('');try{const data=await api<Envelope>('/projects/'+id);
      savedDocument.current=clone(data.document);
      setDoc(data.document);setImage(data.image);setResults(data.results);setProjectId(id);setRevision(data.revision);setDirty(false);history.current=[];future.current=[];setDraft([]);setSelected(null);setPeaks(null);setRegion(null);setZoom(1);setTool('select');setProjectList(null);setMessage('Project restored. Changed review definitions require a fresh review.');
      return true;
    }catch(e){setError((e as Error).message);return false;}finally{setBusy(false);}
  }
  async function download(kind:'json'|'csv'|'summary_csv'|'candidates_json'|'plan_json'|'reported_json'){
    if(!doc)return;const saved=dirty||!projectId?await save():{id:projectId};if(!saved)return;
    setBusy(true);try{
      const exported=await api<{path:string}>('/projects/'+saved.id+'/export-file?format='+kind,'POST',{});
      setMessage('Workspace export saved to '+exported.path);
    }catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  async function openMasterCopy(){
    if(!doc?.reference_id)return;
    const saved=dirty||!projectId?await save():{id:projectId};
    if(saved)setMasterCopyProject(saved.id);
  }
  function imagePoint(e:{clientX:number;clientY:number}):Point|null{
    const matrix=svg.current?.getScreenCTM();if(!matrix||!image)return null;
    const p=new DOMPoint(e.clientX,e.clientY).matrixTransform(matrix.inverse());
    return {x:Math.min(image.width,Math.max(0,p.x)),y:Math.min(image.height,Math.max(0,p.y))};
  }
  async function importReference(f:File){
    if(!doc)return;setBusy(true);setError('');
    try{const form=new FormData();form.append('file',f);const ref=await api<ReferenceIndex>('/references','POST',form);
      change(d=>{d.reference_id=ref.id;for(const m of d.measurements){m.reference_path=null;m.status='needs_review';}});
      setReference(ref);setMessage('Reference master indexed locally. Original values are unchanged.');
    }catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  async function findPeaks(p:Point){
    if(!doc)return;setBusy(true);setPeaks(null);setError('');
    try{setPeaks(await api<PeakSuggestion>('/peak-candidates','POST',{image_id:doc.image_id,point:p,radius:peakRadius}));}
    catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  async function findRegion(p:Point){
    if(!doc)return;setBusy(true);setRegion(null);setError('');
    try{setRegion(await api<RegionSuggestion>('/region-candidate','POST',{image_id:doc.image_id,point:p,radius:regionRadius,tolerance:regionTolerance}));}
    catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  function useRegion(){
    if(!doc||!region?.points.length||region.polygon_area_px2===null||region.simplification_delta_percent===null)return;
    if(!planAllowsMeasurement())return;
    const id=crypto.randomUUID();const {points,reason,...origin}=region;
    change(d=>d.measurements.push({id,kind:'area',points,label:'Region '+(d.measurements.filter(m=>m.kind==='area').length+1),sample:d.active_sample,
      group_label:d.active_group||'',include_in_summary:false,acquisition:'region_assist',status:'needs_review',plan_id:d.active_plan_id||null,
      region_origin:{...origin,polygon_area_px2:region.polygon_area_px2!,simplification_delta_percent:region.simplification_delta_percent!},
      note:'Seed-based grayscale proposal. Verify boundary and phase evidence. The stored origin describes the initial proposal, not later edits.'}));
    setSelected(id);setRegion(null);setTool('select');
  }
  function addMeasurement(kind:Measurement['kind'],points:Point[],assisted=false,candidateKind=''){
    if(!doc)return;const id=crypto.randomUUID();const count=doc.measurements.filter(m=>m.kind===kind).length+1;
    if(!planAllowsMeasurement())return;
    change(d=>d.measurements.push({id,kind,points,label:(kind==='area'?'Region':kind==='line'?'Length':'Point')+' '+count,sample:d.active_sample,
      note:assisted&&peaks?`${peaks.algorithm}; ${candidateKind}; click (${format(peaks.click.x)}, ${format(peaks.click.y)}) px; radius ${peaks.radius_px} px; initial candidate (${format(points[0].x)}, ${format(points[0].y)}) px. Verify curve identity. Pixel Y is not normalized intensity.`:'',
      acquisition:assisted?'peak_assist':'manual',status:'needs_review',group_label:kind==='area'?d.active_group||'':'',include_in_summary:false,plan_id:d.active_plan_id||null}));
    setSelected(id);setDraft([]);
  }
  function finishArea(){if(draft.length>=3)addMeasurement('area',draft);}
  function clickImage(e:React.MouseEvent<SVGSVGElement>){
    if(busy||!doc)return;const p=imagePoint(e);if(!p)return;
    if(!['select','cal-x','cal-y','cal-spatial'].includes(tool)&&!planAllowsMeasurement())return;
    if(tool==='point'){addMeasurement('point',[p]);return;}
    if(tool==='peak'){void findPeaks(p);return;}
    if(tool==='region'){void findRegion(p);return;}
    if(tool==='select'){setSelected(null);return;}
    const next=[...draft,p];
    if(tool==='area'){setDraft(next);return;}
    if(next.length<2){setDraft(next);return;}
    if(tool==='line'){addMeasurement('line',next);return;}
    change(d=>{if(tool==='cal-x')d.graph.x.points=next;else if(tool==='cal-y')d.graph.y.points=next;else if(tool==='cal-spatial')d.spatial.points=next;});
    setDraft([]);setTool('select');setMessage('Reference points set. Check the values and units in Calibration.');
  }
  function editMeasurement(fn:(m:Measurement)=>void){change(d=>{const m=d.measurements.find(v=>v.id===selected);if(m)fn(m);});}
  useEffect(()=>{
    const handler=(e:KeyboardEvent)=>{
      if(['INPUT','TEXTAREA','SELECT'].includes((e.target as HTMLElement).tagName)||busy||projectList)return;
      if(e.key==='Escape'){setDraft([]);setPeaks(null);setRegion(null);setTool('select');}
      if(e.key==='Enter'&&tool==='area'){e.preventDefault();finishArea();}
      if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();e.shiftKey?redo():undo();}
      if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='s'){e.preventDefault();void save();}
    };window.addEventListener('keydown',handler);return()=>window.removeEventListener('keydown',handler);
  });
  const current=doc?.measurements.find(m=>m.id===selected);
  const result=results?.measurements.find(m=>m.id===selected);
  const linkedTarget=reference?.targets.find(t=>t.path===current?.reference_path);
  const matchingTargets=reference?.targets.filter(t=>(t.record_id+' '+t.definition+' '+t.sample_ids.join(' ')).toLowerCase().includes(referenceSearch.toLowerCase()))||[];
  const visible=doc?.measurements.filter(m=>doc.mode==='graph'?m.kind==='point':m.kind!=='point')||[];
  const accepted=visible.filter(m=>m.status==='accepted').length;
  const scale=image?Math.min(fitWidth/image.width,fitHeight/image.height,1)*zoom:1;
  const instructions:Record<Tool,string>={select:'Select a measurement to inspect it. Drag its handles to refine the geometry.',point:'Click a data point. Coordinates are calculated from the graph calibration.',peak:'Click near a peak top, then choose a candidate. Light backgrounds only; curve identity must be checked.',region:'Click inside one object. Inspect the proposed boundary before adding it. Similar gray levels do not establish phase identity.',line:'Click two endpoints to measure a straight length.',area:'Click around a region, then Finish region or press Enter. Escape cancels.',
    'cal-x':`Click ${draft.length?'the second':'the first'} labeled X tick. Tick values are entered in Calibration.`,
    'cal-y':`Click ${draft.length?'the second':'the first'} labeled Y tick. Tick values are entered in Calibration.`,
    'cal-spatial':`Click ${draft.length?'the second':'the first'} endpoint of the scale bar.`};
  function axisControls(name:'x'|'y'){
    if(!doc)return null;const axis=doc.graph[name];return <div className="axis-fields">
      <div className="section-row"><b>{name.toUpperCase()} axis</b><span className={'dot '+(results?.calibration[name]?.valid?'ready':'')}/></div>
      <button className={'wide '+(tool==='cal-'+name?'active':'')} onClick={()=>chooseTool(('cal-'+name) as Tool)}>Set {name.toUpperCase()} reference ticks <span>{axis.points.length}/2</span></button>
      <div className="two"><label>First value<NumberField label={name+' first value'} value={axis.values[0]} onChange={v=>change(d=>d.graph[name].values[0]=v)}/></label><label>Second value<NumberField label={name+' second value'} value={axis.values[1]} onChange={v=>change(d=>d.graph[name].values[1]=v)}/></label></div>
      <div className="two"><label>Unit<input aria-label={name+' unit'} value={axis.unit} onChange={e=>change(d=>d.graph[name].unit=e.target.value)}/></label><label>Scale<select aria-label={name+' scale'} value={axis.scale} onChange={e=>change(d=>d.graph[name].scale=e.target.value as Axis['scale'])}><option value="linear">Linear</option><option value="log10">Log 10</option></select></label></div>
    </div>;
  }
  function marker(p:Point,label:string,color:string){return <g key={label} pointerEvents="none"><circle cx={p.x} cy={p.y} r={6/scale} fill={color} stroke="white" strokeWidth={1.5/scale}/><text x={p.x+10/scale} y={p.y-10/scale} fill={color} fontSize={12/scale} fontWeight="700" paintOrder="stroke" stroke="white" strokeWidth={3/scale}>{label}</text></g>;}

  return <div className="app-shell">
    {showPaperJobs&&<PaperJobs onClose={()=>setShowPaperJobs(false)} onOpen={async id=>{if(await openProject(id))setShowPaperJobs(false);}}/>}
    <header className="app-header"><div className="brand"><span className="brand-mark">Ti</span><div><strong>TiFigure Lab</strong><span>Literature measurement workbench</span></div></div>
      <div className="header-middle"><span className="local-dot"/> LOCAL WORKSPACE <span className="version">PROTOTYPE 0.8.1-dev</span></div>
      <div className="header-actions"><button className="primary" onClick={()=>setShowPaperJobs(true)} disabled={busy}>Paper extraction</button><LLMConnection/><button onClick={()=>setShowTableBatches(true)} disabled={busy}>Table batches</button><button onClick={openProjects} disabled={busy}>Open project</button><button className="primary" onClick={()=>void save()} disabled={!doc||busy}>{busy?'Working…':dirty?'Save project •':'Save project'}</button></div>
    </header>
    <div className="workspace" aria-busy={busy}>
      <aside className="left-panel">
        <div className="panel-heading"><span className="eyebrow">01 / SETUP</span><h2>Source & calibration</h2></div>
        <input ref={file} type="file" aria-label="Import image file" accept=".png,.jpg,.jpeg,.bmp,.tif,.tiff,.webp" hidden onChange={e=>{if(e.target.files?.[0])void importImage(e.target.files[0]);e.target.value='';}}/>
        <button className="import-button" onClick={()=>file.current?.click()} disabled={busy}><span>＋</span> Import image</button>
        <p className="muted tiny">PNG, JPEG, BMP, TIFF or WebP · up to 20 MB</p>
        {doc&&image?<fieldset disabled={busy} className="setup-fields">
          <label>Project name<input value={doc.name} onChange={e=>change(d=>d.name=e.target.value)} maxLength={200}/></label>
          <div className="source-file" title={image.filename}>{image.filename}<small>{image.width} × {image.height} px · original coordinates</small></div>
          <label>Workspace mode</label><div className="segmented"><button className={doc.mode==='graph'?'chosen':''} onClick={()=>{change(d=>d.mode='graph');chooseTool('select');setSelected(null);}}>Graph</button><button className={doc.mode==='micrograph'?'chosen':''} onClick={()=>{change(d=>d.mode='micrograph');chooseTool('select');setSelected(null);}}>Micrograph</button></div>
          <label>Active sample / condition<input placeholder="e.g. PAPER_A / 980 °C / 120 min / AC" value={doc.active_sample} onChange={e=>change(d=>d.active_sample=e.target.value)}/></label>
          {doc.mode==='micrograph'&&<label>Group for new regions<input placeholder="User-defined object group" value={doc.active_group||''} onChange={e=>change(d=>d.active_group=e.target.value)}/></label>}
          <label>Source reference<textarea rows={2} placeholder="Paper, figure, panel, page…" value={doc.source} onChange={e=>change(d=>d.source=e.target.value)}/></label>
          <div className="divider"/><h3>TiFM reference</h3>
          <input ref={referenceFile} type="file" aria-label="Import TiFM master file" accept=".json" hidden onChange={e=>{if(e.target.files?.[0])void importReference(e.target.files[0]);e.target.value='';}}/>
          <button className="wide" onClick={()=>referenceFile.current?.click()}>Import reference master</button>
          {reference?<><p className="hint">{reference.paper_short_id} · {reference.targets.length} indexed fields</p><p className="tiny muted">Read-only reference. Link a selected measurement in the review panel.</p>{reference.warnings.map((w,i)=><p className="warning-text tiny" key={i}>{w}</p>)}</>:<p className="tiny muted">Optional. Reuse existing sample IDs and record paths without changing the master.</p>}
          <div className="divider"/><h3>Calibration</h3>
          {doc.mode==='graph'?<>{axisControls('x')}<label className="check"><input type="checkbox" checked={doc.graph.use_y} onChange={e=>change(d=>d.graph.use_y=e.target.checked)}/> Also measure Y</label>{doc.graph.use_y&&axisControls('y')}<p className="hint">Axis-aligned graphs only. Broken axes, tilted axes and multiple Y axes are not supported yet.</p></>:<>
            <button className={'wide '+(tool==='cal-spatial'?'active':'')} onClick={()=>chooseTool('cal-spatial')}>Set scale-bar endpoints <span>{doc.spatial.points.length}/2</span></button>
            <div className="two"><label>Known length<NumberField label="Known scale length" value={doc.spatial.length} onChange={v=>change(d=>d.spatial.length=v)}/></label><label>Unit<select aria-label="Spatial unit" value={doc.spatial.unit} onChange={e=>change(d=>d.spatial.unit=e.target.value as Doc['spatial']['unit'])}><option>nm</option><option>µm</option><option>mm</option></select></label></div>
            <p className="hint">Uniform scale; square pixels assumed. Calibrate this panel only. Insets need their own project.</p>
            {results?.calibration.spatial.valid&&<div className="cal-ok">✓ Spatial scale ready</div>}
          </>}
        </fieldset>:<div className="start-note"><h3>One source. Traceable measurements.</h3><p>Import a figure to measure graph coordinates, lengths or two-dimensional areas.</p></div>}
        <div className="demo-links"><span className="eyebrow">PRACTICE WITH KNOWN GEOMETRY</span><button disabled={busy} onClick={()=>void loadDemo('graph')}>Load graph practice <span>↗</span></button><button disabled={busy} onClick={()=>void loadDemo('micrograph')}>Load area practice <span>↗</span></button><p className="tiny muted">Synthetic images, not research data.</p></div>
      </aside>
      <main className="main-panel">
        <div className="main-top"><div><span className="eyebrow">02 / MEASURE</span><h1>{doc?(doc.mode==='graph'?'Graph digitization':'Micrograph measurement'):'Your measurement workbench'}</h1></div><span className="manual-badge">HUMAN REVIEW</span></div>
        {doc&&<EvidencePlans key={'EvidencePlans-'+(projectId||doc.image_id)} doc={doc} planning={results?.planning} reference={reference} loading={evaluating} busy={busy} change={change} onStart={startPlan} onExport={()=>void download('plan_json')}/>}
        {doc&&<LLMPlanner key={'LLMPlanner-'+(projectId||doc.image_id)} doc={doc} reference={reference} busy={busy} change={change}/>} 
        {doc?.active_plan_id&&<div className="active-plan">Active plan: {doc.plans?.find(p=>p.id===doc.active_plan_id)?.panel||'Missing plan'} · {doc.plans?.find(p=>p.id===doc.active_plan_id)?.object_identity}<button disabled={busy} onClick={()=>{change(d=>d.active_plan_id=null);setDraft([]);setRegion(null);setPeaks(null);}}>Exit plan</button></div>}
        <div className="tool-bar"><div className="tools">
          {(['select',...(doc?.mode==='micrograph'?['line','area','region']:['point','peak'])] as Tool[]).map(t=><button key={t} disabled={!doc||busy} className={tool===t?'active':''} onClick={()=>chooseTool(t)}>{t==='select'?'↖ Select':t==='point'?'+ Point':t==='peak'?'⌁ Peak Assist':t==='region'?'◈ Region Assist':t==='line'?'／ Length':'◇ Area'}</button>)}
          <span className="toolbar-separator"/><button aria-label="Undo" disabled={busy||(!history.current.length&&!draft.length)} onClick={undo}>↶</button><button aria-label="Redo" disabled={busy||!future.current.length} onClick={redo}>↷</button>
        </div><div className="zoom-tools"><button aria-label="Zoom out" disabled={!image} onClick={()=>setZoom(z=>Math.max(.5,z/1.25))}>−</button><span>{Math.round(zoom*100)}%</span><button aria-label="Zoom in" disabled={!image} onClick={()=>setZoom(z=>Math.min(8,z*1.25))}>＋</button><button disabled={!image} onClick={()=>setZoom(1)}>Fit</button></div></div>
        <div className="instruction"><span className="instruction-dot"/>{doc?instructions[tool]:'Start with your own image, or try a synthetic practice figure.'}{tool==='area'&&draft.length>0&&<button onClick={finishArea} disabled={draft.length<3||busy}>Finish region ({draft.length})</button>}{draft.length>0&&<button onClick={()=>setDraft([])}>Cancel</button>}</div>
        {tool==='peak'&&<div className="assist-panel"><label>Search radius (source px)<select aria-label="Peak search radius" value={peakRadius} disabled={busy} onChange={e=>{setPeakRadius(Number(e.target.value));setPeaks(null);}}>{[20,40,60,80].map(n=><option key={n}>{n}</option>)}</select></label>
          {peaks?<><p>{peaks.reason}</p><div className="candidate-buttons">{peaks.candidates.map((p,i)=><button disabled={busy} key={i} onClick={()=>{addMeasurement('point',[{x:p.x,y:p.y}],true,p.kind);setPeaks(null);setTool('select');}}>Use candidate {i+1} · {p.kind==='unresolved_stroke_tip'?'Unresolved tip · ':''}{format(p.distance_px)} px away</button>)}<button disabled={busy} onClick={()=>{addMeasurement('point',[peaks.click]);setPeaks(null);setTool('select');}}>Use original click</button><button onClick={()=>setPeaks(null)}>Dismiss</button></div></>:<p className="tiny muted">Local pixel geometry, no LLM. Crossing traces, labels, broad peaks and weak contrast may need manual points.</p>}</div>}
        {tool==='region'&&<div className="assist-panel"><div className="region-controls"><label>Search radius (source px)<select aria-label="Region search radius" value={regionRadius} disabled={busy} onChange={e=>{setRegionRadius(Number(e.target.value));setRegion(null);}}>{[40,80,160,256].map(n=><option key={n}>{n}</option>)}</select></label>
          <label>Gray tolerance<select aria-label="Region gray tolerance" value={regionTolerance} disabled={busy} onChange={e=>{setRegionTolerance(Number(e.target.value));setRegion(null);}}>{[12,24,40,64,80].map(n=><option key={n}>{n}</option>)}</select></label></div>
          {region?<><p>{region.reason}</p>{region.points.length>0&&<p className="tiny">Initial mask: {region.pixel_count} pixels · Polygon: {format(region.polygon_area_px2!)} px² · Simplification change: {format(region.simplification_delta_percent!)}% (not measurement uncertainty)</p>}{region.warnings.map(w=><p className="hint" key={w}>{w}</p>)}<div className="candidate-buttons"><button disabled={busy||!region.points.length} onClick={useRegion}>Add candidate region</button><button onClick={()=>setRegion(null)}>Dismiss</button><button onClick={()=>chooseTool('area')}>Draw manually</button></div></>:<p className="tiny muted">Local grayscale region growing; no learned model or phase classifier. Change parameters, then click again. Edge-touching and holed candidates are rejected.</p>}</div>}
        <div className="image-stage" ref={stage}>
          {image&&doc?<div className="image-inner" style={{width:image.width*scale,height:image.height*scale}}>
            <svg ref={svg} data-testid="image-canvas" width={image.width*scale} height={image.height*scale} viewBox={`0 0 ${image.width} ${image.height}`} onClick={clickImage} onPointerMove={e=>setCursor(imagePoint(e))} onPointerLeave={()=>setCursor(null)} style={{cursor:tool==='select'?'default':'crosshair'}}>
              <image href={image.url} width={image.width} height={image.height}/>
              {tool==='region'&&region&&showMarks&&<g pointerEvents="none"><rect x={Math.max(0,region.seed.x-region.radius_px)} y={Math.max(0,region.seed.y-region.radius_px)} width={Math.min(image.width,region.seed.x+region.radius_px)-Math.max(0,region.seed.x-region.radius_px)} height={Math.min(image.height,region.seed.y+region.radius_px)-Math.max(0,region.seed.y-region.radius_px)} fill="none" stroke="#b06627" strokeWidth={1/scale} strokeDasharray="5 4"/>{region.points.length>0&&<polygon points={region.points.map(p=>`${p.x},${p.y}`).join(' ')} fill="#e37d38" fillOpacity={.25} stroke="#b06627" strokeWidth={2/scale}/ >}{marker(region.seed,'Seed','#b06627')}</g>}
              {tool==='peak'&&peaks&&<g pointerEvents="none"><rect x={Math.max(0,peaks.click.x-peaks.radius_px)} y={Math.max(0,peaks.click.y-peaks.radius_px)} width={Math.min(image.width,peaks.click.x+peaks.radius_px)-Math.max(0,peaks.click.x-peaks.radius_px)} height={Math.min(image.height,peaks.click.y+peaks.radius_px)-Math.max(0,peaks.click.y-peaks.radius_px)} fill="none" stroke="#b06627" strokeDasharray="5 4" strokeWidth={1/scale}/>{peaks.candidates.map((p,i)=>marker(p,'C'+(i+1),'#b06627'))}</g>}
              {showMarks&&visible.map((m,index)=>{
                const points=m.points.map((p,i)=>drag.current?.id===m.id&&drag.current.index===i&&dragPoint?dragPoint:p);
                const active=selected===m.id;const color=m.status==='accepted'?'#08836f':active?'#e37d38':'#1d749c';
                return <g key={m.id} onClick={e=>{if(tool==='select'){e.stopPropagation();setSelected(m.id);}}}>
                  {m.kind==='area'?<polygon points={points.map(p=>`${p.x},${p.y}`).join(' ')} fill={color} fillOpacity={.18} stroke={color} strokeWidth={(active?2.5:1.5)/scale}/>:m.kind==='line'?<line x1={points[0].x} y1={points[0].y} x2={points[1].x} y2={points[1].y} stroke={color} strokeWidth={2.5/scale}/>:<circle cx={points[0].x} cy={points[0].y} r={6/scale} fill={color} stroke="white" strokeWidth={1.5/scale}/>}
                  <text x={points[0].x+10/scale} y={points[0].y-10/scale} fontSize={12/scale} fill={color} paintOrder="stroke" stroke="white" strokeWidth={3/scale} fontWeight="700">{index+1}</text>
                  {active&&tool==='select'&&points.map((p,i)=><circle key={i} cx={p.x} cy={p.y} r={5/scale} fill="white" stroke={color} strokeWidth={2/scale} style={{cursor:'grab'}} onClick={e=>e.stopPropagation()}
                    onPointerDown={e=>{if(busy)return;e.stopPropagation();drag.current={id:m.id,index:i};setDragPoint(p);e.currentTarget.setPointerCapture(e.pointerId);}}
                    onPointerMove={e=>{if(drag.current){e.stopPropagation();setDragPoint(imagePoint(e));}}}
                    onPointerUp={e=>{e.stopPropagation();const pos=imagePoint(e),dragging=drag.current;drag.current=null;setDragPoint(null);if(pos&&dragging)change(d=>{const target=d.measurements.find(v=>v.id===dragging.id);if(target)target.points[dragging.index]=pos;});}}
                    onPointerCancel={()=>{drag.current=null;setDragPoint(null);}}/>)}
                </g>;
              })}
              {showMarks&&(doc.mode==='graph'?<>{doc.graph.x.points.map((p,i)=>marker(p,'X'+(i+1),'#087f73'))}{doc.graph.use_y&&doc.graph.y.points.map((p,i)=>marker(p,'Y'+(i+1),'#7854a6'))}</>:<>{doc.spatial.points.length===2&&<line x1={doc.spatial.points[0].x} y1={doc.spatial.points[0].y} x2={doc.spatial.points[1].x} y2={doc.spatial.points[1].y} stroke="#087f73" strokeWidth={2/scale} pointerEvents="none"/>}{doc.spatial.points.map((p,i)=>marker(p,'S'+(i+1),'#087f73'))}</>)}
              {draft.length>0&&<g pointerEvents="none"><polyline points={draft.map(p=>`${p.x},${p.y}`).join(' ')} fill="none" stroke="#e37d38" strokeWidth={2/scale} strokeDasharray={`${5/scale} ${4/scale}`}/>{draft.map((p,i)=>marker(p,String(i+1),'#e37d38'))}</g>}
            </svg>
          </div>:<div className="empty-stage"><div className="empty-symbol">⌖</div><h2>Every number starts with a source.</h2><p>Calibrate once. Measure in original image coordinates.<br/>Keep the geometry, context and review decision together.</p><button className="primary" onClick={()=>file.current?.click()} disabled={busy}>Import your first image</button><div className="capability-chips"><span>Graph points</span><span>Lengths</span><span>2D areas</span></div></div>}
        </div>
        <div className="canvas-footer"><span>{image?`${image.width} × ${image.height} source pixels`:'No image loaded'}{cursor?` · x ${format(cursor.x)}, y ${format(cursor.y)} px`:''}</span><label className="check"><input type="checkbox" checked={showMarks} onChange={e=>setShowMarks(e.target.checked)}/> Show overlays</label></div>
        {doc&&<ReportedValues key={'ReportedValues-'+(projectId||doc.image_id)} doc={doc} results={results} reference={reference} busy={busy} loading={evaluating} change={change} selected={selectedReport} onSelect={setSelectedReport} onExport={()=>void download('reported_json')}/>} 
        <section className="measurement-section"><div className="table-heading"><h2>Measurements <span>{visible.length}</span></h2><div><span className="muted tiny">{accepted} reviewed · {visible.length-accepted} pending</span><button disabled={!doc||busy} onClick={()=>void download('csv')}>Export CSV</button></div></div>
          <div className="table-scroll"><table><thead><tr><th>OBJECT</th><th>SAMPLE / CONDITION</th><th>RESULT</th><th>REVIEW</th></tr></thead><tbody>
            {visible.map((m,i)=>{const r=results?.measurements.find(v=>v.id===m.id);return <tr key={m.id} className={selected===m.id?'selected':''} onClick={()=>{setSelected(m.id);chooseTool('select');}}><td><span className="row-index">{i+1}</span>{m.label}</td><td>{m.sample||<span className="muted">Not assigned</span>}</td><td>{evaluating?'Updating…':r?.valid?Object.entries(r.values).map(([k,v])=><span className="result-piece" key={k}>{k==='ecd'?'ECD':k.toUpperCase()} {format(v)} {r.units[k]}</span>):<span className="warning-text">Blocked — see review details</span>}</td><td><span className={'status '+m.status}>{m.status==='accepted'?'Reviewed':'Needs review'}</span></td></tr>;})}
            {!visible.length&&<tr><td colSpan={4} className="empty-table">{doc?'Choose a measurement tool above to add your first object.':'Your points and regions will appear here.'}</td></tr>}
          </tbody></table></div>
        </section>
        {doc?.mode==='micrograph'&&<ObjectSummary summary={results?.summary} measurements={doc.measurements} loading={evaluating} busy={busy} onExport={()=>void download('summary_csv')}/>}
        {doc&&<CandidateReview key={'CandidateReview-'+(projectId||doc.image_id)} doc={doc} results={results} reference={reference} loading={evaluating} busy={busy} change={change} onExport={()=>void download('candidates_json')}/>}
      </main>
      <aside className="right-panel"><div className="panel-heading"><span className="eyebrow">03 / REVIEW</span><h2>Measurement details</h2></div>
        {current&&doc?<fieldset disabled={busy} className="details">
          <span className={'status '+current.status}>{current.status==='accepted'?'Reviewed geometry':'Needs review'}</span>
            {current.plan_id&&<p className="hint">Linked plan: {doc.plans?.find(p=>p.id===current.plan_id)?.panel||'Missing plan'}. Evidence changes require review again.</p>}
          <label>Object label<input value={current.label} onChange={e=>editMeasurement(m=>m.label=e.target.value)}/></label>
          <label>Sample / condition<input value={current.sample} placeholder="Assign a material state" onChange={e=>editMeasurement(m=>m.sample=e.target.value)}/></label>
          {current.kind==='area'&&<div className="object-settings"><label>Object group<input value={current.group_label||''} placeholder="User-defined; verify phase separately" onChange={e=>editMeasurement(m=>m.group_label=e.target.value)}/></label><label className="check"><input type="checkbox" checked={current.include_in_summary||false} onChange={e=>editMeasurement(m=>m.include_in_summary=e.target.checked)}/> Include in object summary</label>{!current.include_in_summary&&<label>Exclusion reason<input value={current.exclusion_reason||''} placeholder="Optional: cropped, duplicate, wrong object…" onChange={e=>editMeasurement(m=>m.exclusion_reason=e.target.value)}/></label>}<p className="tiny muted">Geometry must be reviewed before inclusion takes effect. A group label is not automatic phase identification.</p></div>}
          {current.region_origin&&<details><summary>Initial region proposal</summary><p className="tiny">{current.region_origin.pixel_count} pixels; tolerance {current.region_origin.tolerance}; radius {current.region_origin.radius_px} px. Simplification changed area by {format(current.region_origin.simplification_delta_percent)}%. Later vertex edits are separate.</p>{current.region_origin.warnings.map(w=><p className="hint" key={w}>{w}</p>)}</details>}
          {reference&&<div className="reference-panel"><h3>Existing master field</h3><label>Filter records<input value={referenceSearch} onChange={e=>setReferenceSearch(e.target.value)} placeholder="Sample ID, record ID, metric…"/></label>
            <label>Link to field<select aria-label="Link to master field" value={current.reference_path||''} onChange={e=>{const target=reference.targets.find(t=>t.path===e.target.value);editMeasurement(m=>{m.reference_path=target?.path||null;if(target)m.sample=target.sample_ids.join(' | ');});}}><option value="">Not linked</option>{(linkedTarget&&!matchingTargets.includes(linkedTarget)?[linkedTarget,...matchingTargets]:matchingTargets).map(t=><option key={t.path} value={t.path}>{t.record_id} · {t.definition}</option>)}</select></label>
            {linkedTarget&&<><b>{linkedTarget.definition}</b><p className="reference-value">Existing: {JSON.stringify(linkedTarget.value)} {linkedTarget.unit||''}</p><code>{linkedTarget.path}</code><p className="tiny">Samples: {linkedTarget.sample_ids.join(', ')}</p><p className="tiny">Source: {typeof linkedTarget.source==='string'?linkedTarget.source:JSON.stringify(linkedTarget.source)}</p><p className="hint">Check phase, metric definition, units and sample state. A single region is not a population mean or fraction. No automatic replacement or 5% acceptance rule.</p></>}
          </div>}
          <div className="result-card">{evaluating?<p>Updating measurement…</p>:result?.valid?Object.entries(result.values).map(([key,value])=><div key={key}><span>{key==='ecd'?'Equivalent circle diameter':key==='area'?'2D polygon area':key==='length'?'Straight length':key.toUpperCase()+' coordinate'}</span><strong>{format(value)} <small>{result.units[key]}</small></strong></div>):<p className="warning-text">{result?.error||'Measurement not available.'}</p>}</div>
          {result?.warnings.map(w=><p className="hint" key={w}>{w}</p>)}
          <label>Review note<textarea rows={3} value={current.note} placeholder="Phase evidence, exclusions, limitations…" onChange={e=>editMeasurement(m=>m.note=e.target.value)}/></label>
          <button className="primary wide" disabled={!result?.valid||evaluating||!current.sample.trim()} onClick={()=>editMeasurement(m=>{m.status=m.status==='accepted'?'needs_review':'accepted';m.review_basis=result?.review_basis;})}>{current.status==='accepted'?'Return to review':'Mark reviewed'}</button>
          {!current.sample.trim()&&<p className="hint">Assign a sample before marking this measurement reviewed.</p>}
          <p className="tiny muted">Review confirms your selected geometry and context. It does not establish phase identity or material-wide statistics.</p>
          <details><summary>Source coordinates ({current.points.length})</summary><div className="coordinates">{current.points.map((p,i)=><div key={i}>{i+1} · {format(p.x)}, {format(p.y)} px</div>)}</div></details>
          <button className="danger wide" onClick={()=>{change(d=>d.measurements=d.measurements.filter(m=>m.id!==selected));setSelected(null);}}>Delete measurement</button>
        </fieldset>:<div className="review-empty"><div className="outline-circle">✓</div><h3>Keep the evidence close.</h3><p>Select a point or region to inspect its values, assign a sample and review the measurement.</p><div className="review-rule"><b>Geometry ≠ phase identity</b><p>A measured region is not automatically an α or β phase.</p></div></div>}
        <div className="export-card"><span className="eyebrow">WORKSPACE FILE</span><p>Save calibration, geometry and review state for later.</p><button className="wide" disabled={!doc||busy} onClick={()=>void download('json')}>Export workspace JSON</button><button className="wide" disabled={!doc?.reference_id||busy} onClick={()=>void openMasterCopy()}>Export TiFM copy</button><small>Copy export saves the current project first. Only explicitly adopted reviewed scalar fields may change; the original master stays intact. Learned segmentation and downstream training compatibility are not implemented.</small></div>
      </aside>
    </div>
    <footer className={'status-bar '+(error?'error':'')} role={error?'alert':'status'}><span>{error||message}</span><span title={storage}>{projectId?`Revision ${revision}${dirty?' · Unsaved changes':''}`:storage}</span></footer>
    {showTableBatches&&<TableBatches onClose={()=>setShowTableBatches(false)} onOpen={async id=>{if(await openProject(id))setShowTableBatches(false);}}/>}
    {masterCopyProject&&<MasterCopy projectId={masterCopyProject} onClose={()=>setMasterCopyProject(null)}/>}
    {projectList&&<div className="modal-backdrop"><section className="modal" role="dialog" aria-modal="true" aria-label="Open project"><div className="section-row"><h2>Open a local project</h2><button onClick={()=>setProjectList(null)}>Close</button></div><p className="muted">Stored on this computer. Opening a project restores its original image and measurements.</p><div className="project-list">{projectList.length?projectList.map(p=><button disabled={busy} key={p.id} onClick={()=>void openProject(p.id)}><strong>{p.name}</strong><span>Revision {p.revision} · {new Date(p.updated_at).toLocaleString('en-US')}</span></button>):<p>No saved projects yet.</p>}</div></section></div>}
  </div>;
}
createRoot(document.getElementById('root')!).render(<App/>);
