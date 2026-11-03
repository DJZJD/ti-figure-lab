"""Strict Table-4-like parser and condition matcher; not a general PDF reader."""
import re


def parse_heat_treatment_table(text):
    """Source-only extraction. No reference values or sample IDs are accepted."""
    if not all(s in re.sub(r'\s+', '', text) for s in ('Fracturetoughness', 'Heattreatmentconditions', 'MPa.m1/2')):
        raise ValueError('Unsupported table heading or unit; review the source.')
    rows = []
    number = r'\d+(?:\.\d+)?'
    pattern = re.compile(rf'(HT\d+)\s+({number})\s*°C\s*×\s*({number})h\s*\+\s*({number})\s*°C\s*×\s*({number})h\s*,\s*(AC|WQ|OQ|FC)\s+({number})')
    for raw in text.splitlines():
        if not raw.strip().startswith('HT'):
            continue
        normalized = raw.replace('(cid:2)', '°').replace('(cid:4)', '×').replace('þ', '+').strip()
        match = pattern.fullmatch(normalized)
        if not match:
            raise ValueError(f'Unrecognized treatment row: {raw}')
        label, st, sh, at, ah, cool, value = match.groups()
        rows.append(dict(row_label=label, raw_row=raw, normalized_row=normalized,
            solution_temperature_C=float(st), solution_time_h=float(sh),
            aging_temperature_C=float(at), aging_time_h=float(ah),
            final_cooling=cool, solution_cooling=None,
            definition='fracture_toughness', value_text=value, value=float(value),
            unit='MPa·m^0.5', unit_as_printed='MPa.m1/2', review_status='needs_review'))
    if not rows or len({r['row_label'] for r in rows}) != len(rows):
        raise ValueError('No rows or duplicate row labels; review the source.')
    return rows


def match_sample(row, samples):
    """Match only reported conditions within an already paper-scoped sample list."""
    matches = []
    for sample in samples:
        route = sample.get('processing_route', [])
        if len(route) != 2:
            continue
        st, at = route
        if (st.get('operation_standardized') == 'solution_treatment'
                and at.get('operation_standardized') == 'aging'
                and st.get('temperature_C') == row['solution_temperature_C']
                and st.get('hold_time_h') == row['solution_time_h']
                and at.get('temperature_C') == row['aging_temperature_C']
                and at.get('hold_time_h') == row['aging_time_h']
                and (at.get('cooling') or {}).get('mode') == row['final_cooling']):
            matches.append(sample['sample_id'])
    return dict(status='unique_reported_conditions_match' if len(matches)==1 else 'ambiguous' if matches else 'unmatched',
                sample_ids=matches, unverified_conditions=['solution_cooling', 'test_conditions'],
                human_review_required=True)
