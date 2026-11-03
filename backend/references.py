"""Read-only index of existing TiFM values. Never rewrites the source master."""


def catalog(data):
    if not isinstance(data,dict) or data.get('schema_version')!='TiFM_master_self_contained_v0.1':
        raise ValueError('Select a TiFM_master_self_contained_v0.1 JSON file.')
    samples=data.get('samples')
    if not isinstance(samples,list) or not all(isinstance(s,dict) and isinstance(s.get('sample_id'),str) for s in samples):
        raise ValueError('The master must contain samples with string sample_id values.')
    ids=[s['sample_id'] for s in samples]
    if len(set(ids))!=len(ids): raise ValueError('Duplicate sample IDs; resolve them before linking measurements.')
    targets=[];warnings=[]
    def add(path,record,key,record_id,kind,unit):
        sample_ids=record.get('linked_sample_ids')
        if sample_ids is None: sample_ids=[record['sample_id']] if record.get('sample_id') else []
        if not isinstance(sample_ids,list) or not all(isinstance(s,str) for s in sample_ids):
            warnings.append(f'{path}: invalid sample reference; target omitted.');return
        if not sample_ids or any(s not in ids for s in sample_ids):
            warnings.append(f'{path}: missing/unresolved sample reference; target omitted.');return
        value=record.get(key)
        definition=record.get('property_name',record.get('quantification_target',key))
        if not isinstance(definition,str) or (unit is not None and not isinstance(unit,str)):
            warnings.append(f'{path}: invalid metric name or unit; target omitted.');return
        # Keep scalar/range/string values as supplied. Numeric comparison is not inferred.
        targets.append({'path':path,'record_id':str(record_id),'kind':kind,'sample_ids':sample_ids,
                        'value':value,'unit':unit,'source':record.get('source_figure_or_table'),
                        'definition':definition,'record_context':{k:v for k,v in record.items() if k not in (key,'peaks')}})
    for module,id_key,kind in [('sample_property_value_records','property_record_id','property'),
                               ('microstructure_quantification_records','quantification_record_id','microstructure')]:
        records=data.get(module,[])
        if not isinstance(records,list): warnings.append(f'{module}: expected an array; not indexed.');continue
        for i,r in enumerate(records):
            if not isinstance(r,dict) or id_key not in r: warnings.append(f'{module}/{i}: missing record ID; omitted.');continue
            add(f'/{module}/{i}/value',r,'value',r[id_key],kind,r.get('unit'))
    records=data.get('xrd_records',[])
    if not isinstance(records,list): raise ValueError('xrd_records must be an array.')
    for i,r in enumerate(records):
        if not isinstance(r,dict) or not isinstance(r.get('peaks',[]),list):
            warnings.append(f'xrd_records/{i}: invalid record; omitted.');continue
        for j,peak in enumerate(r.get('peaks',[])):
            if not isinstance(peak,dict): continue
            record={**r,**peak}
            for key,unit in [('two_theta_deg','deg'),('normalized_intensity',None)]:
                if key in peak:
                    add(f'/xrd_records/{i}/peaks/{j}/{key}',record,key,
                        f'{r.get("xrd_record_id", "?")} / peak {peak.get("peak_id", "?")}', 'xrd',unit)
    return {'schema_version':data['schema_version'],'paper_id':data.get('paper_id'),
            'paper_short_id':data.get('paper_short_id'),
            'samples':[{'sample_id':s['sample_id'],'label':s.get('sample_label',''),'context':s} for s in samples],
            'targets':targets,'warnings':warnings,
            'notice':'Existing master values, not necessarily author-reported values. Linking does not verify scientific equivalence.'}
