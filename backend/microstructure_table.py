"""Explicit SEM table vocabulary. Source parsing never receives reference answers."""
import math
import re

COLUMNS = {
    'Vαp': ('%', 'primary_alpha_fraction', 'Reported primary-alpha volume fraction'),
    'Vβt': ('%', 'transformed_beta_fraction', 'Reported transformed-beta-region volume fraction, not pure beta phase'),
    'Dαp': ('µm', 'primary_alpha_size', 'Reported primary-alpha diameter; equivalence to 2D ECD is unverified'),
    'Dαs': ('nm', 'secondary_alpha_width', 'Reported secondary-alpha width, not primary-alpha diameter'),
}


def parse_microstructure_table(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    indices = [i for i,line in enumerate(lines) if re.match(r'^Samples\s', line)]
    if len(indices) != 1:
        raise ValueError('Exactly one Samples header is required.')
    header_index = indices[0]
    tail = lines[header_index][len('Samples'):].strip()
    columns = []
    while tail:
        match = re.match(r'([VD][αβ][pst])\s*\(([^)]+)\)\s*', tail)
        if not match:
            raise ValueError('Unknown or incomplete column heading.')
        symbol, unit = match.groups()
        unit = unit.replace('μ', 'µ').strip()
        if symbol not in COLUMNS or COLUMNS[symbol][0] != unit:
            raise ValueError('Unknown quantity or unexpected unit; do not guess.')
        columns.append(symbol)
        tail = tail[match.end():]
    if len(columns) != len(COLUMNS) or set(columns) != set(COLUMNS):
        raise ValueError('Expected each supported column exactly once.')
    records, labels, warnings = [], set(), []
    for line in lines[header_index+1:]:
        tokens = line.split()
        if len(tokens) != len(columns)+1 or not re.fullmatch(r'[A-Za-z][\w.-]*', tokens[0]):
            raise ValueError('Incomplete or unsupported table row.')
        label = tokens[0]
        if label in labels:
            raise ValueError('Duplicate sample row.')
        labels.add(label)
        row_values = {}
        for symbol, token in zip(columns, tokens[1:]):
            if not re.fullmatch(r'\d+(?:\.\d+)?(?:[eE][+-]?\d+)?', token):
                raise ValueError('Unsupported value notation; preserve and review the source.')
            value = float(token)
            if not math.isfinite(value) or (symbol.startswith('V') and not 0 <= value <= 100) or (symbol.startswith('D') and value <= 0):
                raise ValueError('Value outside the supported physical range.')
            unit, metric, definition = COLUMNS[symbol]
            row_values[symbol] = value
            records.append(dict(sample_label=label, source_symbol=symbol, metric=metric,
                definition=definition, unit=unit, value_text=token, value=value,
                raw_row=line, uncertainty=None, n_objects=None, review_status='needs_review'))
        if abs(row_values['Vαp']+row_values['Vβt']-100) > 0.2:
            warnings.append(f'{label}: reported fractions do not sum to 100 within a 0.2 percentage-point rounding check; values preserved, not normalized.')
    if not labels:
        raise ValueError('No sample rows.')
    return dict(records=records, warnings=warnings, sample_count=len(labels), header=lines[header_index])


def match_reference(record, paper, master):
    """Paper-scoped sample label and metric match; no inferred treatment conditions."""
    if master.get('paper_short_id') != paper:
        raise ValueError('Cross-paper mapping is forbidden.')
    samples = [s for s in master.get('samples', []) if s.get('sample_label') == record['sample_label']]
    output = dict(sample_ids=[], target_path=None, status='sample_unmatched', comparison=None,
                  human_review_required=True, conditions_verified=False)
    if len(samples) != 1:
        output['status'] = 'sample_ambiguous' if samples else 'sample_unmatched'
        return output
    output['sample_ids'] = [samples[0]['sample_id']]
    targets = [(i,r) for i,r in enumerate(master.get('microstructure_quantification_records', []))
               if r.get('sample_id') == samples[0]['sample_id'] and r.get('quantification_target') == record['metric']]
    if len(targets) != 1:
        output['status'] = 'target_ambiguous' if targets else 'outside_existing_targets'
        return output
    i, target = targets[0]
    output.update(target_path=f'/microstructure_quantification_records/{i}/value', status='mapping_needs_review',
                  reference_value=target.get('value'), reference_unit=target.get('unit'))
    if str(target.get('unit')).replace('μ','µ') != record['unit']:
        output['status'] = 'unit_mismatch'
        return output
    old = target.get('value')
    if not isinstance(old, (int,float)) or isinstance(old,bool) or not math.isfinite(old):
        output['status'] = 'non_numeric_reference'
        return output
    output['comparison'] = dict(exact_match=record['value']==old, difference=record['value']-old,
                                scope='reported_table_values_only_not_image_measurement_accuracy')
    return output
