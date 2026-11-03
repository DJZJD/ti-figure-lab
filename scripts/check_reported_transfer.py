"""Post-extraction comparison only. Never supplies reference answers to a model."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import urllib.request
from backend.references import catalog
from backend.candidates import unit_spec

MECHANICAL={'yield_strength','tensile_strength','elongation','impact_toughness'}


def canonical(name):return name.strip().lower().replace(' ','_')


def compare(job,master,paper):
    if master.get('paper_short_id')!=paper:raise ValueError('Reference paper ID mismatch.')
    if job['status']!='awaiting_review':raise ValueError('Only complete extraction responses can be compared.')
    scope=MECHANICAL if job.get('extraction_scope')=='mechanical_properties' else None
    index=catalog(master);labels={s['sample_id']:s['label'] for s in index['samples']}
    rows=[];used=set()
    for target in index['targets']:
        if scope and (target['kind']!='property' or canonical(target['definition']) not in scope):continue
        local_labels=[labels[s] for s in target['sample_ids']]
        matches=[r for r in job['records'] if r['kind']==target['kind'] and canonical(r['quantity'])==canonical(target['definition']) and local_labels==[r['sample_label']]]
        row=dict(path=target['path'],sample_labels=local_labels,quantity=target['definition'],reference_value=target['value'],reference_unit=target['unit'],
                 candidate_ids=[r['id'] for r in matches],status='missing' if not matches else 'ambiguous' if len(matches)>1 else 'uncomparable',reference_source=target['source'])
        used.update(r['id'] for r in matches)
        if len(matches)==1:
            r=matches[0];a,b=unit_spec(r['unit']),unit_spec(target['unit'])
            row.update(value=r['value'],raw_value=r['raw_value'],unit=r['unit'],conditions=r['conditions'],checks=r['checks'],
                evidence_text_stream=r.get('evidence_text_stream'),source_page=r['page'],source_locator=r['locator'],evidence_quote=r['evidence_quote'],
                evidence_image=r.get('evidence_image'),review_status=r['review_status'],cell_evidence_status=r.get('table_evidence',{}).get('status','not_checked'))
            if isinstance(r['value'],(int,float)) and not isinstance(r['value'],bool) and isinstance(target['value'],(int,float)) and not isinstance(target['value'],bool) and a and b and a[0]==b[0]:
                difference=r['value']*a[1]/b[1]-target['value']
                row.update(status='numeric_agreement' if abs(difference)<1e-9 else 'numeric_difference',difference=difference)
        rows.append(row)
    return dict(artifact_type='post_extraction_reference_comparison_not_accuracy_certification',job_id=job['id'],paper=paper,scope=job.get('extraction_scope','all'),
        rows=rows,summary=dict(scoped_reference_fields=len(rows),numeric_agreements=sum(r['status']=='numeric_agreement' for r in rows),
            numeric_differences=sum(r['status']=='numeric_difference' for r in rows),missing=sum(r['status']=='missing' for r in rows),ambiguous=sum(r['status']=='ambiguous' for r in rows),
            flagged_rows=sum(bool(r.get('checks')) for r in rows),cell_evidence_supported=sum(r.get('cell_evidence_status')=='supported' for r in rows),unmatched_extractions=len([r for r in job['records'] if r['id'] not in used])),
        unassessed_reference_fields=len(index['targets'])-len(rows),unmatched_extractions=[r for r in job['records'] if r['id'] not in used],
        notice='Labels and quantities identify comparisons; values never select a match. Numeric agreement does not verify conditions, uncertainty, evidence, sample identity or reference correctness. No acceptance or master changes.')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--job-id',required=True);parser.add_argument('--paper',required=True);parser.add_argument('--master',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    import re
    if not re.fullmatch('[0-9a-f]{32}',args.job_id):raise ValueError('Invalid job ID.')
    job=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/paper-jobs/'+args.job_id))
    raw=args.master.read_bytes();report=compare(job,json.loads(raw.decode('utf-8-sig')),args.paper)
    report.update(reference_sha256=hashlib.sha256(raw).hexdigest(),source_pdf_sha256=job['pdf_sha256'],api_usage=job.get('usage',{}))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.with_suffix('.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    esc=lambda x:html.escape(str(x))
    body=''.join('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in (', '.join(r['sample_labels']),r['quantity'],r.get('raw_value','—'),r['reference_value'],r['reference_unit'],r['status'],'; '.join(r.get('checks',[]))))+'</tr>' for r in report['rows'])
    doc='<!doctype html><meta charset="utf-8"><title>Reported-value transfer check</title><style>body{font:16px system-ui;margin:36px;color:#183836}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccd8d6;padding:10px;text-align:left}th{background:#eaf3f1}small{color:#566}</style><h1>'+esc(args.paper)+' · Reported-value transfer check</h1><p>'+esc(report['notice'])+'</p><p>'+esc(report['summary'])+'</p><table><thead><tr><th>Sample</th><th>Quantity</th><th>Extracted expression</th><th>Reference center</th><th>Unit</th><th>Comparison</th><th>Evidence flags</th></tr></thead><tbody>'+body+'</tbody></table><p>Unassessed reference fields: '+str(report['unassessed_reference_fields'])+'</p><small>Job: '+esc(args.job_id)+' · Scope: '+esc(report['scope'])+'</small>'
    args.output.with_suffix('.html').write_text(doc,encoding='utf8')
    print(json.dumps(report['summary']))


if __name__=='__main__':main()
