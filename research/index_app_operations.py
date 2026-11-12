#!/usr/bin/env python3
"""Offline Java-source operation index. Reads an existing JADX tree; never contacts devices."""
import argparse, bisect, collections, hashlib, json, pathlib, re
TOKEN=re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',re.S)
DECL=re.compile(r'(?m)^[ \t]*(?P<prefix>[\w$<>\[\].,? \t]+?\s+)(?P<name>[A-Za-z_$][\w$]*)\s*\((?P<args>[^{};]*?)\)\s*(?:throws\s+[\w$., \t]+)?(?P<term>[{;])')
CONTROL={'if','for','while','switch','catch','return','throw','new','super','this','synchronized'}
def mask(text):
    return TOKEN.sub(lambda m:''.join('\n' if c=='\n' else ' ' for c in m.group()),text)
def literal_safe(line):
    return re.sub(r'"(?:\\.|[^"\\])*"','"<literal>"',line)
def source_methods(text):
    code=mask(text);stack=[];pairs={};depth=[0]*(len(code)+1)
    for i,c in enumerate(code):
        depth[i]=len(stack)
        if c=='{':stack.append(i)
        elif c=='}' and stack:pairs[stack.pop()]=i
    depth[-1]=len(stack)
    newlines=[m.start() for m in re.finditer('\n',text)]
    line=lambda p:bisect.bisect_right(newlines,p)+1
    classes=[]
    for m in re.finditer(r'\b(class|interface|enum)\s+([\w$]+)[^;{}]*\{',code):
        op=m.end()-1
        if op in pairs:classes.append((op,pairs[op],m.group(2),m.group(1)))
    methods=[]
    for m in DECL.finditer(code):
        name=m.group('name');prefix=' '.join(m.group('prefix').split())
        if not prefix or name in CONTROL or prefix.split()[0] in CONTROL:continue
        owners=[c for c in classes if c[0]<m.start()<c[1]]
        if not owners:continue
        owner=max(owners,key=lambda c:c[0]);direct=depth[m.start()]==depth[owner[0]]+1
        term=m.end()-1;end=pairs.get(term,term)+1 if m.group('term')=='{' else term+1
        if name==owner[2]:continue
        methods.append({'name':name,'owner':owner[2],'owner_kind':owner[3],'direct':direct,'line':line(m.start()),'end_line':line(end),'start':m.start(),'body_start':term,'end':end,'signature':' '.join(text[m.start():m.end()].split()),'prefix':prefix,'has_body':m.group('term')=='{'})
    return code,methods

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source-root',required=True,type=pathlib.Path);ap.add_argument('--output',required=True,type=pathlib.Path);args=ap.parse_args();root=args.source_root
    selected=set()
    for rel in ['com/iermu','com/cms/iermu/cms','tv/danmaku/ijk/media/widget','tv/danmaku/ijk/media/audio','tv/danmaku/ijk/media/player']:
        selected.update((root/rel).rglob('*.java'))
    selected={p for p in selected if p.name not in {'R.java','BuildConfig.java','Manifest.java'} and '$$' not in p.name}
    data={};decomp=[]
    for p in sorted(selected):
        text=p.read_text(errors='replace');code,methods=source_methods(text)
        rel=str(p.relative_to(root));data[rel]={'text':text,'code':code,'methods':methods,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
        for n,l in enumerate(text.splitlines(),1):
            if 'Method not decompiled' in l:decomp.append({'file':rel,'line':n})
    routes={}
    for rel,d in data.items():
        if rel.endswith('/ApiRoute.java'):
            for name,value in re.findall(r'\bString\s+(\w+)\s*=\s*"([^"\n]+)"\s*;',d['text']):
                if value.startswith('/') and '?' not in value:routes['ApiRoute.'+name]=value
    contracts=[];missing=[]
    for rel,d in data.items():
        is_biz=bool(re.fullmatch(r'com/iermu/client/I[^/]+\.java',rel))
        is_lan=bool(re.fullmatch(r'com/iermu/opensdk/lan/[^/]+Api\.java',rel))
        is_http=rel.startswith('com/iermu/apiservice/service/')
        is_sdk=rel=='com/iermu/opensdk/ErmuOpenSDK.java'
        is_native=rel in {'com/iermu/nativesdk/NativeSDKEngine.java','com/iermu/nativesdk/CmsNative.java'}
        if not (is_biz or is_lan or is_http or is_sdk or is_native):continue
        kind='business' if is_biz else 'lan' if is_lan else 'http' if is_http else 'sdk_entry' if is_sdk else 'native_entry'
        direct=[m for m in d['methods'] if m['direct'] and (kind in {'business','lan','http'} or ('public' in m['prefix'] or 'native' in m['prefix']))]
        for m in direct:
            item={k:m[k] for k in ['name','owner','line','end_line','signature']};item.update({'file':rel,'kind':kind})
            if kind=='http':
                prev=max([x['end'] for x in d['methods'] if x['end']<m['start']],default=0)
                pre=d['text'][prev:m['start']]
                item['http_annotations']=[]
                item['unresolved_http_annotations']=[]
                for verb,expr in re.findall(r'@(GET|POST|PUT|DELETE|PATCH)\(([^)\n]+)\)',pre):
                    expr=expr.strip()
                    value=expr[1:-1] if expr.startswith('"') and expr.endswith('"') else routes.get(expr)
                    if value is not None:item['http_annotations'].append([verb,value])
                    else:item['unresolved_http_annotations'].append([verb,literal_safe(expr)])
                item['wire_fields']=re.findall(r'@(Field|Query|Path)\("([^"\n]+)"\)',m['signature'])
            contracts.append(item)
        # Unparsed-declaration check applies to interface-only contract files, not class statements/callbacks.
        if kind not in {'business','lan','http'}:continue
        covered={n for m in direct for n in range(m['line'],m['end_line']+1)}
        for n,l in enumerate(d['text'].splitlines(),1):
            if '(' in l and l.strip().endswith(';') and not l.lstrip().startswith(('import ','//','*')) and n not in covered:
                missing.append({'file':rel,'line':n,'declaration':literal_safe(l.strip())})
    names={c['name'] for c in contracts}
    extra={'getOpenDevRotateStruct','getCloseDevRotateStruct','getAddDevPresetPointStruct','getToDevPresetPointStruct','getSetDevMoveStruct','getIsRotateStruct','getIsSupportXYMoveStruct','getIsSupportPlatformStruct','getAirCapsuleStruct','getCardInforStruct','getDevInfoStruct','startAudio','stopAudio','pushAudio','startBaiduAudio','startLyyAudio','setDevParam','getDevParam','getNoLoginDevParam','cmsExecCMD','screenShot','startRecord','stopRecord','setWifi'}
    names|=extra
    call_re=re.compile(r'\b('+ '|'.join(sorted(names,key=len,reverse=True))+r')\s*\(')
    methods=[];refs=[];cms=[];const={}
    for rel,d in data.items():
        if rel.endswith('CmsProtocolDef.java'):
            for m in re.finditer(r'\b(?:int|byte|short)\s+(\w+)\s*=\s*(?:\(byte\)\s*)?(-?\d+|0x[0-9A-Fa-f]+)\s*;',d['code']):
                const['CmsProtocolDef.'+m.group(1)]=int(m.group(2),0)
    def number(expr):
        expr=re.sub(r'\((?:byte|short|int)\)','',expr).strip()
        if re.fullmatch(r'-?\d+|0x[0-9A-Fa-f]+',expr):return int(expr,0)
        return const.get(expr)
    for rel,d in data.items():
        for m in d['methods']:
            if not m['direct'] or not m['has_body']:continue
            body=d['code'][m['body_start']:m['end']]
            relevant=(rel.startswith(('com/iermu/client/business/impl/','com/iermu/client/business/api/','com/iermu/opensdk/lan/impl/','com/iermu/opensdk/setup/')) and (m['name'] in names or rel.endswith('Strategy.java'))) or rel.endswith(('LanUtil.java','CmsNetUtil.java'))
            found=[]
            for call in call_re.finditer(body):
                pos=m['body_start']+call.start()
                # The declaration itself is outside the body; calls are string/comment-masked.
                line=d['code'].count('\n',0,pos)+1
                receiver=d['code'][max(m['body_start'],pos-100):pos].split('\n')[-1].strip()
                rec={'target':call.group(1),'line':line,'receiver_context':receiver[-85:]}
                found.append(rec)
                if False:pass  # UI references are scanned globally below, including field initializers.
            if relevant:
                methods.append({'file':rel,'owner':m['owner'],'name':m['name'],'line':m['line'],'end_line':m['end_line'],'calls':found,'decompilation_incomplete':'Method not decompiled' in d['text'][m['body_start']:m['end']]})
            assignments=[]
            for a in re.finditer(r'([\w.]+)\.(cmsMainCmd|cmsSubCmd)\s*=\s*([^;]+);',body):
                assignments.append({'object':a.group(1),'field':a.group(2),'expression':' '.join(a.group(3).split()),'value':number(a.group(3)),'line':d['code'].count('\n',0,m['body_start']+a.start())+1})
            factories=[]
            for a in re.finditer(r'new\s+CmsCmdStruct\s*\(([^;\n]*)\)',body):
                factories.append({'arguments':' '.join(a.group(1).split()),'line':d['code'].count('\n',0,m['body_start']+a.start())+1})
            if assignments or factories:
                cms.append({'file':rel,'method':m['name'],'line':m['line'],'end_line':m['end_line'],'assignments':assignments,'constructors':factories})
    # Scan UI files globally: constructors / field initializer listeners must not be missed.
    refs=[]
    for rel,d in data.items():
        if not rel.startswith('com/iermu/ui/'):continue
        for call in call_re.finditer(d['code']):
            pos=call.start()
            if any(m['start']<=pos<=m['body_start'] for m in d['methods']):continue
            containers=[m for m in d['methods'] if m['body_start']<pos<m['end']]
            owner=min(containers,key=lambda m:m['end']-m['start']) if containers else None
            line=d['code'].count('\n',0,pos)+1
            receiver=d['code'][max(0,pos-100):pos].split('\n')[-1].strip()
            refs.append({'file':rel,'enclosing_method':owner['name'] if owner else '<constructor_or_field_initializer>','target':call.group(1),'line':line,'receiver_context':receiver[-85:]})
    ui_actions=[]
    for rel,d in data.items():
        if not rel.startswith('com/iermu/ui/'):continue
        for a in re.finditer(r'@OnClick\s*\(([^)]*)\)',d['text']):
            ui_actions.append({'file':rel,'line':d['text'].count('\n',0,a.start())+1,'kind':'OnClick_annotation','view_ids':re.findall(r'R\.id\.(\w+)',a.group(1))})
        for a in re.finditer(r'\bcase\s+R\.id\.(\w+)\s*[^:]*:',d['code']):
            ui_actions.append({'file':rel,'line':d['code'].count('\n',0,a.start())+1,'kind':'view_id_switch_case','view_ids':[a.group(1)]})
    report={'format_version':2,'source_root_label':'Existing com.intl.iermu 1.8.0_1224 (577) JADX tree','network_or_camera_requests_performed':False,'extraction_caveat':'Lexical source index, not Java compiler/type resolution. Method-name references are leads, not proof of reachable UI functionality. Native opaque paths and decompiler failures remain explicit.','scope':{'java_files_scanned':len(data),'ui_java_files_scanned':sum(r.startswith('com/iermu/ui/') for r in data),'contract_files':len({c['file'] for c in contracts}),'contract_methods_by_kind':dict(collections.Counter(c['kind'] for c in contracts))},'unparsed_contract_declarations':missing,'decompiler_failure_markers':decomp,'unparsed_check_scope':['business','lan','http'],'ui_action_sites':ui_actions,'contracts':contracts,'implementations':methods,'ui_references':refs,'cms_command_construction':cms,'cms_protocol_constants':const,'source_files_sha256':{r:d['sha256'] for r,d in data.items()}}
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'scope':report['scope'],'unparsed_contract_count':len(missing),'decompiler_failure_markers':len(decomp),'implementation_methods':len(methods),'ui_references':len(refs),'cms_methods':len(cms),'output_bytes':args.output.stat().st_size},ensure_ascii=False))
    if missing:print(json.dumps({'unparsed_contracts':missing},ensure_ascii=False))
if __name__=='__main__':main()
