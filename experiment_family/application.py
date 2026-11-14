import argparse
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

from .confirmatory import DECISION_RULE, ROLES, evaluate, readiness, spending_table
from .domain import run
from .storage import AnalysisStore, read_dataset


ROOT = Path(__file__).resolve().parents[1]
MAX_LOOKS = 20


def parse_cutoff(data, value):
    try:
        if isinstance(value, bool) or float(value) != int(value):
            raise ValueError()
        cutoff = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('分析时点须为整数') from None
    if not data['meta']['min_cutoff'] <= cutoff <= data['meta']['max_cutoff']:
        raise ValueError('分析时点超出资料范围')
    return cutoff


def parse_group(data, value):
    if not isinstance(value, str) or value not in ['', *data['meta']['groups']]:
        raise ValueError('未知分析范围')
    return value


def parse_real(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not (0 <= value < float('inf')):
        raise ValueError(f'{label}须为非负实数')
    return float(value)


class Application:
    def __init__(self, dataset, database, assets):
        self.dataset = Path(dataset)
        self.store = AnalysisStore(database)
        self.assets = Path(assets)

    def catalog(self):
        data, digest = read_dataset(self.dataset)
        return {
            **data['meta'],
            'digest': digest,
            'tables': {name: len(rows) for name, rows in data['tables'].items()},
            'metrics': data['tables']['metrics'],
            'arms': sorted({row['arm'] for row in data['tables']['assignments']}),
            'decision_rule': DECISION_RULE,
        }

    def analyze(self, supplied):
        if not isinstance(supplied, dict) or set(supplied) - {'cutoff', 'group'}:
            raise ValueError('只接受 cutoff 和 group 筛选项')
        data, digest = read_dataset(self.dataset)
        cutoff = parse_cutoff(data, supplied.get('cutoff', data['meta']['default_cutoff']))
        group = parse_group(data, supplied.get('group', ''))
        options = {'cutoff': cutoff, 'group': group}
        result = run(data['tables'], options)
        return {'digest': digest, 'options': options, 'result': result}

    # ---- 确认性计划 ---------------------------------------------------------

    def build_design(self, supplied):
        if not isinstance(supplied, dict):
            raise ValueError('计划选项须为对象')
        allowed = {'options', 'preregistered'}
        if set(supplied) - allowed:
            raise ValueError('计划请求字段无效')
        options = supplied.get('options')
        if not isinstance(options, dict):
            raise ValueError('计划选项须为对象')
        if set(options) - {'cutoff', 'group', 'control', 'treatments', 'metrics', 'roles', 'alpha', 'max_looks', 'min_observed', 'max_missing_rate'}:
            raise ValueError('计划选项包含未知字段')
        preregistered = supplied.get('preregistered', False)
        if not isinstance(preregistered, bool):
            raise ValueError('preregistered 须为布尔值')

        data, digest = read_dataset(self.dataset)
        tables = data['tables']
        cutoff = parse_cutoff(data, options.get('cutoff', data['meta']['default_cutoff']))
        group = parse_group(data, options.get('group', ''))
        if not any(
            row['assigned_at'] + metric['maturity_days'] <= data['meta']['max_cutoff']
            for row in tables['assignments']
            for metric in tables['metrics']
        ):
            raise ValueError('资料范围内没有指标能够成熟，无法建立计划')
        arms = sorted({row['arm'] for row in tables['assignments']})
        control = options.get('control')
        if control not in arms:
            raise ValueError('对照方案不存在')
        treatments = options.get('treatments')
        if not isinstance(treatments, list) or not treatments:
            raise ValueError('至少选择一个待评方案')
        if any(not isinstance(arm, str) or arm not in arms for arm in treatments):
            raise ValueError('待评方案不存在')
        if len(set(treatments)) != len(treatments):
            raise ValueError('待评方案不能重复')
        if control in treatments:
            raise ValueError('对照方案不能同时作为待评方案')

        metric_map = {row['metric']: row for row in tables['metrics']}
        requested = options.get('metrics')
        if not isinstance(requested, list) or not requested:
            raise ValueError('至少选择一个指标')
        if any(not isinstance(metric, str) for metric in requested):
            raise ValueError('指标选择无效')
        if len(set(requested)) != len(requested) or any(metric not in metric_map for metric in requested):
            raise ValueError('指标选择无效')

        roles = options.get('roles', {})
        if not isinstance(roles, dict) or any(not isinstance(metric, str) for metric in roles) or set(roles) - set(requested):
            raise ValueError('指标角色无效')
        metrics = []
        for metric in requested:
            definition = metric_map[metric]
            role = roles.get(metric, 'guardrail' if definition['direction'] == 'lower' else 'benefit')
            if role not in ROLES:
                raise ValueError('指标角色无效')
            if definition['direction'] not in ('higher', 'lower'):
                raise ValueError('指标方向无效')
            metrics.append({
                'metric': metric,
                'name': definition.get('name', metric),
                'direction': definition['direction'],
                'margin': parse_real(definition['margin'], '业务门槛'),
                'maturity_days': int(definition['maturity_days']),
                'role': role,
            })

        alpha = parse_real(options.get('alpha', 0.05), 'α')
        if not 0 < alpha < 0.5:
            raise ValueError('α 须严格位于 0 与 0.5 之间')
        max_looks = options.get('max_looks', 3)
        if isinstance(max_looks, bool) or not isinstance(max_looks, int) or not 1 <= max_looks <= MAX_LOOKS:
            raise ValueError(f'计划查看次数须为一到 {MAX_LOOKS} 的整数')
        min_observed = options.get('min_observed', 2)
        if isinstance(min_observed, bool) or not isinstance(min_observed, int) or min_observed < 2:
            raise ValueError('观测下限须为不小于 2 的整数')
        max_missing = parse_real(options.get('max_missing_rate', 0.2), '缺失率上限')
        if max_missing >= 1:
            raise ValueError('缺失率上限须小于 1')

        design = {
            'control': control,
            'treatments': treatments,
            'group': group,
            'metrics': metrics,
            'alpha': alpha,
            'max_looks': max_looks,
            'min_observed': min_observed,
            'max_missing_rate': max_missing,
            'decision_rule': DECISION_RULE,
            'planned_at_cutoff': cutoff,
            'source': {'digest': digest, 'metrics': sorted(metric_map), 'arms': arms},
        }
        return design, digest, preregistered

    def create_design(self, payload):
        if not isinstance(payload, dict) or set(payload) - {'title', 'preregistered', 'options'}:
            raise ValueError('计划请求字段无效')
        title = payload.get('title')
        if not isinstance(title, str) or not title.strip() or len(title) > 100:
            raise ValueError('计划名称须为一到一百个字符')
        design, digest, preregistered = self.build_design({'options': payload.get('options', {}), 'preregistered': payload.get('preregistered', False)})
        prior = [item for item in self.store.designs_for_digest(digest) if item['confirmatory'] and item['superseded_by'] is None]
        if preregistered and prior and prior[0]['looks_used']:
            raise ValueError(f'同一资料的计划「{prior[0]["title"]}」已经打开过结果；改动后的计划只能作为探索保存')
        if preregistered and prior:
            note = f'同一资料已有未打开结果的计划「{prior[0]["title"]}」；本计划替换其确认性额度，原计划不能再查看结果。'
        elif preregistered:
            note = '看结果前保存；本次分析为确认性，按计划中的错误率分配判定。'
        else:
            note = '结果已可查看后才保存；本计划标记为探索，不产生确认性结论。'
        created = self.store.save_design(title, digest, design, preregistered, note)
        if preregistered and prior:
            self.store.supersede(prior[0]['id'], created['id'])
            created = self.store.get_design(created['id'])
        return created

    def look(self, design_id, payload):
        if not isinstance(payload, dict) or set(payload) - {'cutoff'}:
            raise ValueError('查看请求只接受 cutoff')
        details = self.store.get_design(design_id)
        design = details['design']
        if not details['confirmatory']:
            raise ValueError('探索计划不提供确认性分析；请在看结果前保存计划')
        if details['superseded_by']:
            raise ValueError('该计划已被替换，不能再查看结果')
        if details['looks_used'] >= design['max_looks']:
            raise ValueError('计划查看次数已用尽；继续查看只能作为探索')
        data, digest = read_dataset(self.dataset)
        cutoff = parse_cutoff(data, payload.get('cutoff'))
        group = design['group']
        existing = self.store.find_look(design_id, cutoff, group, digest)
        if existing is not None:
            return self.enrich_look(details, existing)
        if details['looks'] and cutoff <= max(item['cutoff'] for item in details['looks']):
            raise ValueError('查看时点须晚于已打开过的时点，不能在已看过的资料上重新分配检验额度')

        index = details['looks_used'] + 1
        for _ in range(5):
            try:
                value = self.store.append_look(
                    design_id, cutoff, group, digest, index,
                    lambda look_index: evaluate(data['tables'], design, cutoff, look_index, digest),
                )
                return self.enrich_look(self.store.get_design(design_id), value)
            except ValueError as error:
                if str(error) != '查看次序已变化':
                    raise
                index = self.store.get_design(design_id)['looks_used'] + 1
        raise ValueError('查看冲突，请重试')

    def enrich_look(self, details, value):
        return {
            'design_id': details['id'],
            'title': details['title'],
            'look_id': value['id'],
            'look_index': value['look_index'],
            'reused': value.get('reused', False),
            'cutoff': value['cutoff'],
            'group': value['group'],
            'digest': value['digest'],
            'created_at': value['created_at'],
            'result': value['result'],
            'design': details['design'],
            'looks_remaining': details['looks_remaining'],
        }

    def preview(self, design_id, query):
        details = self.store.get_design(design_id)
        design = details['design']
        data, digest = read_dataset(self.dataset)
        cutoff = parse_cutoff(data, query.get('cutoff', data['meta']['default_cutoff']))
        index = min(details['looks_used'] + 1, design['max_looks'])
        report = readiness(data['tables'], design, cutoff)
        schedule = spending_table(design['alpha'], design['max_looks'])
        spending = schedule[index - 1]
        return {
            'design_id': details['id'],
            'confirmatory': False,
            'warning': '预备检查不消耗查看次数，也不展示效应与判定；实际查看才会形成确认性结论。',
            'digest': digest,
            'cutoff': cutoff,
            'group': design['group'],
            'look_index_if_opened': index,
            'readiness': report['readiness'],
            'immature': report['immature'],
            'fully_accrued': report['fully_accrued'],
            'accrual_end': report['accrual_end'],
            'error_budget': {
                'look_index': index,
                'information_fraction': spending['information_fraction'],
                'incremental_alpha': spending['incremental_alpha'],
                'comparisons': len(design['treatments']) * len(design['metrics']),
                'alpha_per_comparison': spending['incremental_alpha'] / (len(design['treatments']) * len(design['metrics'])),
                'schedule': schedule,
            },
            'looks_remaining': details['looks_remaining'],
        }

    def schedule(self, design_id):
        details = self.store.get_design(design_id)
        schedule = spending_table(details['design']['alpha'], details['design']['max_looks'])
        return {'design_id': details['id'], 'max_looks': details['design']['max_looks'], 'alpha': details['design']['alpha'], 'schedule': schedule}


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, application, **kwargs):
        self.application = application
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        return

    def send_json(self, status, value):
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        request = urlsplit(self.path)
        query = {key: values[-1] for key, values in parse_qs(request.query, keep_blank_values=True).items()}
        parts = [part for part in request.path.split('/') if part]
        try:
            if request.path == '/api/catalog':
                value = self.application.catalog()
            elif request.path == '/api/view':
                value = self.application.analyze(query)
            elif request.path == '/api/analyses':
                value = self.application.store.list()
            elif request.path.startswith('/api/analyses/'):
                value = self.application.store.get(request.path.rsplit('/', 1)[-1])
            elif request.path == '/api/designs':
                value = self.application.store.list_designs()
            elif len(parts) == 3 and parts[:2] == ['api', 'designs']:
                value = self.application.store.get_design(parts[2])
            elif len(parts) == 4 and parts[:2] == ['api', 'designs'] and parts[3] == 'preview':
                value = self.application.preview(parts[2], query)
            elif len(parts) == 4 and parts[:2] == ['api', 'designs'] and parts[3] == 'schedule':
                value = self.application.schedule(parts[2])
            elif len(parts) == 5 and parts[:2] == ['api', 'designs'] and parts[3] == 'looks':
                try:
                    index = int(parts[4])
                except ValueError:
                    raise KeyError('查看记录不存在') from None
                value = self.application.store.get_look(parts[2], index)
            elif request.path == '/api/source':
                data, digest = read_dataset(self.application.dataset)
                table = query.get('table', '')
                if table not in data['tables']:
                    raise KeyError('资料表不存在')
                value = {'digest': digest, 'rows': data['tables'][table]}
            elif request.path in {'/', '/app.js', '/style.css'}:
                name = 'index.html' if request.path == '/' else request.path[1:]
                payload = (self.application.assets / name).read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', {'index.html': 'text/html', 'app.js': 'text/javascript', 'style.css': 'text/css'}[name] + '; charset=utf-8')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            else:
                raise KeyError('路径不存在')
            self.send_json(200, value)
        except KeyError as error:
            self.send_json(404, {'error': str(error)})
        except (ValueError, TypeError) as error:
            self.send_json(400, {'error': str(error)})

    def do_POST(self):
        request = urlsplit(self.path)
        parts = [part for part in request.path.split('/') if part]
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 65536:
                raise ValueError('请求大小不合适')
            payload = json.loads(self.rfile.read(length))
            if parts == ['api', 'analyses']:
                if not isinstance(payload, dict) or set(payload) - {'title', 'options'}:
                    raise ValueError('保存请求字段无效')
                analyzed = self.application.analyze(payload.get('options', {}))
                value = self.application.store.save(payload.get('title'), analyzed['digest'], analyzed['options'], analyzed['result'])
                self.send_json(201, value)
            elif parts == ['api', 'designs']:
                value = self.application.create_design(payload)
                self.send_json(201, value)
            elif len(parts) == 4 and parts[:2] == ['api', 'designs'] and parts[3] == 'looks':
                value = self.application.look(parts[2], payload)
                self.send_json(200 if value['reused'] else 201, value)
            else:
                self.send_json(404, {'error': '路径不存在'})
        except KeyError as error:
            self.send_json(404, {'error': str(error)})
        except (ValueError, TypeError) as error:
            self.send_json(400, {'error': str(error)})


def server(application, host='127.0.0.1', port=0):
    return ThreadingHTTPServer((host, port), partial(Handler, application=application))


def main():
    parser = argparse.ArgumentParser(description='启动本地分析工作台')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', default=8080, type=int)
    parser.add_argument('--data', type=Path, default=ROOT / 'fixtures/data.json')
    parser.add_argument('--state', type=Path, default=Path(os.environ.get('APP_STATE', '.state/analyses.sqlite3')))
    parser.add_argument('--demo', action='store_true')
    args = parser.parse_args()
    application = Application(args.data, args.state, ROOT / 'web')
    httpd = server(application, args.host, 0 if args.demo else args.port)
    if args.demo:
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        base = f'http://{args.host}:{httpd.server_port}'
        try:
            with urlopen(base + '/api/view') as response:
                value = json.load(response)
            request = Request(base + '/api/analyses', data=json.dumps({'title': '当前资料摘要', 'options': value['options']}).encode(), headers={'Content-Type': 'application/json'})
            with urlopen(request) as response:
                saved = json.load(response)
            with urlopen(base + '/api/analyses/' + saved['id']) as response:
                restored = json.load(response)
            print(json.dumps({'当前摘要': value['result']['summary'], '资料指纹': value['digest'], '保存后相同': restored['result'] == value['result'], '说明': value['result']['notes']}, ensure_ascii=False, indent=2))
        finally:
            httpd.shutdown()
            worker.join()
            httpd.server_close()
    else:
        print(f'本地工作台：http://{args.host}:{httpd.server_port}', flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
