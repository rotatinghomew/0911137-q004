"""确认性实验分析：预注册假设族、查看消耗与联合上线判定。

本模块只做纯计算，不读写存储。错误率口径完全由 ``spending_table`` 暴露：

* 计划最多查看 ``K`` 次，第 ``k`` 次的累计 α 由单侧 O'Brien–Fleming
  消耗函数 ``S(t) = 1 - Φ(Φ^{-1}(1-α) / √t)`` 给出，``t = k / K``；
* 第 ``k`` 次的增量 ``α_inc(k) = S(k/K) - S((k-1)/K)``；
* 该次共有 ``M`` 个比较（方案 × 指标），每个比较使用单侧水平
  ``α_inc(k) / M``；由 Bonferroni 与并集界，跨全部查看与比较的
  第一类错误不超过 ``Σ α_inc(k) = α``。

共用对照会令比较之间正相关，Bonferroni 因此偏保守但不会失效。
"""

import math
from statistics import NormalDist

from .domain import number


NORMAL = NormalDist()
DIRECTIONS = ('higher', 'lower')
ROLES = ('benefit', 'guardrail')

DECISION_RULE = (
    '一个方案可以上线，当且仅当它的全部收益比较与保护比较在本次查看的调整阈值下同时通过。'
    '点估计已越过业务门槛而区间尚未越过时，记为「尚不能判断」；保护指标未成熟、观测不足或缺失过多时也只能等待。'
    '若还有方案尚不能判断，本次不给出合并的上线建议；查看额度用尽后仍未判定的，结论为「证据不足」。'
    '全部方案都可判定后：恰有一个通过则建议上线；多个通过时计划未预注册方案间比较，视为不能唯一确定；'
    '没有通过则本次资料不支持上线。人群、指标或方案族与计划不一致的结果只作探索。'
)

SUMMARY_STATUS = {
    'immature': '尚未成熟',
    'insufficient': '观测不足',
    'unreliable': '缺失过多',
    'ready': '可判定',
}
VERDICT_LABELS = {'pass': '通过', 'fail': '未达门槛', 'unconfirmed': '尚不能判断'}
ARM_STATUS_LABELS = {'recommendable': '可上线', 'waiting': '尚不能判断', 'not_met': '未达门槛'}
DECISION_LABELS = {
    'recommend': '可以上线',
    'wait': '尚不能判断',
    'ambiguous': '尚不能唯一确定',
    'none': '不支持上线',
    'inconclusive': '证据不足',
}


def cumulative_spending(alpha, fraction):
    """单侧 O'Brien–Fleming 累计 α 消耗。"""
    if not 0 < alpha < 1:
        raise ValueError('α 必须严格位于 0 与 1 之间')
    if fraction <= 0:
        return 0.0
    if fraction >= 1:
        return alpha
    return 1 - NORMAL.cdf(NORMAL.inv_cdf(1 - alpha) / math.sqrt(fraction))


def spending_table(alpha, max_looks):
    """返回计划中每一次查看的 α 分配，供页面和测试直接复核。"""
    if isinstance(max_looks, bool) or not isinstance(max_looks, int) or max_looks < 1:
        raise ValueError('计划查看次数须为正整数')
    rows = []
    previous = 0.0
    for look in range(1, max_looks + 1):
        fraction = look / max_looks
        cumulative = cumulative_spending(alpha, fraction)
        incremental = cumulative - previous
        rows.append({'look': look, 'information_fraction': fraction, 'cumulative_alpha': cumulative, 'incremental_alpha': incremental})
        previous = cumulative
    return rows


def _summaries(tables, design, cutoff):
    assignments = {}
    for row in tables['assignments']:
        if row['assigned_at'] > cutoff:
            continue
        if design['group'] and row['segment'] != design['group']:
            continue
        assignments[row['unit']] = row

    latest = {}
    for row in tables['outcomes']:
        if row['known_at'] > cutoff:
            continue
        key = (row['metric'], row['unit'])
        previous = latest.get(key)
        if previous is None or row['known_at'] > previous['known_at']:
            latest[key] = row

    result = {}
    for arm in [design['control'], *design['treatments']]:
        arm_units = [unit for unit, row in assignments.items() if row['arm'] == arm]
        for metric in design['metrics']:
            mature_units = [unit for unit in arm_units if assignments[unit]['assigned_at'] + metric['maturity_days'] <= cutoff]
            values = []
            pending = 0
            nulls = 0
            for unit in mature_units:
                row = latest.get((metric['metric'], unit))
                if row is None:
                    pending += 1
                elif row['value'] is None:
                    nulls += 1
                else:
                    values.append(number(row['value']))
            result[(arm, metric['metric'])] = _summary(
                values, len(mature_units), len(arm_units), pending, nulls,
                design['min_observed'], design['max_missing_rate'],
            )
    return result


def _summary(values, mature, assigned, pending, nulls, min_observed, max_missing_rate):
    observed = len(values)
    missing = mature - observed
    average = sum(values) / observed if observed else None
    variance = sum((value - average) ** 2 for value in values) / (observed - 1) if observed > 1 else None
    deviation = math.sqrt(variance) if variance is not None else None
    missing_rate = missing / mature if mature else None
    if mature == 0:
        status = 'immature'
    elif observed < min_observed:
        status = 'insufficient'
    elif missing_rate is not None and missing_rate > max_missing_rate:
        status = 'unreliable'
    else:
        status = 'ready'
    return {
        'assigned': assigned,
        'mature': mature,
        'observed': observed,
        'missing': missing,
        'null': nulls,
        'pending': pending,
        'missing_rate': missing_rate,
        'average': average,
        'deviation': deviation,
        'status': status,
        'status_label': SUMMARY_STATUS[status],
    }


def _blocker(summary, label):
    if summary['status'] == 'immature':
        return f'{label}尚未成熟（成熟样本 {summary["mature"]}，观测 {summary["observed"]}）'
    if summary['status'] == 'insufficient':
        return f'{label}观测不足（{summary["observed"]} < 计划下限）'
    if summary['status'] == 'unreliable':
        return f'{label}缺失过多（缺失率 {summary["missing_rate"]:.1%} 超过计划上限）'
    return ''


def _accrual_end(tables, design, cutoff):
    """当前人群与方案族在计划中最长成熟期下的最后一个成熟时点。"""
    horizon = max(metric['maturity_days'] for metric in design['metrics'])
    latest = None
    for row in tables['assignments']:
        if design['group'] and row['segment'] != design['group']:
            continue
        if row['arm'] not in [design['control'], *design['treatments']]:
            continue
        if row['assigned_at'] > cutoff:
            continue
        end = row['assigned_at'] + horizon
        if latest is None or end > latest:
            latest = end
    return latest


def readiness(tables, design, cutoff):
    """只检查成熟度与错误率，不计算任何效应或判定，供预备检查使用。"""
    summaries = _summaries(tables, design, cutoff)
    accrual_end = _accrual_end(tables, design, cutoff)
    return {
        'readiness': [
            {'arm': arm, 'metric': metric, **{key: item[key] for key in ('assigned', 'mature', 'observed', 'missing', 'null', 'pending', 'missing_rate', 'status', 'status_label')}}
            for (arm, metric), item in summaries.items()
        ],
        'immature': [
            {'arm': arm, 'metric': metric_id, 'status': summaries[(arm, metric_id)]['status']}
            for arm in [design['control'], *design['treatments']]
            for metric_id in [item['metric'] for item in design['metrics']]
            if summaries[(arm, metric_id)]['status'] != 'ready'
        ],
        'accrual_end': accrual_end,
        'fully_accrued': accrual_end is not None and cutoff >= accrual_end,
    }


def evaluate(tables, design, cutoff, look_index, digest):
    """在给定资料快照和查看次序上执行一次确认性分析。

    ``design`` 必须是计划保存时的快照，指标方向、margin 与成熟天数不会
    随之后替换的资料定义而改变。返回值不含任何指针，可直接冻结保存。
    """
    alpha = design['alpha']
    table = spending_table(alpha, design['max_looks'])
    if isinstance(look_index, bool) or not isinstance(look_index, int) or not 1 <= look_index <= len(table):
        raise ValueError('查看次序超出计划范围')
    spending = table[look_index - 1]
    comparisons_count = len(design['treatments']) * len(design['metrics'])
    alpha_per_comparison = spending['incremental_alpha'] / comparisons_count
    if not 0 < alpha_per_comparison < 1:
        raise ValueError('本次查看的调整水平无效')
    critical = NORMAL.inv_cdf(1 - alpha_per_comparison)
    summaries = _summaries(tables, design, cutoff)
    looks_remaining = design['max_looks'] - look_index

    comparisons = []
    arms = []
    for arm in design['treatments']:
        arm_comparisons = []
        for metric in design['metrics']:
            control = summaries[(design['control'], metric['metric'])]
            treatment = summaries[(arm, metric['metric'])]
            sign = 1 if metric['direction'] == 'higher' else -1
            blocked = []
            for summary, label in ((control, '对照组'), (treatment, f'{arm} 组')):
                if summary['status'] != 'ready':
                    blocked.append(_blocker(summary, label))
            ready = not blocked
            effect = treatment['average'] - control['average'] if ready and treatment['average'] is not None and control['average'] is not None else None
            oriented_point = sign * (effect - metric['margin']) if effect is not None else None
            row = {
                'arm': arm,
                'metric': metric['metric'],
                'name': metric['name'],
                'role': metric['role'],
                'direction': metric['direction'],
                'margin': metric['margin'],
                'control': control,
                'treatment': treatment,
                'effect': effect,
                'oriented_effect': oriented_point,
                'standard_error': None,
                'z': None,
                'p_value': None,
                'alpha_per_comparison': alpha_per_comparison,
                'critical_value': critical,
                'effect_range': None,
                'oriented_bound': None,
                'pass': None,
                'missed': None,
                'verdict': None,
                'verdict_label': None,
                'status': 'ready' if ready else 'blocked',
                'reasons': blocked,
            }
            if ready:
                variance = 0.0
                for summary, count in ((treatment, treatment['observed']), (control, control['observed'])):
                    if summary['deviation'] is not None:
                        variance += summary['deviation'] ** 2 / count
                standard_error = math.sqrt(variance)
                if standard_error == 0:
                    row['status'] = 'degenerate'
                else:
                    z_value = oriented_point / standard_error
                    if metric['direction'] == 'higher':
                        effect_range = {'lower': effect - critical * standard_error, 'upper': None}
                    else:
                        effect_range = {'lower': None, 'upper': effect + critical * standard_error}
                    favorable = oriented_point - critical * standard_error > 0
                    unfavorable = oriented_point + critical * standard_error < 0
                    row.update(
                        standard_error=standard_error,
                        z=z_value,
                        p_value=1 - NORMAL.cdf(z_value),
                        effect_range=effect_range,
                        oriented_bound=oriented_point - critical * standard_error,
                    )
                    row['pass'] = bool(favorable)
                    row['missed'] = bool(unfavorable and not favorable)
                if row['pass']:
                    row['verdict'] = 'pass'
                    row['reasons'] = ['调整后区间越过业务门槛']
                elif row['status'] == 'degenerate':
                    row['verdict'] = 'unconfirmed'
                    row['reasons'] = ['两组观测均无变异，无法给出调整后区间']
                elif row.get('missed'):
                    row['verdict'] = 'fail'
                    position = '低于' if metric['direction'] == 'higher' else '高于'
                    row['reasons'] = [f'调整后区间整体{position}业务门槛 {metric["margin"]}']
                else:
                    row['verdict'] = 'unconfirmed'
                    row['reasons'] = [f'调整后区间仍覆盖业务门槛 {metric["margin"]}']
                row['verdict_label'] = VERDICT_LABELS[row['verdict']]
            comparisons.append(row)
            arm_comparisons.append(row)

        failures = [row for row in arm_comparisons if row['verdict'] == 'fail']
        unresolved = [row for row in arm_comparisons if row['verdict'] in (None, 'unconfirmed')]
        if failures:
            status = 'not_met'
            reasons = [f'{row["name"]}未达门槛（{row["reasons"][0]}）' for row in failures]
            if unresolved:
                reasons.append('另有指标尚不能判断：' + '、'.join(row['name'] for row in unresolved))
        elif unresolved:
            status = 'waiting'
            reasons = [f'{row["name"]}尚不能判断（{row["reasons"][0] if row["reasons"] else "证据不足"}）' for row in unresolved]
        else:
            status = 'recommendable'
            reasons = ['全部收益与保护比较通过调整后阈值']
        arms.append({
            'arm': arm,
            'status': status,
            'status_label': ARM_STATUS_LABELS[status],
            'failures': [row['metric'] for row in failures],
            'unresolved': [row['metric'] for row in unresolved],
            'blocked_metrics': [row['metric'] for row in arm_comparisons if row['status'] in ('immature', 'insufficient', 'unreliable')],
            'reasons': reasons,
        })

    unresolved_arms = [item for item in arms if item['status'] != 'recommendable']
    eligible = [item['arm'] for item in arms if item['status'] == 'recommendable']
    accrual_end = _accrual_end(tables, design, cutoff)
    fully_accrued = accrual_end is not None and cutoff >= accrual_end
    if unresolved_arms and looks_remaining > 0:
        decision = 'wait'
    elif unresolved_arms:
        decision = 'inconclusive'
    elif len(eligible) == 1:
        decision = 'recommend'
    elif len(eligible) > 1:
        decision = 'ambiguous'
    else:
        decision = 'none'
    explanation = {
        'wait': f'仍有方案尚不能判断，且还有 {looks_remaining} 次计划查看额度；请按计划继续查看。',
        'inconclusive': '仍有方案尚不能判断，但计划查看额度已用尽；当前证据不足以判断上线。',
        'recommend': f'仅 {eligible[0] if len(eligible) == 1 else ""} 同时满足全部预注册的收益与保护标准，建议上线。',
        'ambiguous': '多个方案同时满足预注册标准，计划未包含方案间比较，尚不能唯一确定上线对象。',
        'none': '所有方案都有未达门槛的收益或保护指标，本次资料不支持上线。',
    }[decision]

    immature = [
        {'arm': arm, 'metric': metric_id, 'status': summaries[(arm, metric_id)]['status']}
        for arm in [design['control'], *design['treatments']]
        for metric_id in [item['metric'] for item in design['metrics']]
        if summaries[(arm, metric_id)]['status'] != 'ready'
    ]
    notes = [
        '未成熟用户、成熟但尚无结果的用户与空值均不补零，不计入均值。',
        '效应范围按指标业务方向统一：收益类越大越好，保护类越小越好；区间为本次查看的单侧调整后区间。',
        '共用对照令各比较正相关，Bonferroni 口径偏保守，仍保证族错误率。',
        '未调整 p 值仅供参考，判定以调整后的阈值与区间为准。',
    ]
    return {
        'confirmatory': True,
        'cutoff': cutoff,
        'group': design['group'],
        'look_index': look_index,
        'looks_remaining': looks_remaining,
        'error_budget': {
            'alpha': alpha,
            'max_looks': design['max_looks'],
            'look_index': look_index,
            'information_fraction': spending['information_fraction'],
            'spending_method': "O'Brien–Fleming 单侧 α 消耗",
            'cumulative_alpha': spending['cumulative_alpha'],
            'incremental_alpha': spending['incremental_alpha'],
            'comparisons': comparisons_count,
            'alpha_per_comparison': alpha_per_comparison,
            'critical_value': critical,
            'schedule': table,
            'checks': {
                'sum_incremental_alpha': sum(item['incremental_alpha'] for item in table),
                'equals_alpha': abs(sum(item['incremental_alpha'] for item in table) - alpha) < 1e-12,
                'formula': 'α_inc(k)=S(k/K)-S((k-1)/K)，S(t)=1-Φ(Φ⁻¹(1-α)/√t)；每个比较阈值=α_inc(k)/M',
                'guarantee': '并集界：跨 K 次查看与 M 个比较的第一类错误 ≤ Σ α_inc(k) = α。',
            },
        },
        'decision': {
            'rule': design['decision_rule'],
            'decision': decision,
            'label': DECISION_LABELS[decision],
            'explanation': explanation,
            'recommended_arm': eligible[0] if decision == 'recommend' else None,
            'evidence_complete': not unresolved_arms,
            'fully_accrued': fully_accrued,
            'accrual_end': accrual_end,
            'looks_remaining': looks_remaining,
            'arms': arms,
        },
        'comparisons': comparisons,
        'summaries': [
            {'arm': arm, 'metric': metric['metric'], **summaries[(arm, metric['metric'])]}
            for arm in [design['control'], *design['treatments']]
            for metric in design['metrics']
        ],
        'immature': immature,
        'notes': notes,
        'trace': {
            'cutoff': cutoff,
            'group': design['group'],
            'digest': digest,
            'look_index': look_index,
            'design': design,
        },
    }
