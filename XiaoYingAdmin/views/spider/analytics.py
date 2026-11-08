"""
蜘蛛日志 — 数据分析（独立页面）

整合 统计概览 / 爬虫时段分布 / 访问者类型分布 / 访问统计 四大模块，
提供独立的筛选交互，不与日志列表耦合。
"""
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import render
from django.views.decorators.http import require_GET

from XiaoYingAdmin.models.spider_log import SpiderAccessLog
from XiaoYingAdmin.models.firewall import FirewallRule
from XiaoYingAdmin.views.spider.logs import (
    DAYS_OPTIONS, PERIOD_DEFS, WHO_LABELS,
    _parse_days_param, _parse_who, _apply_who,
    _build_base_query, _compute_stats, _compute_period_tree, _compute_who_counts,
    _build_filters,
)


TEMPLATE = 'XiaoYingAdmin/蜘蛛管理/蜘蛛日志/数据分析/index.html'


@login_required
@require_GET
def spider_analytics_view(request):
    """数据分析页面（/xiaoying_admin/spider/logs/analytics/）"""
    since = _parse_days_param(request)
    filters = _build_filters(request)
    current_who = _parse_who(request)
    hide_blocked = request.GET.get('hide_blocked') == '1'
    blocked_ips = set(
        FirewallRule.objects.filter(
            rule_type='ip_block', is_active=True
        ).values_list('value', flat=True)
    )

    # 基础 queryset
    base_qs = SpiderAccessLog.objects.filter(**filters)
    if since is not None:
        base_qs = base_qs.filter(create_time__gte=since)

    # 应用 who 过滤
    qs = _apply_who(base_qs, current_who)

    total = qs.count()

    # 统计指标（基于今天全量，不受筛选影响）
    stats = _compute_stats(SpiderAccessLog.objects.all())

    # 爬虫时段分布
    period_stats = _compute_period_tree(qs)

    # 访问者类型计数
    who_counts = _compute_who_counts(base_qs)

    # TOP 页面 & TOP IP
    total_for_pct = max(total, 1)
    top_pages_raw = base_qs.values('path').annotate(count=Count('id')).order_by('-count')[:8]
    top_pages = [
        {'path': r['path'], 'count': r['count'],
         'percent': round(r['count'] / total_for_pct * 100)}
        for r in top_pages_raw
    ]
    top_ips_raw = base_qs.values('ip').annotate(
        count=Count('id'),
        spider_count=Count('id', filter=Q(spider_name__gt='')),
    ).order_by('-count')[:8]
    top_ips = [
        {'ip': r['ip'], 'count': r['count'],
         'percent': round(r['count'] / total_for_pct * 100),
         'is_spider': r['spider_count'] > 0}
        for r in top_ips_raw
    ]

    # 过滤链接
    base_query_no_who = _build_base_query(request, override={'who': ''}, drop=('page',))
    base_query_with_who_spider = _build_base_query(request, override={'who': 'spider'}, drop=('page',))
    base_query_with_who_human = _build_base_query(request, override={'who': 'human'}, drop=('page',))
    base_query_with_who_direct = _build_base_query(request, override={'who': 'direct'}, drop=('page',))

    return render(request, TEMPLATE, {
        'stats': stats,
        'period_stats': period_stats,
        'counts': who_counts,
        'who_label': WHO_LABELS[current_who],
        'current_who': current_who,
        'total': total,
        'top_pages': top_pages,
        'top_ips': top_ips,
        'base_query_no_who': base_query_no_who,
        'base_query_with_who_spider': base_query_with_who_spider,
        'base_query_with_who_human': base_query_with_who_human,
        'base_query_with_who_direct': base_query_with_who_direct,
        'days': request.GET.get('days', '7d'),
        'current_spider': request.GET.get('spider', ''),
        'current_ip': request.GET.get('ip', ''),
        'current_method': request.GET.get('method', ''),
        'all_spider_names': list(
            SpiderAccessLog.objects.exclude(spider_name='')
            .values_list('spider_name', flat=True).distinct().order_by('spider_name')
        ),
        'blocked_ips': blocked_ips,
        'hide_blocked': hide_blocked,
    })
