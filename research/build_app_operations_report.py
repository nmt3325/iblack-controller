#!/usr/bin/env python3
"""Build derivative app-operation catalogues from an existing local JADX index.
No network, APK execution, camera access, or credential loading.
"""
import argparse
import collections
import hashlib
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from index_app_operations import source_methods


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cell(x):
    return str(x).replace('|', r'\|').replace('\n', ' ')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--index', type=pathlib.Path, required=True)
    ap.add_argument('--source-root', type=pathlib.Path, required=True)
    ap.add_argument('--output-root', type=pathlib.Path, required=True)
    ap.add_argument('--apk', type=pathlib.Path)
    ap.add_argument('--native-root', type=pathlib.Path)
    a = ap.parse_args()
    d = json.loads(a.index.read_text())
    docs = a.output_root / 'docs'
    evidence = a.output_root / 'evidence'
    docs.mkdir(parents=True, exist_ok=True)
    evidence.mkdir(parents=True, exist_ok=True)
    refs = collections.defaultdict(list)
    impl = collections.defaultdict(list)
    for r in d['ui_references']:
        refs[r['target']].append(r)
    for m in d['implementations']:
        impl[m['name']].append(m)
    lines = [
        '# アプリAPI・実装索引', '',
        '[操作の説明](APP-OPERATIONS.md) / [UI索引](APP-UI-INDEX.md)', '',
        '既存JADXツリーからの字句ベースの索引。同名参照は型解決したcall graphではない。',
        '宣言・オーバーロード・層の重複を含み、機能数や実機での対応数ではない。',
        '実際の認証値・APK本文・nativeバイナリーは含めない。ソース参照はAPK内のパスと行番号。', ''
    ]
    for kind in ['business', 'http', 'lan', 'sdk_entry', 'native_entry']:
        group = [c for c in d['contracts'] if c['kind'] == kind]
        lines += [f'## {kind} ({len(group)}宣言)', '']
        for file in sorted({c['file'] for c in group}):
            lines += ['### ' + pathlib.Path(file).stem, '', '`' + file + '`', '',
                      '| 行 | 宣言 | 通信・引数名 | UI同名参照 | 実装候補 |',
                      '| --- | --- | --- | --- | --- |']
            for c in [x for x in group if x['file'] == file]:
                route = '; '.join(' '.join(x) for x in c.get('http_annotations', []))
                unresolved = c.get('unresolved_http_annotations', [])
                if unresolved:
                    route += '; unresolved=' + str(unresolved)
                fields = ', '.join(x[1] for x in c.get('wire_fields', []))
                route = route + ((' / ' + fields) if fields else '')
                ur = '; '.join(f"{x['file']}:{x['line']}" for x in refs[c['name']]) or '参照なし（未実装の証明ではない）'
                ir = '; '.join(f"{x['file']}:{x['line']}–{x['end_line']}" for x in impl[c['name']]) or 'この抽出範囲の候補なし'
                lines.append('| ' + ' | '.join(cell(x) for x in [c['line'], '`' + c['signature'] + '`', route, ur, ir]) + ' |')
            lines.append('')
    lines += ['## CMS組立ての全検出箇所', '',
              'main/subは代入**順**を保持する。最後の値だけに潰さない。',
              'ラッパーの自動saveや動的な式は、下記だけでは表現できない。', '']
    for c in d['cms_command_construction']:
        vals = '; '.join(f"L{x['line']} {x['object']}.{x['field']}={x['expression']}" + (f" [{x['value']}]" if x['value'] is not None else ' [未解決]') for x in c['assignments'])
        constructors = '; '.join(f"L{x['line']} new CmsCmdStruct({x['arguments']})" for x in c['constructors'])
        lines += [f"- `{c['file']}:{c['line']}–{c['end_line']} {c['method']}`: " + cell('; '.join(x for x in [vals, constructors] if x))]
    (docs / 'APP-API-INDEX.md').write_text('\n'.join(lines).rstrip() + '\n')

    lines = ['# アプリUI操作索引', '', '[操作の説明](APP-OPERATIONS.md) / [API索引](APP-API-INDEX.md)', '',
             'クリック注釈とview IDのswitchを機械的に抽出。タッチ、ジェスチャー、動的生成、権限・機種条件を完全に網羅する証明ではない。',
             '同名API参照を併記するが、Javaの型解決・画面の実機到達性は保証しない。', '']
    by_file = collections.defaultdict(list)
    for q in d.get('ui_action_sites', []):
        by_file[q['file']].append(q)
    for q in d['ui_references']:
        by_file.setdefault(q['file'], [])
    for f in sorted(by_file):
        lines += ['## ' + f, '']
        for q in by_file[f]:
            lines.append(f"- L{q['line']} {q['kind']}: `" + '`, `'.join(q['view_ids']) + '`')
        rr = [q for q in d['ui_references'] if q['file'] == f]
        if rr:
            lines += ['', '参照：' + '; '.join(f"L{q['line']} `{q['target']}` ({q['enclosing_method']})" for q in rr)]
        lines.append('')
    (docs / 'APP-UI-INDEX.md').write_text('\n'.join(lines).rstrip() + '\n')

    constants_cache = {}
    def resolve_key(expr):
        expr = expr.strip()
        if re.fullmatch(r'"[^"\n]+"', expr):
            return expr[1:-1]
        pieces = expr.split('.')
        if len(pieces) < 2:
            return None
        owner, name = pieces[0], pieces[-1]
        if owner not in constants_cache:
            values = collections.defaultdict(set)
            for p in a.source_root.rglob(owner + '.java'):
                s = p.read_text(errors='replace')
                for n, v in re.findall(r'\bString\s+(\w+)\s*=\s*"([^"\n]*)"\s*;', s):
                    values[n].add(v)
            constants_cache[owner] = values
        choices = constants_cache[owner].get(name, set())
        return next(iter(choices)) if len(choices) == 1 else None

    cloud = []
    api_rel = 'com/iermu/client/business/api/CamSettingApi.java'
    p = a.source_root / api_rel
    text = p.read_text()
    _, methods = source_methods(text)
    for m in methods:
        if not m['has_body'] or not m['direct']:
            continue
        body = text[m['body_start']:m['end']]
        calls = []
        for q in re.finditer(r'\bmApiService\.(\w+)\(([^;\n]*)\);', body):
            first = re.match(r'\s*"([^"\n]+)"', q.group(2))
            calls.append({'service_method':q.group(1), 'rpc_method_literal':first.group(1) if first else None,
                          'source_arguments_expression':q.group(2), 'line':text.count('\n', 0, m['body_start']+q.start())+1})
        keys = []
        for q in re.finditer(r'\bjson\.put\(([^,;\n]+),\s*([^;\n]*)\);', body):
            keys.append({'key_expression':q.group(1).strip(), 'resolved_key':resolve_key(q.group(1)), 'value_expression':q.group(2).strip(),
                         'line':text.count('\n', 0, m['body_start']+q.start())+1})
        if calls or keys:
            cloud.append({'file':api_rel, 'method':m['name'], 'line':m['line'], 'end_line':m['end_line'], 'service_calls':calls, 'json_fields':keys})
    identity = {'package':'com.intl.iermu', 'version_name':'1.8.0_1224', 'version_code':577}
    if a.apk:
        identity['apk_sha256'] = digest(a.apk)
        identity['apk_bytes'] = a.apk.stat().st_size
    if a.native_root:
        identity['native_inputs_sha256'] = {p.name:digest(p) for p in sorted(a.native_root.glob('*.so'))}
    details = {
        'identity':identity, 'source_index_sha256':digest(a.index),
        'executed_new_camera_setting_writes':False, 'executed_destructive_operations':False, 'executed_speaker_transmission':False,
        'cam_setting_api_calls':cloud,
        'manually_verified_ui_values': {
            'nightmode':{'0':'Auto','1':'Turn off night vision','2':'Turn on night vision','source':'com/iermu/ui/fragment/camseting/CamNightFragment.java:78-96'},
            'exposemode':{'0':'Auto','1':'Highlight priority','2':'Lowlight priority','source':'com/iermu/ui/fragment/camseting/CamNightFragment.java:98-116'},
            'scene':{'0':'Indoor','1':'Outdoor','source':'com/iermu/ui/fragment/camseting/CamNightFragment.java:62-77'},
            'record_mode':{'0':'recordContinue','1':'recordChange','source':'com/iermu/ui/fragment/camseting/local/SdSettingFragment.java:355-362'},
            'live_bitlevel':{'0':'Low','1':'HD','2':'Super','send_condition':'isDoNekWork == true','source':'com/iermu/ui/fragment/live/MineLiveFragment.java:1523-1568'},
            'labels_source':['res/layout/setting_night_exposure.xml','res/values/strings.xml']
        },
        'side_effects':[
            {'operation':'getLanPlayUrl','wire':'75/54','source':'com/iermu/opensdk/lan/impl/LanDevPlatformImpl.java:235','note':'get name does not imply read-only'},
            {'operation':'getSmbFolder','source':'com/iermu/opensdk/lan/impl/NasDevImpl.java:47-124','note':'Phone SMB listing may create/delete iermutest to check write access'},
            {'operation':'setDevParam','source':'com/cms/iermu/cms/CmsNetUtil.java:109-172','note':'main 75 auto-saves via 65/3 except sub 54/64/62/42'},
            {'operation':'setRecordType','source':'com/iermu/opensdk/lan/impl/NasDevImpl.java:829-870','note':'6-byte zero-filled payload; only byte5 bit7 configured; explicit save in addition to wrapper'},
            {'operation':'formatSdc','wire':'75/83','source':'com/iermu/opensdk/lan/impl/NasDevImpl.java:872-899','note':'Destructive; not executed'},
            {'operation':'kernelUpgrade','wire':'75/42','source':'com/iermu/opensdk/lan/impl/NasDevImpl.java:719-746','note':'HTTP update, port80/type2; not executed'},
            {'operation':'PTZ','source':'com/iermu/client/business/impl/CloudPlatformBusImpl.java:50-280','note':'camIsInLan branch; hardware support and fallback response not verified'},
            {'operation':'talkback','source':'com/iermu/ui/fragment/live/MineLiveFragment.java:1075-1128','note':'Network, microphone permission, delayed500ms press/release; no audio transmitted'}
        ],
        'decompiler_failure_markers':d['decompiler_failure_markers'],
        'limitations':[d['extraction_caveat'], 'No new hardware applicability tests; no exhaustive native/third-party/server coverage']
    }
    (evidence / 'app-operations-details.json').write_text(json.dumps(details, ensure_ascii=False, indent=2)+'\n')
    summary = {
        'identity':{k:v for k,v in identity.items() if k != 'native_inputs_sha256'},
        'scope':d['scope'], 'contract_declarations_total':len(d['contracts']),
        'ui_action_sites':len(d.get('ui_action_sites', [])),
        'unique_view_ids':len({k for q in d.get('ui_action_sites', []) for k in q['view_ids']}),
        'ui_method_name_references':len(d['ui_references']),
        'cms_construction_methods':len(d['cms_command_construction']),
        'unparsed_interface_declarations':len(d['unparsed_contract_declarations']),
        'unresolved_http_paths':sum(bool(c.get('unresolved_http_annotations')) for c in d['contracts']),
        'decompiler_failure_markers':d['decompiler_failure_markers'],
        'unique_user_feature_count':'not_claimed; declarations overlap layers and include helpers',
        'new_camera_settings_written':False, 'destructive_operations_executed':False, 'speaker_transmission_executed':False,
        'native_3356_concurrency_tested':False,
        'important_findings':['nightmode 0=auto,1=off,2=on','exposemode 0=auto,1=highlight-priority,2=lowlight-priority',
             'many camera settings use cloud RPC /v2/pcs/device; literal form key fileds',
             'PTZ branches on camIsInLan; presets metadata still uses cloud',
             'setDevParam main75 auto-save except sub54/64/62/42',
             'SMB getSmbFolder may create/delete a test file','getLanPlayUrl sends75/54; names are not safety classifications'],
        'limitations':details['limitations'],
        'source_index_sha256':digest(a.index),
        'cam_setting_rpc_methods_indexed':len(cloud)
    }
    (evidence / 'app-operations-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'outputs':[str(p.relative_to(a.output_root)) for p in sorted(a.output_root.rglob('*')) if p.is_file()],
        'contracts':len(d['contracts']), 'cam_setting_methods':len(cloud), 'unresolved_json_keys':sum(f['resolved_key'] is None for m in cloud for f in m['json_fields'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
