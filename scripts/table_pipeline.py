"""Configured table CLI. Extract reads PDF only; compare reads saved extraction and master."""
import argparse
import json
from pathlib import Path
import tempfile
from backend.table_pipeline import TableCase, extract_table, compare_table

ROOT = Path(__file__).resolve().parents[1]


def load_cases(path):
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if set(data) != {'schema_version', 'cases'} or data['schema_version'] != 'tifigure-table-cases/1':
        raise ValueError('Unsupported cases configuration.')
    cases = [TableCase.model_validate(c) for c in data['cases']]
    if not cases or len({c.id for c in cases}) != len(cases):
        raise ValueError('Cases must be nonempty and IDs unique.')
    return cases


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, suffix='.tmp', delete=False) as stream:
            temp_path = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
        temp_path.replace(path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def run_batch(stage, cases, source_root, output_dir, review_root=ROOT):
    items = []
    for case in cases:
        extraction_path = Path(output_dir)/(case.id+'-extraction.json')
        destination = Path(output_dir)/(case.id+'-'+('extraction' if stage=='extract' else 'comparison')+'.json')
        try:
            if stage == 'extract':
                artifact = extract_table(case.source, source_root)
                summary = dict(source_values=len(artifact['records']), rows=artifact['sample_count'])
            elif stage == 'compare':
                artifact = compare_table(case, extraction_path.read_bytes(), source_root, review_root)
                summary = artifact['summary']
            else:
                raise ValueError('Unknown stage.')
            write_json(destination, artifact)
            items.append(dict(case_id=case.id, status='ok', output=str(destination.resolve()), summary=summary))
        except (ValueError, OSError, KeyError, TypeError, ImportError) as error:
            items.append(dict(case_id=case.id, status='failed', error=str(error),
                notice='No current result produced. Any previous artifact for this case is stale until a successful rerun.'))
    manifest = dict(schema_version='tifigure-table-batch/1', stage=stage, items=items,
        succeeded=sum(r['status']=='ok' for r in items), failed=sum(r['status']=='failed' for r in items),
        human_approved=0, notice='No workspace or master changes. Known source conflicts remain pending.')
    write_json(Path(output_dir)/(stage+'-batch.json'), manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['list','extract','compare'])
    parser.add_argument('--config', type=Path, default=ROOT/'configs/table-cases.json')
    parser.add_argument('--case', default='all', help='Configured case ID, or all')
    parser.add_argument('--source-root', type=Path, help='Directory containing the configured paper folders. Required except for list.')
    parser.add_argument('--output-dir', type=Path, default=ROOT/'outputs/table-batch')
    parser.add_argument('--review-root', type=Path, default=ROOT, help='Base directory for source-review evidence files')
    args = parser.parse_args(argv)
    try:
        cases = load_cases(args.config)
        if args.case != 'all':
            cases = [c for c in cases if c.id == args.case]
            if not cases:
                raise ValueError('Unknown case ID; use list to inspect configured cases.')
        if args.stage == 'list':
            print(json.dumps([dict(id=c.id, paper=c.source.paper, table=c.source.table,
                page=c.source.page, parser=c.source.parser) for c in cases], indent=2))
            return 0
        if args.source_root is None:
            parser.error('--source-root is required for extract/compare.')
        result = run_batch(args.stage, cases, args.source_root, args.output_dir, args.review_root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result['failed'] else 0
    except (ValueError, OSError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    raise SystemExit(main())
