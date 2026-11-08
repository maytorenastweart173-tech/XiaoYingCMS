from django.db import models
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils import timezone
from django.conf import settings as django_settings
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
import json
import re

from XiaoYingAdmin.common.http import parse_json_body, err, get_or_404
from XiaoYingAdmin.middleware.operation_log import log_operation
from XiaoYingAdmin.views.page_generator import (
    get_task_progress,
    start_generation,
    start_seo_optimization,
)

from XiaoYingAdmin.models.prompt import Prompt
from XiaoYingAdmin.models.generated_page import GeneratedPage
from XiaoYingAdmin.models.site_settings import SiteSettings
from XiaoYingAdmin.models.user_config import UserConfig
from XiaoYingAdmin.models.user import User
from XiaoYingAdmin.models.spider_log import SpiderAccessLog
from XiaoYingAdmin.models.operation_log import OperationLog
from XiaoYingAdmin.models.login_log import LoginLog
from XiaoYingAdmin.models.task import PageGenerationTask
from XiaoYingAdmin.models.firewall import FirewallRule
from XiaoYingAdmin.models.multi_page_project import MultiPageProject
from XiaoYingAdmin.models.seo_domain import SeoDomain
from XiaoYingAdmin.models.page_category import PageCategory


# =============================================================================
# 工具函数
# =============================================================================

def _filter_pages_for_user(request, qs):
    """
    根据当前用户过滤页面查询集：
      - 超级管理员 → 查看全部
      - 普通用户 → 只看自己的
      - 未登录 → 空结果
    """
    if request.user.is_superuser:
        return qs
    if request.user.is_authenticated:
        return qs.filter(created_by=request.user)
    return qs.none()


def _check_page_owner(request, page):
    """检查当前用户是否有权限操作该页面。返回 error JsonResponse 或 None。"""
    if request.user.is_superuser:
        return None
    if page.created_by_id != request.user.id:
        from XiaoYingAdmin.common.http import err
        return err('无权操作此页面', status=403)
    return None


# =============================================================================
# 页面视图（模板渲染）
# =============================================================================

# TemplateView: 模板视图（用于直接预览母版）
def template_view(request):
    return render(request, 'XiaoYingAdmin/template.html')


# IndexView: 首页视图
def index_view(request):
    """首页仪表盘 — 展示项目概览统计"""
    today = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # 登录统计
    login_success_today = LoginLog.objects.filter(
        login_time__gte=today, status='success'
    ).count()
    login_failed_today = LoginLog.objects.filter(
        login_time__gte=today
    ).exclude(status='success').count()
    login_total = LoginLog.objects.count()

    # 最近操作
    recent_ops = list(
        OperationLog.objects.select_related('user')
        .defer('detail', 'user_agent')
        .order_by('-created_at')[:8]
    )

    # 今日活跃蜘蛛 TOP 5
    active_spiders_today = list(
        SpiderAccessLog.objects.filter(
            create_time__gte=today
        ).exclude(spider_name='')
        .values('spider_name')
        .annotate(count=models.Count('id'))
        .order_by('-count')[:5]
    )

    # 防火墙统计
    firewall_active = FirewallRule.objects.filter(is_active=True).count()
    firewall_total = FirewallRule.objects.count()

    # 多页面统计
    multi_projects_total = MultiPageProject.objects.count()
    multi_projects_completed = MultiPageProject.objects.filter(status='completed').count()
    multi_projects_enabled = MultiPageProject.objects.filter(is_enabled=True).count()

    # SEO 域名统计
    seo_domains_total = SeoDomain.objects.count()

    # 数据库引擎 — 从 settings.py 动态读取
    raw_engine = django_settings.DATABASES['default']['ENGINE']
    # django.db.backends.sqlite3 → SQLite, django.db.backends.mysql → MySQL
    db_engine = raw_engine.rsplit('.', 1)[-1].capitalize()

    context = {
        # 用户统计
        'total_users': User.objects.count(),
        'active_users': User.objects.filter(is_active=True).count(),
        'staff_users': User.objects.filter(is_staff=True).count(),
        # 内容统计
        'total_pages': GeneratedPage.objects.count(),
        'total_prompts': Prompt.objects.count(),
        'active_prompts': Prompt.objects.filter(is_active=True).count(),
        # 多页面统计
        'multi_projects_total': multi_projects_total,
        'multi_projects_completed': multi_projects_completed,
        'multi_projects_enabled': multi_projects_enabled,
        # SEO 统计
        'seo_domains_total': seo_domains_total,
        # 蜘蛛统计
        'spider_total': SpiderAccessLog.objects.count(),
        'spider_today': SpiderAccessLog.objects.filter(create_time__gte=today).count(),
        # 日志统计
        'op_log_today': OperationLog.objects.filter(created_at__gte=today).count(),
        'login_log_today': LoginLog.objects.filter(login_time__gte=today).count(),
        'total_tasks': PageGenerationTask.objects.count(),
        'failed_tasks': PageGenerationTask.objects.filter(status='failed').count(),
        # 登录统计
        'login_success_today': login_success_today,
        'login_failed_today': login_failed_today,
        'login_total': login_total,
        'login_success_rate': round(login_success_today / max(login_success_today + login_failed_today, 1) * 100),
        # 最近动态 & 蜘蛛活跃
        'recent_ops': recent_ops,
        'active_spiders_today': active_spiders_today,
        # 防火墙
        'firewall_active': firewall_active,
        'firewall_total': firewall_total,
        # 数据库引擎
        'db_engine': db_engine,
    }
    return render(request, 'XiaoYingAdmin/index.html', context)


# CoreSettingsView: 网站设置视图
def site_settings_view(request):
    """
    网站设置页：单例 SiteSettings + UserConfig 读写。

    GET:  渲染表单（带当前值）
    POST: 保存 statistics_code / is_active / user_config 配置，然后 PRG 重定向回 GET
    """
    settings, _ = SiteSettings.objects.get_or_create(pk=1)
    user_config = UserConfig.get_singleton()

    if request.method == 'POST':
        update_fields = []

        # 判断提交的是哪个表单：统计代码表单始终包含 statistics_code 字段
        if 'statistics_code' in request.POST:
            settings.statistics_code = request.POST.get('statistics_code', '') or ''
            # layui switch 关闭时不提交值，需显式处理：不存在即 False
            settings.is_active = request.POST.get('is_active') == 'on'
            update_fields.extend(['statistics_code', 'is_active'])

        # 判断提交的是备份配置表单：始终包含 auto_backup_spider_threshold 字段
        if 'auto_backup_spider_threshold' in request.POST:
            try:
                spider_th = int(request.POST.get('auto_backup_spider_threshold', 0))
            except (TypeError, ValueError):
                spider_th = 0
            settings.auto_backup_spider_threshold = max(0, spider_th)
            update_fields.append('auto_backup_spider_threshold')

            try:
                oper_th = int(request.POST.get('auto_backup_operation_threshold', 0))
            except (TypeError, ValueError):
                oper_th = 0
            settings.auto_backup_operation_threshold = max(0, oper_th)
            update_fields.append('auto_backup_operation_threshold')

        if update_fields:
            update_fields.append('updated_time')
            settings.save(update_fields=update_fields)

        # 判断提交的是 DeepSeek 配置表单
        if 'deepseek_api_key' in request.POST or 'deepseek_thinking_enabled' in request.POST:
            settings.deepseek_api_key = (request.POST.get('deepseek_api_key', '') or '').strip()
            settings.deepseek_thinking_enabled = request.POST.get('deepseek_thinking_enabled') == 'on'
            settings.save(update_fields=['deepseek_api_key', 'deepseek_thinking_enabled', 'updated_time'])

        # 判断提交的是登录安全配置
        if 'max_login_attempts' in request.POST:
            try:
                max_attempts = int(request.POST.get('max_login_attempts', 0))
            except (TypeError, ValueError):
                max_attempts = 0
            settings.max_login_attempts = max(0, max_attempts)
            settings.login_ip_whitelist = (request.POST.get('login_ip_whitelist', '') or '').strip()
            settings.allowed_admin_domains = (request.POST.get('allowed_admin_domains', '') or '').strip()
            settings.save(update_fields=['max_login_attempts', 'login_ip_whitelist', 'allowed_admin_domains', 'updated_time'])

        # 判断提交的是用户系统配置表单
        if 'registration_enabled' in request.POST:
            user_config.registration_enabled = request.POST.get('registration_enabled') == 'on'
            user_config.save(update_fields=['registration_enabled', 'updated_time'])

        return redirect(reverse('site_settings') + '?saved=1')

    return render(request, 'XiaoYingAdmin/核心设置/网站设置.html', {
        'site_settings': settings,
        'user_config': user_config,
        'saved': request.GET.get('saved') == '1',
        'spider_total': SpiderAccessLog.objects.count(),
        'backup_dir': getattr(django_settings, 'BACKUP_DIR', 'backups'),
    })


@csrf_exempt
@require_POST
def api_site_settings_save_search_engines(request):
    """保存搜索引擎列表（AJAX API）"""
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'code': 1, 'message': 'JSON 解析失败'}, status=400)

    engines = body.get('search_engines', [])
    if not isinstance(engines, list):
        return JsonResponse({'code': 1, 'message': 'search_engines 必须为数组'}, status=400)

    settings, _ = SiteSettings.objects.get_or_create(pk=1)
    settings.search_engines = engines
    settings.save(update_fields=['search_engines', 'updated_time'])

    return JsonResponse({'code': 0, 'message': '保存成功', 'data': engines})


@require_GET
def api_site_settings_get_search_engines(request):
    """获取搜索引擎列表（AJAX API）"""
    settings, _ = SiteSettings.objects.get_or_create(pk=1)
    return JsonResponse({'code': 0, 'data': settings.search_engines or []})


# PageGenerateView: 页面生成视图
def page_generate_view(request):
    """页面生成页 — 若 session 中存在活跃任务，自动恢复进度显示。"""
    task_id = request.session.get('page_gen_task_id')
    active_task = None
    if task_id:
        active_task = get_task_progress(task_id)
        if active_task and active_task['status'] in ('completed', 'failed'):
            # 已结束的任务不保留在 session
            request.session.pop('page_gen_task_id', None)

    # 构造初始域名文本（批量文本框，一行一个）
    initial_domain_text = ''
    if active_task:
        config = active_task.get('domain_prompt_config', [])
        if config:
            initial_domain_text = '\n'.join(
                item.get('domain', '') for item in config if item.get('domain')
            )
    return render(request, 'XiaoYingAdmin/页面管理/页面生成.html', {
        'active_task': active_task,
        'active_task_domain_config_json': (
            json.dumps(active_task.get('domain_prompt_config', []))
            if active_task else '[]'
        ),
        'initial_domain_text': initial_domain_text,
    })


# PageListView: 页面列表视图
def page_list_view(request):
    return render(request, 'XiaoYingAdmin/页面管理/页面列表.html')


# =============================================================================
# AJAX API: 页面生成
# =============================================================================

@csrf_exempt
@require_POST
def api_start_generate(request):
    """
    启动一次 AI 页面生成。

    请求: POST application/json
      {"content": "页面描述...", "domain": "example.com"}  旧版，单域名
      {"content": "页面描述...", "domains": [{"domain": "app.example.com", "prompt_id": null}, ...]}
        新版：多域名，每个域名可选提示词 ID（null=默认提示词）

    响应: application/json
      {"task_id": "...", "status": "pending", "progress": 0, "message": "..."}
    """
    body, error = parse_json_body(request)
    if error is not None:
        return error

    content = (body.get('content') or '').strip()
    if not content:
        return err('请输入页面描述内容')

    if not request.session.session_key:
        request.session.save()

    # ---- 解析域名配置 ----
    # 优先级：domains（数组） > domain（兼容旧版单域名）
    domain = ''
    domain_prompt_config = []

    if 'domains' in body:
        raw_domains = body['domains']
        if isinstance(raw_domains, list):
            seen = set()
            for item in raw_domains:
                if not isinstance(item, dict):
                    continue
                d = (item.get('domain') or '').strip().lower()
                for proto in ('https://', 'http://'):
                    if d.startswith(proto):
                        d = d[len(proto):]
                        break
                if d and d not in seen:
                    seen.add(d)
                    pid = item.get('prompt_id')
                    pids = item.get('prompt_ids')
                    entry = {'domain': d}
                    if pids:
                        entry['prompt_ids'] = [int(x) for x in pids if x is not None]
                    elif pid is not None:
                        entry['prompt_id'] = int(pid)
                    else:
                        entry['prompt_ids'] = []
                    domain_prompt_config.append(entry)
            if domain_prompt_config:
                domain = domain_prompt_config[0]['domain']
    elif 'domain' in body:
        # 兼容旧版：单域名
        domain = (body.get('domain') or '').strip().lower()
        for proto in ('https://', 'http://'):
            if domain.startswith(proto):
                domain = domain[len(proto):]
                break

    task = start_generation(
        input_content=content,
        session_key=request.session.session_key,
        created_by=request.user if request.user.is_authenticated else None,
        domain=domain,
        domain_prompt_config=domain_prompt_config if domain_prompt_config else None,
    )

    # 写入 session：刷新页面后可恢复进度
    request.session['page_gen_task_id'] = str(task.task_id)

    domain_log = domain or '(无)'
    if domain_prompt_config:
        domain_log = f'{len(domain_prompt_config)} 个域名'
    log_operation(request, 'create', 'PageGeneration', str(task.task_id),
                  f'页面生成「{content[:50]}」',
                  detail={'changes': {'需求描述': {'new': content[:100]},
                                       '绑定域名': {'new': domain_log}}})

    return JsonResponse({
        'task_id': str(task.task_id),
        'status': task.status,
        'status_display': task.get_status_display(),
        'progress': task.progress,
        'message': task.message,
    })


@csrf_exempt
@require_POST
def api_abort_generate(request, task_id):
    """
    中断一次页面生成任务。

    请求: POST /api/generate/abort/<task_id>/

    响应: application/json
      {"message": "已中断"}
    """
    try:
        task = PageGenerationTask.objects.get(task_id=task_id)
    except (PageGenerationTask.DoesNotExist, ValueError):
        return err('任务不存在', status=404)

    # 只能中断进行中的任务
    if task.status in (PageGenerationTask.STATUS_COMPLETED, PageGenerationTask.STATUS_FAILED):
        return err('任务已结束，无法中断')

    # 标记为失败，后台线程在下一个检查点会退出
    task.mark_failed('用户已中断生成')

    # 清理 session 中的任务记录
    if request.session.get('page_gen_task_id') == str(task.task_id):
        request.session.pop('page_gen_task_id', None)

    log_operation(request, 'update', 'PageGeneration', str(task.task_id),
                  f'页面生成任务已中断「{task.input_content[:50]}」',
                  detail={'changes': {'状态': {'old': task.status, 'new': '中断'}}})

    return JsonResponse({'message': '已中断'})


@require_GET
def api_get_progress(request, task_id):
    """
    查询指定任务的进度（前端轮询用）。

    响应: application/json
      {"task_id":"...","status":"running","progress":50,"message":"..."}
    """
    progress = get_task_progress(task_id)
    if progress is None:
        return err('任务不存在', status=404)
    return JsonResponse(progress)


# =============================================================================
# AJAX API: 提示词管理
# =============================================================================

@require_GET
def api_prompt_list(request):
    """
    获取所有提示词列表。

    查询参数:
      category: str（可选），按分类筛选

    响应: application/json
      [{"id":1, "category":"...", "name":"...", "version":1, "is_active":true, "content":"...", ...}]
    """
    qs = Prompt.objects.all()
    category = request.GET.get('category')
    if category:
        qs = qs.filter(category=category)
    return JsonResponse([p.to_dict() for p in qs], safe=False)


@require_GET
def api_prompt_detail(request, prompt_id):
    """获取单个提示词详情。"""
    prompt, error = get_or_404(Prompt, id=prompt_id)
    if error is not None:
        return error
    return JsonResponse(prompt.to_dict())


@csrf_exempt
@require_POST
def api_prompt_save(request):
    """
    创建新的提示词版本。

    请求: application/json
      {"category":"page_generation", "name":"通用页面生成 v3", "content":"...", "description":"调整了..."}
    """
    body, error = parse_json_body(request)
    if error is not None:
        return error

    category = (body.get('category') or 'page_generation').strip()
    name = (body.get('name') or '').strip()
    content = (body.get('content') or '').strip()
    description = (body.get('description') or '').strip()

    if not name:
        return err('请输入提示词名称')
    if not content:
        return err('请输入提示词内容')

    prompt = Prompt.objects.create(
        category=category,
        name=name,
        content=content,
        description=description,
    )

    log_operation(request, 'create', 'Prompt', prompt.id,
                  f'提示词「{name}」v{prompt.version}',
                  detail={'changes': {'分类': {'new': category}, '名称': {'new': name}}})

    return JsonResponse({
        'id': prompt.id,
        'version': prompt.version,
        'message': f'提示词 v{prompt.version} 创建成功',
    })


@csrf_exempt
@require_POST
def api_prompt_activate(request):
    """
    切换指定提示词的启用状态。

    请求: application/json
      {"id": 1, "is_active": false}

    注意：允许多条提示词同时启用，生成时会合并到 system_prompt 中。
    """
    body, error = parse_json_body(request)
    if error is not None:
        return error

    prompt, error = get_or_404(Prompt, id=body.get('id'))
    if error is not None:
        return error

    prompt.is_active = body.get('is_active', True)
    prompt.save(update_fields=['is_active', 'updated_time'])

    status_text = '启用' if prompt.is_active else '禁用'
    log_operation(request, 'update', 'Prompt', prompt.id,
                  f'提示词「{prompt.name}」→ {status_text}',
                  detail={'changes': {'启用状态': {'new': status_text}}})

    return JsonResponse({'message': '已更新', 'is_active': prompt.is_active})


@csrf_exempt
@require_POST
def api_prompt_delete(request):
    """
    删除提示词。

    请求: application/json
      {"id": 1}
    """
    body, error = parse_json_body(request)
    if error is not None:
        return error

    prompt, error = get_or_404(Prompt, id=body.get('id'))
    if error is not None:
        return error

    prompt_name = prompt.name
    prompt.delete()
    log_operation(request, 'delete', 'Prompt', body.get('id'),
                  f'提示词「{prompt_name}」',
                  detail={'changes': {'已删除提示词': {'new': prompt_name}}})
    return JsonResponse({'message': '已删除'})


# =============================================================================
# AJAX API: 已保存页面
# =============================================================================

@require_GET
def api_saved_pages(request):
    """
    获取已保存的生成页面列表（支持分页 + 搜索）。

    查询参数:
      page: int（默认 1）
      limit: int（默认 15）
      keyword: str（可选，按 name 或 input_content 模糊匹配）

    响应: application/json
      {"total": 100, "page": 1, "items": [{"id":1, "name":"...", ...}]}
    """
    page = max(int(request.GET.get('page', 1) or 1), 1)
    limit = max(int(request.GET.get('limit', 15) or 15), 1)
    keyword = (request.GET.get('keyword') or '').strip()

    qs = GeneratedPage.objects.all()
    if keyword:
        qs = qs.filter(Q(name__icontains=keyword) | Q(input_content__icontains=keyword))
    qs = _filter_pages_for_user(request, qs)

    total = qs.count()
    offset = (page - 1) * limit
    items = qs.order_by('-create_time')[offset:offset + limit]

    return JsonResponse({
        'total': total,
        'page': page,
        'items': [p.to_dict() for p in items],
    }, safe=False)


@csrf_exempt
@require_POST
def api_saved_page_create(request):
    """
    手动创建已保存页面（用于从旧项目迁移页面）。

    请求: application/json
      {
        "name": "页面名称",          // 必填
        "html_content": "HTML 代码",  // 必填
        "input_content": "需求描述",  // 可选
        "domains": ["example.com"],  // 可选，域名列表
        "category_ids": [1, 3],       // 可选，分类 ID 列表
      }
    """
    body, error = parse_json_body(request)
    if error is not None:
        return error

    name = (body.get('name') or '').strip()
    html_content = (body.get('html_content') or '').strip()

    if not name:
        return err('页面名称不能为空')
    if not html_content:
        return err('HTML 内容不能为空')

    import uuid

    domains = body.get('domains')
    if domains is not None and not isinstance(domains, list):
        return err('domains 必须为数组')

    page = GeneratedPage(
        name=name,
        html_content=html_content,
        input_content=(body.get('input_content') or '').strip(),
        domains=domains or [],
        task_id=uuid.uuid4(),  # 手动创建的页面用随机 UUID
        created_by=request.user if request.user.is_authenticated else None,
    )
    page.save()

    # 设置分类
    category_ids = body.get('category_ids')
    if category_ids and isinstance(category_ids, list):
        from XiaoYingAdmin.models.page_category import PageCategory
        cats = PageCategory.objects.filter(id__in=[c for c in category_ids if c])
        if cats.exists():
            page.categories.set(cats)

    # 自动注入配置了「全部页面 / 所属分类」的自定义友情链接
    from XiaoYingAdmin.views.friend_link import sync_friend_links_to_pages
    sync_friend_links_to_pages(pages=[page])

    log_operation(request, 'create', 'GeneratedPage', page.id,
                  f'手动创建页面「{page.name}」',
                  detail={'changes': {'页面名称': {'new': page.name}}})

    return JsonResponse({'message': '页面创建成功', 'page': page.to_dict(with_html=True)})


def api_saved_page_detail(request, page_id):
    """获取已保存页面的详情（含 HTML 内容）。"""
    page, error = get_or_404(GeneratedPage, id=page_id, not_found_msg='页面不存在')
    if error is not None:
        return error
    perm_err = _check_page_owner(request, page)
    if perm_err:
        return perm_err
    return JsonResponse(page.to_dict(with_html=True))


@csrf_exempt
@require_POST
def api_saved_page_delete(request):
    """
    删除已保存页面。

    请求: application/json
      {"id": 1}
    """
    body, error = parse_json_body(request)
    if error is not None:
        return error

    page, error = get_or_404(GeneratedPage, id=body.get('id'), not_found_msg='页面不存在')
    if error is not None:
        return error
    perm_err = _check_page_owner(request, page)
    if perm_err:
        return perm_err

    page_name = page.name
    page.delete()
    log_operation(request, 'delete', 'GeneratedPage', body.get('id'),
                  f'生成页面「{page_name}」',
                  detail={'changes': {'已删除页面': {'new': page_name}}})
    return JsonResponse({'message': '已删除'})


@csrf_exempt
@require_POST
def api_saved_page_delete_by_root(request):
    """
    删除指定根域名下的所有已保存页面。

    查找范围：page.domain（旧字段） 和 page.domains（JSON 数组，支持 *. 通配符）。
    匹配逻辑：域名字段的值 == root，或以 .root 结尾（即 root 的子域名）。

    请求: application/json
      {"root": "example.com"}
    """
    body, error = parse_json_body(request)
    if error is not None:
        return error

    root = (body.get('root') or '').strip().lower()
    if not root:
        return err('请提供根域名')

    from XiaoYingAdmin.models.generated_page import GeneratedPage

    # 遍历所有页面，匹配 domains JSON 数组和旧 domain 字段
    root_dot = '.' + root
    matched_ids = []
    for page in GeneratedPage.objects.iterator():
        # 检查旧 domain 字段
        if page.domain and page.domain.lower() == root:
            matched_ids.append(page.id)
            continue
        # 检查 domains JSON 数组
        if page.domains:
            for d in page.domains:
                d_lower = d.lower()
                # 去掉 *. 前缀再比较
                clean_d = d_lower.lstrip('*.')
                if clean_d == root or clean_d.endswith(root_dot):
                    matched_ids.append(page.id)
                    break

    if not matched_ids:
        return JsonResponse({'message': f'根域名 "{root}" 下没有页面', 'deleted': 0})

    pages = GeneratedPage.objects.filter(id__in=matched_ids)
    count = pages.count()
    page_names = list(pages.values_list('name', flat=True))
    pages.delete()

    log_operation(request, 'delete', 'GeneratedPage', None,
                  f'批量删除根域名「{root}」下的 {count} 个页面: {", ".join(page_names)}',
                  detail={'changes': {'批量删除根域名': {'new': f'{root} ({count}个页面)'}}})
    return JsonResponse({'message': f'已删除根域名 "{root}" 下的 {count} 个页面', 'deleted': count})


@csrf_exempt
@require_POST
def api_saved_page_update(request):
    """
    更新已保存页面的名称、需求描述或 HTML 内容。

    只发送需要修改的字段即可，未传的字段保持不变。

    请求: application/json
      {"id": 1, "name": "新名称"}
      {"id": 1, "name": "新名称", "html_content": "新 HTML"}
      {"id": 1, "input_content": "新需求描述"}

    响应: application/json
      {"message": "已更新", "page": {id, name, ...}}
    """
    body, error = parse_json_body(request)
    if error is not None:
        return error

    page, error = get_or_404(GeneratedPage, id=body.get('id'), not_found_msg='页面不存在')
    if error is not None:
        return error
    perm_err = _check_page_owner(request, page)
    if perm_err:
        return perm_err

    old_name = page.name
    changed_fields = []
    for field in ('name', 'input_content', 'html_content'):
        if field in body:
            val = (body[field] or '').strip()
            if getattr(page, field) != val:
                changed_fields.append(field)
                setattr(page, field, val)

    if not changed_fields:
        return JsonResponse({'message': '未检测到修改', 'page': page.to_dict(with_html=True)})

    page.save(update_fields=['name', 'input_content', 'html_content', 'updated_time'])

    # 如果页面名称变更，同步更新其他页面中的互链名称
    if 'name' in changed_fields:
        _update_crosslink_names(page, old_name)

    # HTML 内容更新后，自动重新注入配置了「全部页面 / 所属分类」的自定义友情链接
    if 'html_content' in changed_fields:
        from XiaoYingAdmin.views.friend_link import sync_friend_links_to_pages
        sync_friend_links_to_pages(pages=[page])

    # 构建变更详情
    field_labels = {'name': '页面名称', 'input_content': '需求描述', 'html_content': 'HTML内容'}
    changes = {}
    for f in changed_fields:
        label = field_labels.get(f, f)
        val = (body[f] or '').strip()
        changes[label] = {'new': val[:100] + ('…' if len(val) > 100 else ''), 'old': None}
    log_operation(request, 'update', 'GeneratedPage', body.get('id'),
                  f'生成页面「{page.name}」',
                  detail={'changes': changes})

    return JsonResponse({'message': '已更新', 'page': page.to_dict(with_html=True)})


@csrf_exempt
@require_POST
def api_seo_optimize_page(request):
    """
    对已保存页面启动 SEO 优化（异步，立即返回 task_id，前端轮询进度）。

    请求: application/json
      {"page_id": 1}

    响应: application/json
      {"task_id": "...", "status": "pending", "progress": 0, "message": "..."}
    """
    body, error = parse_json_body(request)
    if error is not None:
        return error

    page_id = body.get('page_id')
    if not page_id:
        return err('缺少 page_id')

    page, error = get_or_404(GeneratedPage, id=page_id, not_found_msg='页面不存在')
    if error is not None:
        return error
    perm_err = _check_page_owner(request, page)
    if perm_err:
        return perm_err

    task = start_seo_optimization(
        page_id=page.id,
        created_by=request.user if request.user.is_authenticated else None,
    )

    log_operation(request, 'update', 'GeneratedPage', page.id,
                  f'生成页面「{page.name}」→ 启动 SEO 优化',
                  detail={'changes': {'SEO优化': {'new': '启动中'}}})

    return JsonResponse({
        'task_id': str(task.task_id),
        'status': task.status,
        'status_display': task.get_status_display(),
        'progress': task.progress,
        'message': task.message,
    })


# =============================================================================
# AJAX API: 域名管理（绑定/解绑）
# =============================================================================

@csrf_exempt
@require_POST
def api_saved_page_set_domain(request):
    """
    设置或清除页面的绑定域名（支持多个域名及 *. 通配符）。

    请求: application/json
      {"id": 1, "domains": ["example.com", "*.example.com"]}  设置多个域名
      {"id": 1, "domains": []}                                 清除所有域名
      {"id": 1, "domain": "example.com"}                       兼容旧版（单域名）

    响应: application/json
      {"message": "...", "page": {id, name, domains, ...}}
    """
    body, error = parse_json_body(request)
    if error is not None:
        return error

    page, error = get_or_404(GeneratedPage, id=body.get('id'), not_found_msg='页面不存在')
    if error is not None:
        return error
    perm_err = _check_page_owner(request, page)
    if perm_err:
        return perm_err

    # ------- 解析域名列表 -------
    if 'domains' in body:
        new_domains = body['domains']
        if not isinstance(new_domains, list):
            return err('domains 必须是一个数组')
        # 清洗：去空、去重、保留顺序
        seen = set()
        cleaned = []
        for d in new_domains:
            d = (d or '').strip().lower()
            if d and d not in seen:
                seen.add(d)
                # 校验格式：普通域名 或 *.domain 通配符
                if d.startswith('*.'):
                    if len(d) <= 2 or '.' not in d[2:]:
                        return err(f'通配符域名格式无效: {d}')
                else:
                    if '.' not in d or d.count('.') < 1:
                        return err(f'域名格式无效: {d}')
                cleaned.append(d)
        new_domains = cleaned
    elif 'domain' in body:
        # 兼容旧版：单域名
        d = (body['domain'] or '').strip().lower()
        new_domains = [d] if d else []
    else:
        return err('请提供 domains 或 domain 参数')

    # ------- 冲突检查：任一域名已被其他页面占用 -------
    old_domains = set(page.domains or [])
    new_set = set(new_domains)
    force = bool(body.get('force'))

    # 新增的域名（不在旧列表中）需要检查冲突
    added = new_set - old_domains
    if added and not force:
        # 收集每个冲突域名对应的占用页面
        conflict_map = {}  # {domain: {id, name}}
        other_pages = list(GeneratedPage.objects.exclude(id=page.id).exclude(domains=[]))
        for p in other_pages:
            for d in (p.domains or []):
                if d in added:
                    conflict_map[d] = {'id': p.id, 'name': p.name}
        # 兼容旧版 domain 字段
        for p in GeneratedPage.objects.exclude(id=page.id).exclude(domain__isnull=True).exclude(domain=''):
            d = (p.domain or '').lower()
            if d in added and d not in conflict_map:
                conflict_map[d] = {'id': p.id, 'name': p.name}

        if conflict_map:
            # 返回冲突详情，让前端询问用户是否强制解绑
            details = [
                f"{d}（占用页面：{info['name']}）"
                for d, info in conflict_map.items()
            ]
            return JsonResponse({
                'error': '域名已被占用',
                'conflict': True,
                'conflicts': conflict_map,
                'detail': '；'.join(details),
            }, status=409)

    # force=true 时，先解绑被占用的域名（从占用页面移除这些域名）
    if added and force:
        added_lower = {d.lower() for d in added}
        other_pages = list(GeneratedPage.objects.exclude(id=page.id).exclude(domains=[]))
        for p in other_pages:
            old_p_domains = list(p.domains or [])
            new_p_domains = [d for d in old_p_domains if d.lower() not in added_lower]
            if len(new_p_domains) != len(old_p_domains):
                p.domains = new_p_domains
                p.domain = new_p_domains[0] if new_p_domains else None
                p.save(update_fields=['domains', 'domain', 'updated_time'])
                log_operation(request, 'update', 'GeneratedPage', p.id,
                              f'生成页面「{p.name}」域名被强制解绑（转移至「{page.name}」）',
                              detail={'changes': {'绑定域名': {'old': old_p_domains, 'new': new_p_domains}}})
        # 兼容旧版 domain 字段
        for p in GeneratedPage.objects.exclude(id=page.id).exclude(domain__isnull=True).exclude(domain=''):
            d = (p.domain or '').lower()
            if d in added_lower:
                old_single = p.domain
                p.domain = None
                p.save(update_fields=['domain', 'updated_time'])
                log_operation(request, 'update', 'GeneratedPage', p.id,
                              f'生成页面「{p.name}」旧版域名字段被强制解绑（转移至「{page.name}」）',
                              detail={'changes': {'domain': {'old': old_single, 'new': None}}})

    # ------- 保存 -------
    old_domains_list = page.domains or []
    page.domains = new_domains
    # 保持旧 domain 字段同步（第一个域名）
    page.domain = new_domains[0] if new_domains else None
    save_fields = ['domains', 'domain', 'updated_time']
    # 如果 domain 原来有值但被清空，要把 null 写入
    page.save(update_fields=save_fields)

    # ------- 日志 -------
    if not new_domains:
        log_operation(request, 'update', 'GeneratedPage', body.get('id'),
                      f'生成页面「{page.name}」清除所有域名',
                      detail={'changes': {'绑定域名': {'old': old_domains_list, 'new': []}}})
    else:
        added_str = ', '.join(sorted(new_set - old_domains))
        removed_str = ', '.join(sorted(old_domains - new_set))
        parts = []
        if added_str:
            parts.append(f'+{added_str}')
        if removed_str:
            parts.append(f'-{removed_str}')
        change_desc = ' '.join(parts) if parts else ', '.join(new_domains)
        log_operation(request, 'update', 'GeneratedPage', body.get('id'),
                      f'生成页面「{page.name}」域名变更: {change_desc}',
                      detail={'changes': {'绑定域名': {'old': old_domains_list, 'new': new_domains}}})

    return JsonResponse({
        'message': f'已设置 {len(new_domains)} 个域名',
        'page': page.to_dict(),
    })


# =============================================================================
# 智能互链
# =============================================================================

# 互链标签常量（前后端保持一致）
_CROSSLINK_TAG_START = '<!-- ====== 智能互链 ====== -->'
_CROSSLINK_TAG_END = '<!-- ====== /智能互链 ====== -->'


def _extract_root_domain(domain: str) -> str:
    """
    规范化域名，统一为 ``https://域名/`` 格式，用于互链匹配。

    只做最小化处理：
      - 移除 *. 通配符前缀
      - 统一小写
      - 移除 www. 前缀（www.example.com → example.com）
      - **不会截断域名层次结构**（app-xiaoying.hl.cn 保持为 app-xiaoying.hl.cn）
      - 保留端口号（127.0.0.1:8000 保持原样）
    """
    d = domain.strip().lower()
    # 移除 *. 前缀（通配符匹配）
    d = d.lstrip('*')
    d = d.lstrip('.')
    # 移除 www. 前缀
    if d.startswith('www.'):
        d = d[4:]
    return f'https://{d}/'


def _to_attr(s: str) -> str:
    """HTML 属性转义。"""
    return str(s).replace('&', '&amp;').replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')


def _crosslink_items_html(partner_links: list) -> str:
    """生成 <a> 标签行（不含外层包装），供新增/追加共用。"""
    if not partner_links:
        return ''
    return '\n'.join(
        f'      <a href="{_to_attr(p["url"])}" '
        f'title="{_to_attr(p["title"])}" '
        f'rel="friend" target="_blank">{_to_attr(p["title"])}</a>'
        for p in partner_links
    )


def _crosslink_html_block(partner_links: list, extra_items=None) -> str:
    """
    生成完整的互链 HTML 块（含 header + wrapper）。
    partner_links: [{"url": "https://xxx.com/", "title": "页面名称"}, ...]
    extra_items: 需一并保留进块中的已有 <a> 标签字符串列表
                 （如自定义友情链接 data-fl），可为 None
    """
    items_html = _crosslink_items_html(partner_links)
    if extra_items:
        items_html = (items_html + '\n' + '\n'.join(extra_items)) if items_html else '\n'.join(extra_items)
    if not items_html:
        return ''
    return (
        f'\n{_CROSSLINK_TAG_START}\n'
        '<div style="'
        '  max-width:1200px; margin:40px auto 0; padding:24px 20px 16px;'
        '  border-top:1px solid #e8e8e8; text-align:center;'
        '">\n'
        '  <div style="'
        '    font-size:13px; color:#999; margin-bottom:12px;'
        '    letter-spacing:1px;'
        '  ">— 友情链接 —</div>\n'
        '  <div style="display:flex; flex-wrap:wrap; justify-content:center; gap:8px;">\n'
        f'{items_html}\n'
        '  </div>\n'
        '</div>\n'
        f'{_CROSSLINK_TAG_END}\n'
    )


def _replace_crosslink_block(html: str, new_block_html: str) -> str:
    """
    将页面 HTML 中已有的互链块完整替换为新块。
    如果页面还没有互链块，则在末尾追加。

    返回替换后的完整 HTML。
    """
    start_idx = html.find(_CROSSLINK_TAG_START)
    end_idx = html.find(_CROSSLINK_TAG_END)
    if start_idx == -1 or end_idx == -1:
        return html + '\n' + new_block_html
    end_idx += len(_CROSSLINK_TAG_END)
    return html[:start_idx] + new_block_html + html[end_idx:]


def _extract_friend_link_items(html: str) -> list:
    """从页面互链块中提取带 data-fl 标记的自定义友链 <a>，供互链重建时保留。"""
    start_idx = html.find(_CROSSLINK_TAG_START)
    end_idx = html.find(_CROSSLINK_TAG_END)
    if start_idx == -1 or end_idx == -1:
        return []
    end_idx += len(_CROSSLINK_TAG_END)
    return [
        it for it in re.findall(r'<a\b[^>]*>.*?</a>', html[start_idx:end_idx], flags=re.DOTALL)
        if 'data-fl' in it
    ]


@csrf_exempt
@require_POST
def api_generate_crosslinks(request):
    """
    全量生成智能互链 —— 为每个页面完整替换互链块，保证不遗漏。

    可选的请求参数（JSON）：
      - category_id: int | null  — 仅针对指定分类下的页面进行互链（可选）
                                  不传或传 null 则对所有未排除页面全量互链

    行为：
      1. 获取所有未排除的页面（或指定分类下的页面）
      2. 为每个页面计算"应当链接的所有合作域名"（同组其他页面的根域名，排除自己的）
      3. 若页面已有互链块 → 整体替换为新块（包含全部链接）
      4. 若页面没有互链块 → 在末尾追加新块
      5. 无论点多少次，结果始终一致：每个页面链接所有其他非排除域名
    """
    body, parse_error = parse_json_body(request)
    category_id = None
    if parse_error is None:
        category_id = body.get('category_id')

    # 1. 获取当前用户可见且未排除的页面
    qs = GeneratedPage.objects.filter(crosslink_excluded=False)
    qs = _filter_pages_for_user(request, qs)

    # 若指定了分类，则仅获取该分类下的页面
    category_name = None
    if category_id is not None:
        try:
            category = PageCategory.objects.get(id=category_id)
            category_name = category.name
        except PageCategory.DoesNotExist:
            return err('分类不存在')
        # 预取分类关系筛选
        qs = qs.prefetch_related('categories')
        all_pages = list(qs)
        pages = [p for p in all_pages if list(p.categories.filter(id=category_id))]
    else:
        pages = list(qs)

    if not pages:
        msg = f'分类「{category_name}」下没有可互链的页面' if category_name else '没有可互链的页面'
        return JsonResponse({'message': msg, 'updated_count': 0, 'new_link_count': 0, 'total_pages': 0})

    # 2. 构建 {page_id: {根域名集合}}  + {根域名 → page_name}
    page_root_domains = {}
    root_to_page = {}
    for p in pages:
        domains = set()
        for d in (p.domains or []):
            if d.strip():
                rd = _extract_root_domain(d)
                domains.add(rd)
                root_to_page[rd] = p.name
        if (p.domain or '').strip() and not domains:
            rd = _extract_root_domain(p.domain)
            domains.add(rd)
            root_to_page[rd] = p.name
        page_root_domains[p.id] = domains

    # 3. 为每个页面完整生成互链块
    updated_count = 0
    new_link_count = 0
    for p in pages:
        own_domains = page_root_domains.get(p.id, set())

        # 构建所有合作链接（去重）
        seen = set()
        desired_links = []
        for other_p in pages:
            if other_p.id == p.id:
                continue
            for rd in page_root_domains.get(other_p.id, set()):
                if rd not in own_domains and rd not in seen:
                    seen.add(rd)
                    desired_links.append({
                        'url': rd,
                        'title': root_to_page.get(rd, other_p.name),
                    })

        if not desired_links:
            continue

        # 保留块中已有的自定义友情链接（data-fl），避免互链重建时被覆盖
        friend_items = _extract_friend_link_items(p.html_content)
        new_block = _crosslink_html_block(desired_links, friend_items)
        new_html = _replace_crosslink_block(p.html_content, new_block)

        if new_html == p.html_content:
            continue  # 内容无变化，跳过保存

        p.html_content = new_html
        p.save(update_fields=['html_content', 'updated_time'])
        updated_count += 1
        new_link_count += len(desired_links)

    scope_label = f'分类「{category_name}」' if category_name else '全量'
    if updated_count == 0:
        return JsonResponse({
            'message': f'{scope_label}：所有页面已是最新，无需更新',
            'updated_count': 0,
            'new_link_count': 0,
            'total_pages': len(pages),
            'scope': category_name or 'all',
        })

    return JsonResponse({
        'message': f'智能互链完成（{scope_label}），已更新 {updated_count} 个页面，共 {new_link_count} 条链接',
        'updated_count': updated_count,
        'new_link_count': new_link_count,
        'total_pages': len(pages),
        'scope': category_name or 'all',
    })


def _update_crosslink_names(page, old_name: str):
    """
    当页面名称变更后，同步更新其他页面中指向该页面的互链文字和 title。

    匹配方式：遍历每个绑定域名 → 提取根域名 URL → 在所有其他页面的 HTML
    中找到该 URL 对应的 <a> 标签 → 只替换该标签内的 title 和文字。
    """
    new_name = page.name
    if old_name == new_name:
        return 0

    # 收集该页面所有根域名 URL
    root_urls = set()
    for d in (page.domains or []):
        if d.strip():
            root_urls.add(_extract_root_domain(d))
    if (page.domain or '').strip() and not root_urls:
        root_urls.add(_extract_root_domain(page.domain))
    if not root_urls:
        return 0

    updated = 0
    other_pages = GeneratedPage.objects.exclude(id=page.id).only('id', 'html_content')
    for other in other_pages:
        content = other.html_content
        modified = False
        for url in root_urls:
            # 定位到该 URL 的 <a> 标签范围，只在此范围内替换
            tag_start = content.find(f'<a href="{url}"')
            if tag_start == -1:
                continue
            tag_end = content.find('</a>', tag_start)
            if tag_end == -1:
                continue
            tag_end += 4  # 包含 </a>
            before = content[:tag_start]
            snippet = content[tag_start:tag_end]
            after = content[tag_end:]
            snippet = snippet.replace(f'title="{old_name}"', f'title="{new_name}"')
            snippet = snippet.replace(f'>{old_name}</a>', f'>{new_name}</a>')
            content = before + snippet + after
            modified = True
        if modified:
            GeneratedPage.objects.filter(id=other.id).update(html_content=content, updated_time=timezone.now())
            updated += 1
    return updated


@csrf_exempt
@require_POST
def api_crosslink_exclude_toggle(request):
    """
    切换页面的智能互链排除状态。

    请求: application/json
      {"id": 1, "excluded": true}
    """
    body, error = parse_json_body(request)
    if error is not None:
        return error

    page, error = get_or_404(GeneratedPage, id=body.get('id'), not_found_msg='页面不存在')
    if error is not None:
        return error

    excluded = body.get('excluded', False)
    page.crosslink_excluded = bool(excluded)
    page.save(update_fields=['crosslink_excluded', 'updated_time'])

    return JsonResponse({
        'message': '已' + ('排除' if excluded else '纳入') + '智能互链',
        'crosslink_excluded': page.crosslink_excluded,
    })


