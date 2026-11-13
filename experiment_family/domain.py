from collections import Counter, defaultdict
import math
from statistics import mean, median


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('数值必须为有限实数')
    return value


def selected(value, group):
    return not group or value == group


def latest(rows, key, revision, visible, cutoff):
    result = {}
    for row in rows:
        if row[visible] > cutoff:
            continue
        previous = result.get(row[key])
        if previous is None or row[revision] > previous[revision]:
            result[row[key]] = row
    return list(result.values())


def output(rows, summary, notes):
    return {'rows': rows, 'summary': summary, 'notes': notes}


def run(data, options):
    cutoff, group = options['cutoff'], options['group']
    assignments = {row['unit']: row for row in data['assignments'] if row['assigned_at'] <= cutoff and selected(row['segment'], group)}
    metrics = {row['metric']: row for row in data['metrics']}
    arms = sorted({row['arm'] for row in assignments.values()})
    rows = []
    for arm in arms:
        units = {key for key, value in assignments.items() if value['arm'] == arm}
        for metric, definition in metrics.items():
            eligible = {unit for unit in units if assignments[unit]['assigned_at'] + definition['maturity_days'] <= cutoff}
            values = [number(row['value']) for row in data['outcomes'] if row['unit'] in eligible and row['metric'] == metric and row['known_at'] <= cutoff and row['value'] is not None]
            rows.append(dict(arm=arm, metric=metric, assigned=len(units), mature=len(eligible), observed=len(values), missing=len(eligible) - len(values), average=mean(values) if values else None, direction=definition['direction'], margin=definition['margin']))
    return output(rows, {'分组用户': len(assignments), '分析时点': cutoff, '已打开次数': sum(day <= cutoff for day in data['looks'])}, ['均值不含显著性或上线决策；成熟但无结果的用户不补零。'])
