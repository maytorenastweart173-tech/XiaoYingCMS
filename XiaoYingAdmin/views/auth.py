"""
认证视图 — 登录/登出/注册/密码重置/用户管理/登录日志

URL 前缀: /xiaoying_admin/
"""

from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.db.models import Q, Count
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from XiaoYingAdmin.common.http import get_client_ip
from XiaoYingAdmin.models.site_settings import SiteSettings
from XiaoYingAdmin.models.firewall import FirewallRule
from XiaoYingAdmin.models.user import User
from XiaoYingAdmin.models.user_config import UserConfig
from XiaoYingAdmin.models.login_log import LoginLog
from XiaoYingAdmin.models.operation_log import OperationLog
from XiaoYingAdmin.middleware.operation_log import log_operation
from XiaoYingAdmin.utils.backup import backup_model, list_backup_files, restore_model_from_file, get_backup_dir

import json
import os


# =============================================================================
# 模板路径常量
# =============================================================================

LOGIN_TEMPLATE = 'XiaoYingAdmin/登录系统/login.html'
REGISTER_TEMPLATE = 'XiaoYingAdmin/登录系统/register.html'
FORGOT_PASSWORD_TEMPLATE = 'XiaoYingAdmin/登录系统/forgot_password.html'
CHANGE_PASSWORD_TEMPLATE = 'XiaoYingAdmin/用户管理/change_password.html'
USER_LIST_TEMPLATE = 'XiaoYingAdmin/用户管理/user_list.html'
USER_FORM_TEMPLATE = 'XiaoYingAdmin/用户管理/user_form.html'
LOGIN_LOG_TEMPLATE = 'XiaoYingAdmin/用户管理/login_log_list.html'
OPERATION_LOG_TEMPLATE = 'XiaoYingAdmin/用户管理/operation_log_list.html'


# =============================================================================
# 辅助函数
# =============================================================================


def _get_user_agent(request):
    """获取 User-Agent"""
    return request.META.get('HTTP_USER_AGENT', '')[:500]


def _log_login(request, username, user, status, error_msg=''):
    """记录一条登录日志"""
    LoginLog.objects.create(
        user=user,
        username=username,
        ip_address=get_client_ip(request),
        user_agent=_get_user_agent(request),
        status=status,
        error_msg=error_msg,
        session_key=request.session.session_key or '' if status == 'success' else '',
    )


def _auto_block_ip_if_needed(client_ip: str, max_attempts: int):
    """检查 IP 的失败登录次数，超过阈值则自动添加防火墙黑名单"""
    from django.utils import timezone
    recent = timezone.now() - timezone.timedelta(hours=24)
    failed_count = LoginLog.objects.filter(
        ip_address=client_ip,
        login_time__gte=recent,
    ).exclude(status='success').count()

    if failed_count >= max_attempts:
        # 检查是否已存在该 IP 的封禁规则
        if not FirewallRule.objects.filter(rule_type='ip_block', value=client_ip).exists():
            FirewallRule.objects.create(
                rule_type='ip_block',
                value=client_ip,
                is_active=True,
                response_type='forbidden',
                description=f'自动封禁：登录失败超阈值（{failed_count}次）',
            )


# =============================================================================
# 登录 / 登出
# =============================================================================


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def login_view(request):
    """登录页: GET 展示表单, POST 验证登录"""
    if request.user.is_authenticated:
        return HttpResponseRedirect(reverse('index'))

    client_ip = get_client_ip(request)
    site_settings = SiteSettings.objects.first()

    # =====================================================================
    # IP 白名单检查：如果配置了白名单，非白名单 IP 禁止看到登录页
    # =====================================================================
    if site_settings and site_settings.login_ip_whitelist.strip():
        whitelist_ips = [
            ip.strip() for ip in site_settings.login_ip_whitelist.strip().split('\n')
            if ip.strip()
        ]
        if whitelist_ips and client_ip not in whitelist_ips:
            return HttpResponse(
                '<h1 style="text-align:center;margin-top:15%;color:#999;">'
                '访问被拒绝<br><span style="font-size:14px;">您的 IP 不在登录白名单中</span></h1>',
                status=403, content_type='text/html; charset=utf-8',
            )

    # =====================================================================
    # 域名白名单检查：如果配置了允许的域名，非白名单域名禁止访问后台
    # =====================================================================
    if site_settings and site_settings.allowed_admin_domains.strip():
        from XiaoYingAdmin.common.http import get_request_host, is_host_allowed
        current_host = get_request_host(request)
        if not is_host_allowed(current_host, site_settings.allowed_admin_domains):
            return HttpResponse(
                '<h1 style="text-align:center;margin-top:15%;color:#999;">'
                '访问被拒绝<br><span style="font-size:14px;">'
                '请通过指定的域名访问后台管理</span></h1>',
                status=403, content_type='text/html; charset=utf-8',
            )

    # =====================================================================
    # 检查当前 IP 是否已被自动封禁
    # =====================================================================
    if site_settings and site_settings.max_login_attempts > 0:
        blocked = FirewallRule.objects.filter(
            rule_type='ip_block', value=client_ip, is_active=True,
        ).exists()
        if blocked:
            return HttpResponse(
                f'<h1 style="text-align:center;margin-top:15%;color:#999;">'
                f'登录失败次数过多<br><span style="font-size:14px;">'
                f'您的 IP（{client_ip}）已被临时封禁，请稍后重试</span></h1>',
                status=403, content_type='text/html; charset=utf-8',
            )

    error = None
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')

        if not username or not password:
            error = '请输入用户名和密码'
        else:
            # 先按用户名查找用户，区分"不存在"和"已禁用"
            try:
                user_obj = User.objects.get(username=username)
                if not user_obj.is_active:
                    error = '该用户已被禁用，无法登录'
                    _log_login(request, username, user_obj, 'failed_disabled', '用户已被禁用')
                else:
                    # 用户存在且激活，验证密码
                    authenticated_user = authenticate(request, username=username, password=password)
                    if authenticated_user is not None:
                        login(request, authenticated_user)
                        _log_login(request, username, authenticated_user, 'success')
                        next_url = request.GET.get('next') or reverse('index')
                        return HttpResponseRedirect(next_url)
                    else:
                        error = '用户名或密码错误'
                        _log_login(request, username, user_obj, 'failed_password', '密码错误')
            except User.DoesNotExist:
                error = '用户名或密码错误'
                _log_login(request, username, None, 'failed_not_found', '用户不存在')

            # =============================================================
            # 登录失败后：检查是否需要自动封禁此 IP
            # =============================================================
            if error and site_settings and site_settings.max_login_attempts > 0:
                _auto_block_ip_if_needed(client_ip, site_settings.max_login_attempts)

    # 检查是否开放注册，传递给模板
    reg_config = UserConfig.get_singleton()
    return render(request, LOGIN_TEMPLATE, {
        'error': error,
        'registration_enabled': reg_config.registration_enabled,
    })


@require_http_methods(['GET'])
def logout_view(request):
    """登出: 清除 session 后跳转到登录页"""
    logout(request)
    return HttpResponseRedirect(reverse('login'))


# =============================================================================
# 注册
# =============================================================================


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def register_view(request):
    """
    注册页: GET 展示表单, POST 提交注册。
    需 UserConfig.registration_enabled == True 才允许注册。
    """
    # 已登录直接跳首页
    if request.user.is_authenticated:
        return HttpResponseRedirect(reverse('index'))

    reg_config = UserConfig.get_singleton()
    if not reg_config.registration_enabled:
        return render(request, REGISTER_TEMPLATE, {
            'error': '管理员已关闭注册功能',
            'registration_enabled': False,
        })

    error = None
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        password2 = request.POST.get('password2', '')

        # 校验
        if not username or not password:
            error = '用户名和密码为必填项'
        elif len(username) < 3:
            error = '用户名至少 3 个字符'
        elif password != password2:
            error = '两次输入的密码不一致'
        elif len(password) < 6:
            error = '密码至少 6 个字符'
        elif User.objects.filter(username=username).exists():
            error = '该用户名已被注册'
        elif email and User.objects.filter(email=email).exists():
            error = '该邮箱已被使用'
        else:
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                is_active=True,
            )
            # 自动登录
            authenticated_user = authenticate(request, username=username, password=password)
            if authenticated_user:
                login(request, authenticated_user)
            return HttpResponseRedirect(reverse('index'))

    return render(request, REGISTER_TEMPLATE, {
        'error': error,
        'registration_enabled': True,
    })


# =============================================================================
# 忘记密码
# =============================================================================


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def forgot_password_view(request):
    """
    忘记密码: 通过 用户名+邮箱 验证身份后重置密码。
    仅当开放注册时可用（防止恶意利用）。
    """
    if request.user.is_authenticated:
        return HttpResponseRedirect(reverse('index'))

    reg_config = UserConfig.get_singleton()

    error = None
    success = None

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        password2 = request.POST.get('password2', '')

        if not reg_config.registration_enabled:
            error = '系统未开放自助密码重置，请联系管理员'
        elif not username or not email:
            error = '用户名和邮箱为必填项'
        elif password != password2:
            error = '两次输入的密码不一致'
        elif len(password) < 6:
            error = '密码至少 6 个字符'
        else:
            try:
                user = User.objects.get(username=username, email=email, is_active=True)
                user.set_password(password)
                user.save(update_fields=['password'])
                success = '密码重置成功，请使用新密码登录'
            except User.DoesNotExist:
                error = '用户名与邮箱不匹配，或该用户已被禁用'

    return render(request, FORGOT_PASSWORD_TEMPLATE, {
        'error': error,
        'success': success,
        'registration_enabled': reg_config.registration_enabled,
    })


# =============================================================================
# 修改密码（已登录）
# =============================================================================


@login_required
@csrf_exempt
@require_http_methods(['GET', 'POST'])
def change_password_view(request):
    """已登录用户修改自己的密码"""
    error = None
    success = None

    if request.method == 'POST':
        old_pw = request.POST.get('old_password', '')
        new_pw = request.POST.get('new_password', '')
        new_pw2 = request.POST.get('new_password2', '')

        if not request.user.check_password(old_pw):
            error = '当前密码不正确'
        elif not new_pw:
            error = '新密码不能为空'
        elif new_pw != new_pw2:
            error = '两次输入的新密码不一致'
        elif len(new_pw) < 6:
            error = '新密码至少 6 个字符'
        elif old_pw == new_pw:
            error = '新密码不能与当前密码相同'
        else:
            request.user.set_password(new_pw)
            request.user.save(update_fields=['password'])
            # 保持会话不退出
            update_session_auth_hash(request, request.user)
            success = '密码修改成功'

    return render(request, CHANGE_PASSWORD_TEMPLATE, {
        'error': error,
        'success': success,
    })


# =============================================================================
# 用户管理（需 is_staff / is_superuser 权限）
# =============================================================================

def _check_admin(request):
    """检查当前用户是否有用户管理权限"""
    return bool(request.user.is_authenticated and (request.user.is_superuser or request.user.is_staff))


@login_required
@require_http_methods(['GET'])
def user_list_view(request):
    """用户列表页"""
    if not _check_admin(request):
        return render(request, USER_LIST_TEMPLATE, {
            'error': '权限不足，仅管理员可管理用户',
            'users': [],
            'groups': [],
        })

    # 搜索
    q = request.GET.get('q', '').strip()
    users = User.objects.all().order_by('-date_joined')
    if q:
        users = users.filter(
            Q(username__icontains=q) |
            Q(email__icontains=q) |
            Q(phone__icontains=q)
        )

    groups = Group.objects.all().order_by('name')

    return render(request, USER_LIST_TEMPLATE, {
        'users': users,
        'groups': groups,
        'search_query': q,
    })


@login_required
@csrf_exempt
@require_http_methods(['GET', 'POST'])
def user_create_view(request):
    """创建用户"""
    if not _check_admin(request):
        return render(request, USER_FORM_TEMPLATE, {
            'error': '权限不足，仅管理员可创建用户',
            'form_mode': 'create',
        })

    error = None
    success = None

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        is_active = request.POST.get('is_active') == 'on'
        is_staff = request.POST.get('is_staff') == 'on'
        is_superuser = request.POST.get('is_superuser') == 'on'
        group_ids = request.POST.getlist('groups')

        if not username or not password:
            error = '用户名和密码为必填项'
        elif User.objects.filter(username=username).exists():
            error = '该用户名已被注册'
        else:
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                is_active=is_active,
                is_staff=is_staff,
                is_superuser=is_superuser,
            )
            # 设置用户组
            if group_ids:
                groups = Group.objects.filter(id__in=group_ids)
                user.groups.set(groups)

            log_operation(request, 'create', 'User', user.pk,
                          f'用户 {username}',
                          detail={'username': username, 'is_staff': is_staff, 'is_superuser': is_superuser})
            success = f'用户 {username} 创建成功'
            # 继续留在创建页面（可继续创建）

    all_groups = Group.objects.all().order_by('name')
    return render(request, USER_FORM_TEMPLATE, {
        'error': error,
        'success': success,
        'form_mode': 'create',
        'all_groups': all_groups,
        'target_user': None,
    })


@login_required
@csrf_exempt
@require_http_methods(['GET', 'POST'])
def user_edit_view(request, pk):
    """编辑用户"""
    if not _check_admin(request):
        return render(request, USER_FORM_TEMPLATE, {
            'error': '权限不足，仅管理员可编辑用户',
            'form_mode': 'edit',
        })

    target_user = get_object_or_404(User, pk=pk)
    error = None
    success = None

    # 权限校验：非超级管理员只能编辑自己
    if not request.user.is_superuser and request.user.pk != target_user.pk:
        return render(request, USER_FORM_TEMPLATE, {
            'error': '权限不足，普通管理员只能编辑自己的信息',
            'form_mode': 'edit',
            'all_groups': Group.objects.all().order_by('name'),
            'user_group_ids': [],
            'target_user': target_user,
        })

    # 是否允许重置密码：仅超级管理员可以
    can_reset_password = request.user.is_superuser

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        phone = request.POST.get('phone', '').strip()
        is_active = request.POST.get('is_active') == 'on'
        is_staff = request.POST.get('is_staff') == 'on'
        is_superuser = request.POST.get('is_superuser') == 'on'
        group_ids = request.POST.getlist('groups')
        new_password = request.POST.get('new_password', '')

        if not username:
            error = '用户名不能为空'
        elif User.objects.filter(username=username).exclude(pk=pk).exists():
            error = '该用户名已被其他用户使用'
        else:
            # 记录修改前的值，用于生成 diff
            old_vals = {
                'username': target_user.username,
                'email': target_user.email,
                'phone': target_user.phone,
                'is_active': target_user.is_active,
                'is_staff': target_user.is_staff,
                'is_superuser': target_user.is_superuser,
            }

            target_user.username = username
            target_user.email = email
            target_user.phone = phone
            target_user.is_active = is_active
            # 权限保护：非超级管理员不可提升自己或他人的权限
            if request.user.is_superuser:
                target_user.is_staff = is_staff
                target_user.is_superuser = is_superuser
            # is_active 不做回退（保留表单提交值）

            if new_password:
                if not can_reset_password:
                    error = '权限不足，仅超级管理员可重置密码'
                elif len(new_password) < 6:
                    error = '密码至少 6 个字符'
                else:
                    target_user.set_password(new_password)

            if not error:
                target_user.save()
                # 设置用户组
                if group_ids:
                    groups = Group.objects.filter(id__in=group_ids)
                    target_user.groups.set(groups)
                else:
                    target_user.groups.clear()

                # 计算实际变更的字段
                new_vals = {
                    'username': target_user.username,
                    'email': target_user.email,
                    'phone': target_user.phone,
                    'is_active': target_user.is_active,
                    'is_staff': target_user.is_staff,
                    'is_superuser': target_user.is_superuser,
                }
                changes = {}
                field_labels = {
                    'username': '用户名', 'email': '邮箱', 'phone': '手机号',
                    'is_active': '启用状态', 'is_staff': '管理员权限',
                    'is_superuser': '超级管理员',
                }
                for f, label in field_labels.items():
                    if old_vals[f] != new_vals[f]:
                        changes[field_labels.get(f, f)] = {'old': old_vals[f], 'new': new_vals[f]}
                if new_password:
                    changes['登录密码'] = {'old': '***', 'new': '已重置'}

                log_operation(request, 'update', 'User', target_user.pk,
                              f'用户 {target_user.username}',
                              detail={'changes': changes} if changes else {'info': '信息已保存'})
                success = '用户信息已保存'

    all_groups = Group.objects.all().order_by('name')
    user_group_ids = list(target_user.groups.values_list('id', flat=True))

    return render(request, USER_FORM_TEMPLATE, {
        'error': error,
        'success': success,
        'form_mode': 'edit',
        'all_groups': all_groups,
        'user_group_ids': user_group_ids,
        'target_user': target_user,
        'can_reset_password': can_reset_password,
    })


@login_required
@require_http_methods(['POST'])
def user_toggle_active_api(request, pk):
    """切换用户激活状态（AJAX）"""
    if not request.user.is_superuser:
        return JsonResponse({'ok': False, 'error': '权限不足，仅超级管理员可禁用/启用用户'})

    if request.user.pk == pk:
        return JsonResponse({'ok': False, 'error': '不能禁用自己'})

    target_user = get_object_or_404(User, pk=pk)
    old_active = target_user.is_active
    target_user.is_active = not old_active
    target_user.save(update_fields=['is_active'])

    new_status = '启用' if target_user.is_active else '禁用'
    log_operation(request, 'update', 'User', target_user.pk,
                  f'用户 {target_user.username} → {new_status}',
                  detail={'changes': {'is_active': {'old': old_active, 'new': target_user.is_active}}})

    return JsonResponse({
        'ok': True,
        'is_active': target_user.is_active,
        'username': target_user.username,
    })


@login_required
@require_http_methods(['POST'])
def user_delete_api(request, pk):
    """删除用户（AJAX）"""
    if not _check_admin(request):
        return JsonResponse({'ok': False, 'error': '权限不足'})

    if request.user.pk == pk:
        return JsonResponse({'ok': False, 'error': '不能删除自己'})

    target_user = get_object_or_404(User, pk=pk)
    username = target_user.username
    target_user.delete()

    log_operation(request, 'delete', 'User', pk, f'用户 {username}')

    return JsonResponse({
        'ok': True,
        'username': username,
    })


# =============================================================================
# 用户管理 — 用户信息 AJAX 接口
# =============================================================================


@login_required
@require_http_methods(['GET'])
def user_info_api(request):
    """返回当前登录用户的信息（AJAX）"""
    user = request.user
    groups = list(user.groups.values_list('name', flat=True))
    return JsonResponse({
        'ok': True,
        'data': {
            'id': user.pk,
            'username': user.username,
            'email': user.email,
            'phone': user.phone,
            'is_superuser': user.is_superuser,
            'is_staff': user.is_staff,
            'groups': groups,
            'date_joined': user.date_joined.strftime('%Y-%m-%d %H:%M:%S'),
            'last_login': user.last_login.strftime('%Y-%m-%d %H:%M:%S') if user.last_login else None,
        }
    })


# =============================================================================
# 登录日志（需 is_superuser）
# =============================================================================


@login_required
@require_http_methods(['GET'])
def login_log_view(request):
    """登录日志列表页（仅超级管理员可查看）"""
    if not request.user.is_superuser:
        return render(request, LOGIN_LOG_TEMPLATE, {
            'error': '权限不足，仅超级管理员可查看登录日志',
            'logs': [],
            'stats': {},
        })

    # 统计：每个用户的登录情况
    stats = LoginLog.objects.values('username').annotate(
        total=Count('id'),
        success=Count('id', filter=Q(status='success')),
        failed=Count('id', filter=~Q(status='success')),
    ).order_by('-total')

    return render(request, LOGIN_LOG_TEMPLATE, {
        'error': None,
        'logs': [],
        'stats': list(stats),
    })


@login_required
@require_http_methods(['GET'])
def login_log_list_api(request):
    """登录日志列表 AJAX API（仅超级管理员）"""
    if not request.user.is_superuser:
        return JsonResponse({'ok': False, 'error': '权限不足'})

    page = int(request.GET.get('page', 1))
    limit = int(request.GET.get('limit', 20))
    username_q = request.GET.get('username', '').strip()
    status_q = request.GET.get('status', '').strip()

    logs = LoginLog.objects.all()
    if username_q:
        logs = logs.filter(username__icontains=username_q)
    if status_q:
        logs = logs.filter(status=status_q)

    total = logs.count()
    offset = (page - 1) * limit
    items = logs[offset:offset + limit]

    # 获取所有已封禁的 IP（活跃的 ip_block 规则）
    from XiaoYingAdmin.models.firewall import FirewallRule
    banned_ips = set(
        FirewallRule.objects.filter(
            rule_type='ip_block', is_active=True
        ).values_list('value', flat=True)
    )

    data = []
    for log in items:
        ip = log.ip_address or ''
        data.append({
            'id': log.pk,
            'username': log.username,
            'user_id': log.user_id,
            'ip_address': ip,
            'banned': ip in banned_ips,
            'user_agent': log.user_agent[:100] if log.user_agent else '',
            'status': log.status,
            'status_display': log.get_status_display(),
            'error_msg': log.error_msg,
            'login_time': log.login_time.strftime('%Y-%m-%d %H:%M:%S'),
        })

    return JsonResponse({
        'ok': True,
        'total': total,
        'data': data,
    })


@login_required
@csrf_exempt
@require_POST
def operation_log_backup_api(request):
    """操作日志备份 API"""
    if not request.user.is_superuser:
        return JsonResponse({'ok': False, 'error': '权限不足'})

    import json
    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        body = {}
    action = body.get('action', 'backup')

    total = OperationLog.objects.count()
    if total == 0:
        return JsonResponse({'ok': False, 'error': '没有日志可备份', 'count': 0})

    try:
        result = backup_model(OperationLog, 'op_logs')
    except Exception as e:
        return JsonResponse({'ok': False, 'error': f'备份失败: {e}'})

    if action == 'backup_and_delete':
        OperationLog.objects.all().delete()

    return JsonResponse({
        'ok': True,
        'message': f'备份成功，共 {result["count"]} 条',
        'result': {
            'filename': result['filename'],
            'count': result['count'],
            'size_str': result['size_str'],
        },
        'deleted': action == 'backup_and_delete',
    })


@login_required
@csrf_exempt
@require_POST
def operation_log_restore_api(request):
    """操作日志恢复 API"""
    if not request.user.is_superuser:
        return JsonResponse({'ok': False, 'error': '权限不足'})

    import json
    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': '无效的 JSON'})

    filename = (body.get('filename') or '').strip()
    if not filename:
        return JsonResponse({'ok': False, 'error': '请指定备份文件名'})

    backup_dir = get_backup_dir()
    filepath = os.path.join(backup_dir, filename)
    if not os.path.isfile(filepath):
        return JsonResponse({'ok': False, 'error': f'备份文件不存在: {filename}'})

    overwrite = body.get('overwrite', False)
    if overwrite:
        OperationLog.objects.all().delete()

    result = restore_model_from_file(filepath, OperationLog)
    if not result['success']:
        return JsonResponse({'ok': False, 'error': result.get('error', '恢复失败')})

    return JsonResponse({
        'ok': True,
        'message': f'恢复完成：成功导入 {result["imported"]} 条',
        'result': result,
    })


@login_required
@require_GET
def operation_log_backup_list_api(request):
    """获取操作日志备份文件列表"""
    if not request.user.is_superuser:
        return JsonResponse({'ok': False, 'error': '权限不足'})
    files = list_backup_files('op_logs')
    return JsonResponse({'ok': True, 'files': files})


# =============================================================================
# 操作日志（需 is_superuser）
# =============================================================================


@login_required
@require_http_methods(['GET'])
def operation_log_view(request):
    """操作日志列表页（仅超级管理员可查看）"""
    if not request.user.is_superuser:
        return render(request, OPERATION_LOG_TEMPLATE, {
            'error': '权限不足，仅超级管理员可查看操作日志',
            'stats': {},
        })

    # 统计：按操作类型
    stats = OperationLog.objects.values('action').annotate(
        count=Count('id'),
    ).order_by('-count')

    # 今日操作数
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_count = OperationLog.objects.filter(created_at__gte=today_start).count()

    return render(request, OPERATION_LOG_TEMPLATE, {
        'error': None,
        'stats': list(stats),
        'today_count': today_count,
        'total_count': OperationLog.objects.count(),
    })


@login_required
@require_http_methods(['GET'])
def operation_log_list_api(request):
    """操作日志列表 AJAX API（仅超级管理员）"""
    if not request.user.is_superuser:
        return JsonResponse({'ok': False, 'error': '权限不足'})

    page = int(request.GET.get('page', 1))
    limit = int(request.GET.get('limit', 20))
    username_q = request.GET.get('username', '').strip()
    action_q = request.GET.get('action', '').strip()
    target_type_q = request.GET.get('target_type', '').strip()

    logs = OperationLog.objects.all()
    if username_q:
        logs = logs.filter(username__icontains=username_q)
    if action_q:
        logs = logs.filter(action=action_q)
    if target_type_q:
        logs = logs.filter(target_type__icontains=target_type_q)

    total = logs.count()
    offset = (page - 1) * limit
    items = logs[offset:offset + limit]

    data = []
    for log in items:
        data.append({
            'id': log.pk,
            'username': log.username,
            'user_id': log.user_id,
            'action': log.action,
            'action_display': log.get_action_display(),
            'target_type': log.target_type,
            'target_id': log.target_id,
            'target_repr': log.target_repr,
            'detail': log.detail,
            'method': log.method,
            'path': log.path,
            'ip_address': log.ip_address or '',
            'user_agent': log.user_agent[:100] if log.user_agent else '',
            'created_at': log.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        })

    return JsonResponse({
        'ok': True,
        'total': total,
        'data': data,
    })
