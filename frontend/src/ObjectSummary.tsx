import React from 'react';
import {ObjectSummary as Summary,Measurement} from './types';
const number=(v:number|null,unit='')=>v===null?'—':v.toLocaleString('en-US',{maximumFractionDigits:4})+' '+unit;
export function ObjectSummary({summary,measurements,loading,busy,onExport}:{summary?:Summary;measurements:Measurement[];loading:boolean;busy:boolean;onExport:()=>void}){
  return <section className="measurement-section"><div className="table-heading"><h2>Object summary</h2><button onClick={onExport} disabled={busy||loading||!summary?.groups.length}>Export summary CSV</button></div>
    <p className="summary-note">Only explicitly included, reviewed areas with a sample and an object group. N counts objects in this image, not independent specimens. No phase fraction is inferred.</p>
    {loading?<p className="summary-note">Updating summary…</p>:<>
      {!summary?.groups.length?<p className="summary-note">No eligible objects yet. Select a region, assign its group, include it and review its geometry.</p>:<div className="table-scroll"><table><thead><tr><th>SAMPLE / GROUP</th><th>N</th><th>AREA / OBJECT</th><th>ECD DISTRIBUTION</th></tr></thead><tbody>{summary.groups.map(g=><tr key={JSON.stringify([g.sample,g.group])}><td>{g.sample}<br/><b>{g.group}</b></td><td>{g.n_objects}</td><td>{g.valid?<>Mean {number(g.mean_area,g.area_unit)}</>:<span className="warning-text">Withheld: {g.reason}</span>}</td><td>{g.valid?<>
        <span className="result-piece">Mean {number(g.mean_ecd,g.length_unit)}</span><span className="result-piece">Median {number(g.median_ecd,g.length_unit)}</span><span className="result-piece">Sample SD {number(g.sample_sd_ecd,g.length_unit)}{g.n_objects===1?' (N < 2)':''}</span><span className="result-piece">Range {number(g.min_ecd)}–{number(g.max_ecd,g.length_unit)}</span></>:<span className="warning-text">Resolve overlap / limits first</span>}</td></tr>)}</tbody></table></div>}
      {!!summary?.omitted.length&&<details className="summary-note"><summary>Omitted from statistics ({summary.omitted.length})</summary>{summary.omitted.map(o=><p key={o.id}>{measurements.find(m=>m.id===o.id)?.label||o.id}: {o.reason}</p>)}</details>}
    </>}
  </section>;
}
