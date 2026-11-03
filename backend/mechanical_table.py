"""Conservative native-text mechanical table parser; no reference answers as input."""
import math
import re

COLUMNS = {
    'YS': ('MPa', 'yield_strength', 'Reported yield strength'),
    'UTS': ('MPa', 'tensile_strength', 'Reported ultimate tensile strength'),
    'EL': ('%', 'elongation', 'Reported elongation'),
    'Impact energy': ('J', 'impact_energy', 'Reported Charpy impact energy'),
}
NUMBER = r'\d+(?:\.\d+)?'


def parse_mechanical_table(text, *, confirmed_header_aliases=None):
    aliases = confirmed_header_aliases or {}
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    headers = [i for i, line in enumerate(lines) if line.startswith('Samples ')]
    if len(headers) != 1:
        raise ValueError('Exactly one Samples header is required.')
    index = headers[0]
    tail = lines[index][len('Samples'):].strip()
    columns = []
    warnings = []
    while tail:
        match = re.match(r'([A-Za-z]+(?:\s+[A-Za-z]+)*)\s*\(([^)]+)\)\s*', tail)
        if not match:
            raise ValueError('Unsupported column heading.')
        raw_symbol, unit = match.groups()
        raw_symbol = raw_symbol.strip()
        symbol = aliases.get(raw_symbol, raw_symbol)
        if symbol not in COLUMNS or unit != COLUMNS[symbol][0]:
            raise ValueError('Unknown quantity or unexpected unit; do not guess.')
        if symbol != raw_symbol:
            warnings.append(f'Explicit source-specific header alias: {raw_symbol} -> {symbol}; requires review.')
        columns.append((raw_symbol, symbol))
        tail = tail[match.end():]
    if len(columns) != 4 or {s for _, s in columns} != set(COLUMNS):
        raise ValueError('Expected each supported quantity exactly once.')
    records, labels = [], set()
    cell = re.compile(rf'({NUMBER})(?:\s*±\s*({NUMBER}))?(?=\s|$)\s*')
    for line in lines[index+1:]:
        first = re.match(r'([A-Za-z][\w.-]*)\s+', line)
        if not first or first[1] in labels:
            raise ValueError('Invalid or duplicate sample row.')
        label = first[1]
        labels.add(label)
        tail = line[first.end():]
        for raw_symbol, symbol in columns:
            match = cell.match(tail)
            if not match:
                raise ValueError('Missing or unsupported numeric cell.')
            center, spread = match.groups()
            value = float(center)
            if not math.isfinite(value) or value < 0 or (symbol == 'EL' and value > 100):
                raise ValueError('Value outside the supported range.')
            if spread is not None and not math.isfinite(float(spread)):
                raise ValueError('Nonfinite uncertainty.')
            unit, metric, definition = COLUMNS[symbol]
            records.append(dict(sample_label=label, source_symbol=raw_symbol, metric=metric,
                definition=definition, unit=unit, value=value, value_text=center,
                uncertainty_text=spread or '', uncertainty_kind='unspecified' if spread is not None else 'none',
                n_text='', raw_cell=match[0].strip(), raw_row=line, review_status='needs_review'))
            tail = tail[match.end():]
        if tail.strip():
            raise ValueError('Unexpected extra row content.')
    if not labels:
        raise ValueError('No sample rows.')
    return dict(records=records, sample_count=len(labels), header=lines[index], warnings=warnings)


def match_reference(record, paper, master, *, allow_legacy_impact_label=False):
    if master.get('paper_short_id') != paper:
        raise ValueError('Cross-paper mapping is forbidden.')
    output = dict(sample_ids=[], target_path=None, status='sample_unmatched', comparison=None,
                  human_review_required=True, conditions_verified=False, warnings=[])
    samples = [s for s in master.get('samples', []) if s.get('sample_label') == record['sample_label']]
    if len(samples) != 1:
        output['status'] = 'sample_ambiguous' if samples else 'sample_unmatched'
        return output
    sample_id = samples[0]['sample_id']
    output['sample_ids'] = [sample_id]
    names = {record['metric']}
    if record['metric'] == 'impact_energy' and allow_legacy_impact_label:
        names.add('impact toughness')
    targets = [(i, r) for i, r in enumerate(master.get('sample_property_value_records', []))
               if r.get('sample_id') == sample_id and r.get('property_name') in names]
    if len(targets) != 1:
        output['status'] = 'target_ambiguous' if targets else 'outside_existing_targets'
        return output
    i, target = targets[0]
    output.update(target_path=f'/sample_property_value_records/{i}/value', status='mapping_needs_review',
                  reference_value=target.get('value'), reference_unit=target.get('unit'))
    if target.get('property_name') == 'impact toughness':
        output['warnings'].append('Explicit legacy label mapping: master impact toughness (J) to source impact energy (J). Not KIC; definition requires review.')
    unit = target.get('unit')
    if {'Mpa': 'MPa'}.get(unit, unit) != record['unit']:
        output['status'] = 'unit_mismatch'
        return output
    old = target.get('value')
    if not isinstance(old, (int, float)) or isinstance(old, bool) or not math.isfinite(old):
        output['status'] = 'non_numeric_reference'
        return output
    output['comparison'] = dict(exact_match=old == record['value'], difference=record['value']-old,
                                scope='central_table_value_transcription_only_not_uncertainty_or_scientific_validity')
    return output
