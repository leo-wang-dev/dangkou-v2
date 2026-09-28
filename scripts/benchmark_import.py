"""Reproducible structural/authorized model benchmark; source files are read-only.

Default mode never calls a model. --parse requires an approved template JSON.
Physical candidate rows do not establish a logical product count.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catalog import ingest, workbook_templates, dynamic_import


def run(args):
    source = Path(args.source).resolve()
    checksum = ingest._sha256(source)
    if args.sha256 and checksum != args.sha256:
        raise ValueError('Source SHA-256 mismatch')
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='catalog-benchmark-') as directory:
        owned = Path(directory) / source.name
        shutil.copyfile(source, owned)
        converted = ingest._ensure_xlsx(str(owned))
        conversion = time.monotonic() - started
        stamp = time.monotonic()
        sheets = workbook_templates.discover_workbook(converted)
        discovery = time.monotonic() - stamp
        report = {'source': source.name, 'sha256': checksum, 'mode': 'structural-only',
            'timing_seconds': {'conversion': round(conversion, 3), 'discovery': round(discovery, 3)},
            'sheets': [{'sheet': sheet['source_sheet'], 'header_row': sheet['header_row'],
                'physical_candidate_rows': len(sheet['rows']), 'image_objects': sheet['image_count'],
                'source_rows': [row['source_row'] for row in sheet['rows']]} for sheet in sheets],
            'logical_product_target': args.expected_products, 'target_seconds': args.target_seconds,
            'note': 'Physical rows are source evidence, not a verified logical product count. No model run in structural-only mode.'}
        if args.parse:
            if not args.template_json:
                raise ValueError('--parse requires --template-json with the approved category schema')
            template = json.loads(Path(args.template_json).read_text())
            stamp = time.monotonic()
            rows = dynamic_import._agent_rows(template, converted, Path(directory) / 'work')
            report.update(mode='model', parsed_products=len(rows), failures=rows.failures, coverage=rows.coverage)
            report['timing_seconds']['model_validation'] = round(time.monotonic()-stamp, 3)
            report['note'] = 'Model output requires merchant review of grouping and image association.'
        report['timing_seconds']['total'] = round(time.monotonic()-started, 3)
        report['target_verified'] = bool(args.parse and len(rows) == args.expected_products and not rows.failures
            and not rows.coverage.get('uncertain') and time.monotonic()-started <= args.target_seconds)
        return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('--sha256')
    parser.add_argument('--expected-products', type=int, choices=(18, 30))
    parser.add_argument('--target-seconds', type=int, default=600)
    parser.add_argument('--parse', action='store_true')
    parser.add_argument('--template-json')
    print(json.dumps(run(parser.parse_args()), ensure_ascii=False, indent=2))
