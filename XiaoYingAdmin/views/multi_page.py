"""
多页面管理视图 — 项目 CRUD + AI 生成 + 页面编辑。
"""
import json
import logging
import re as _re

from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.urls import reverse
from django.views.decorators.http import require_POST, require_GET

from XiaoYingAdmin.models.multi_page_project import MultiPageProject
from XiaoYingAdmin.models.multi_page import MultiPage
from XiaoYingAdmin.models.multi_page_config import MultiPageConfig
from XiaoYingAdmin.views.multi_page_generator import (
    start_multi_page_generation,
    get_multi_gen_progress,
)

logger = logging.getLogger('XiaoYingAdmin.multi_page')


# ---------------------------------------------------------------------------
# 页面视图
# ---------------------------------------------------------------------------

def multi_page_list_view(request):
    """多页面项目列表页"""
    projects = MultiPageProject.objects.select_related('created_by').all()
    return render(request, 'XiaoYingAdmin/页面管理/多页面管理/项目列表.html', {
        'projects': projects,
    })


def multi_page_create_view(request):
    """创建项目页 — 创建后直接启动 AI 生成，无需二次点击"开始生成"。"""
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        root_domain = request.POST.get('root_domain', '').strip().rstrip('/')
        theme = request.POST.get('theme', '').strip()
        style = request.POST.get('style', 'modern').strip()

        if not name:
            name = theme[:50] or '未命名项目'

        project = MultiPageProject.objects.create(
            name=name,
            root_domain=root_domain,
            theme=theme,
            style=style,
            status=MultiPageProject.Status.DRAFT,
            created_by=request.user if request.user.is_authenticated else None,
        )
        # 创建后直接启动 AI 生成（异步线程），跳过"开始生成"二次确认
        start_multi_page_generation(project.id)
        return redirect('multi_page_project_detail', project_id=project.id)

    return render(request, 'XiaoYingAdmin/页面管理/多页面管理/创建项目.html')


def multi_page_project_detail_view(request, project_id):
    """项目详情页 — 树形展示所有页面 + 操作按钮"""
    project = get_object_or_404(MultiPageProject, pk=project_id)
    pages = project.pages.all()

    return render(request, 'XiaoYingAdmin/页面管理/多页面管理/项目详情.html', {
        'project': project,
        'pages': pages,
    })


def multi_page_edit_view(request, page_id):
    """编辑单个页面（HTML + SEO meta）"""
    page = get_object_or_404(MultiPage, pk=page_id)

    if request.method == 'POST':
        page.name = request.POST.get('name', page.name).strip()
        page.nav_title = request.POST.get('nav_title', '').strip() or page.name
        page.url_path = request.POST.get('url_path', page.url_path).strip()
        page.title = request.POST.get('title', '').strip()[:500]
        page.description = request.POST.get('description', '').strip()[:500]
        page.keywords = request.POST.get('keywords', '').strip()[:500]
        page.html_content = request.POST.get('html_content', page.html_content)
        page.save()
        return redirect('multi_page_project_detail', project_id=page.project_id)

    return render(request, 'XiaoYingAdmin/页面管理/多页面管理/编辑页面.html', {
        'page': page,
    })


_FALLBACK_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;line-height:1.6;color:#333;background:#fff}
header{background:linear-gradient(135deg,#1a73e8,#0d47a1);padding:0 20px;position:sticky;top:0;z-index:100;box-shadow:0 2px 8px rgba(0,0,0,.15)}
nav{max-width:1200px;margin:0 auto}
nav ul{list-style:none;display:flex;gap:4px;overflow-x:auto}
nav ul li a{display:block;padding:14px 20px;color:rgba(255,255,255,.85);text-decoration:none;font-size:15px;font-weight:500;transition:all .2s;white-space:nowrap;border-bottom:3px solid transparent}
nav ul li a:hover,nav ul li a.active{color:#fff;border-bottom-color:#ffd54f;background:rgba(255,255,255,.1)}
main{max-width:1200px;margin:0 auto;padding:20px;min-height:60vh}
section{padding:40px 0}
.hero{text-align:center;padding:80px 20px 60px;background:linear-gradient(135deg,#e3f2fd,#bbdefb);border-radius:12px;margin:20px 0}
.hero h1{font-size:2.4em;color:#0d47a1;margin-bottom:16px}
.hero p{font-size:1.15em;color:#555;max-width:600px;margin:0 auto 28px}
.cta-button,.cta-btn{display:inline-block;padding:14px 36px;background:linear-gradient(135deg,#1a73e8,#0d47a1);color:#fff!important;border-radius:6px;text-decoration:none;font-size:16px;font-weight:600;transition:transform .2s,box-shadow .2s;box-shadow:0 4px 14px rgba(26,115,232,.35)}
.cta-button:hover,.cta-btn:hover{transform:translateY(-2px);box-shadow:0 6px 20px rgba(26,115,232,.45)}
h1{font-size:2em;color:#0d47a1;margin-bottom:16px}
h2{font-size:1.5em;color:#1a3a5c;margin:32px 0 16px}
h3{font-size:1.15em;color:#333;margin-bottom:8px}
.features-preview h2{text-align:center;margin-bottom:32px}
.feature-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:24px}
.feature-item{background:#fff;border:1px solid #e8edf2;border-radius:10px;padding:28px 24px;transition:box-shadow .3s,transform .3s}
.feature-item:hover{box-shadow:0 8px 24px rgba(0,0,0,.08);transform:translateY(-4px)}
.feature-item h3{color:#1a73e8}
.cta-banner{text-align:center;padding:60px 20px;background:linear-gradient(135deg,#1a73e8,#0d47a1);border-radius:12px;color:#fff;margin:20px 0}
.cta-banner h2{color:#fff;font-size:1.8em}
.cta-banner .cta-button{background:#fff;color:#1a73e8!important;box-shadow:0 4px 14px rgba(0,0,0,.2)}
.download-options{display:flex;gap:16px;justify-content:center;flex-wrap:wrap;margin:24px 0}
.download-btn,.download-btn-win,.download-btn-mac{display:inline-block;padding:16px 32px;border-radius:8px;text-decoration:none;font-size:15px;font-weight:600;transition:transform .2s;min-width:160px;text-align:center}
.download-btn,.download-btn-win{background:#1a73e8;color:#fff!important}
.download-btn-mac{background:#2d3748;color:#fff!important}
.download-btn:hover,.download-btn-win:hover,.download-btn-mac:hover{transform:translateY(-3px)}
article{margin-bottom:24px;padding:20px 24px;border:1px solid #e8edf2;border-radius:8px;background:#fafbfc}
article h2{margin-top:0}
blockquote{border-left:4px solid #1a73e8;margin:12px 0;padding:12px 20px;background:#f5f8ff;border-radius:0 8px 8px 0;font-style:italic}
img{max-width:100%;height:auto;border-radius:8px;margin:16px 0}
footer{background:#1a3a5c;color:rgba(255,255,255,.7);text-align:center;padding:24px 20px;font-size:14px;margin-top:40px}
@media(max-width:768px){.hero h1{font-size:1.6em}.hero{padding:40px 16px}.feature-grid{grid-template-columns:1fr}nav ul li a{padding:12px 14px;font-size:14px}}
"""


def _sanitize_html(html: str) -> str:
    """清理 HTML：移除外部 CSS 引用，注入备选样式。"""
    # 移除 <link rel="stylesheet"> 标签
    cleaned = _re.sub(
        r'<link\s[^>]*rel=["\']stylesheet["\'][^>]*/?>',
        '',
        html,
        flags=_re.IGNORECASE,
    )
    # 如果没有 <style> 标签，注入备选样式
    if '<style' not in cleaned:
        # 在 </head> 前插入
        cleaned = cleaned.replace(
            '</head>',
            f'<style>{_FALLBACK_CSS}</style></head>',
        )
    return cleaned


@require_GET
def multi_page_preview_view(request, page_id):
    """
    预览多页面 — 绕过斗篷中间件，直接渲染页面 HTML。

    走 /xiaoying_admin/ 前缀路径，SeoCloakMiddleware 会跳过此路径，
    因此不会被斗篷拦截或重定向。
    """
    page = get_object_or_404(MultiPage, pk=page_id)
    clean_html = _sanitize_html(page.html_content)
    return HttpResponse(clean_html, content_type='text/html; charset=utf-8')


# ---------------------------------------------------------------------------
# API: AI 生成
# ---------------------------------------------------------------------------

@require_POST
def api_multi_page_start_generate(request, project_id):
    """启动 AI 多页面生成。"""
    project = get_object_or_404(MultiPageProject, pk=project_id)
    if project.status == MultiPageProject.Status.GENERATING:
        return JsonResponse({'error': '该项目正在生成中，请勿重复操作'}, status=400)

    start_multi_page_generation(project_id)
    return JsonResponse({
        'message': '已启动多页面生成',
        'project_id': project_id,
    })


@require_GET
def api_multi_page_gen_progress(request, project_id):
    """轮询多页面生成进度。"""
    progress = get_multi_gen_progress(project_id)
    return JsonResponse(progress)


# ---------------------------------------------------------------------------
# API: 项目 CRUD
# ---------------------------------------------------------------------------


def _check_domain_occupied(domain: str, exclude_project_id: int = None):
    """检查域名是否已被占用。

    返回 (is_occupied, info_dict or None)。
    info_dict = {'type': 'multi_page'|'single_page', 'id': int, 'name': str}
    """
    domain = domain.strip().lower()

    # 1. 检查多页面项目
    qs = MultiPageProject.objects.filter(is_enabled=True, enabled_domain=domain)
    if exclude_project_id:
        qs = qs.exclude(pk=exclude_project_id)
    occ = qs.first()
    if occ:
        return True, {
            'type': 'multi_page',
            'id': occ.pk,
            'name': occ.name,
            'domain': occ.enabled_domain,
        }

    # 2. 检查单页面（GeneratedPage.domains JSON 字段）
    from XiaoYingAdmin.models.generated_page import GeneratedPage
    for page in GeneratedPage.objects.exclude(domains=[]).iterator():
        if domain in [d.strip().lower() for d in (page.domains or [])]:
            return True, {
                'type': 'single_page',
                'id': page.pk,
                'name': page.name,
                'domain': domain,
            }
    # 兼容旧字段
    page = GeneratedPage.objects.filter(domain=domain).first()
    if page:
        return True, {
            'type': 'single_page',
            'id': page.pk,
            'name': page.name,
            'domain': domain,
        }

    return False, None


def _release_domain(info: dict):
    """解除指定项目/页面占用的域名。"""
    occ_type = info['type']
    occ_id = info['id']
    domain = info['domain']

    if occ_type == 'multi_page':
        proj = MultiPageProject.objects.get(pk=occ_id)
        proj.is_enabled = False
        proj.enabled_domain = ''
        proj.save(update_fields=['is_enabled', 'enabled_domain', 'updated_time'])
    elif occ_type == 'single_page':
        from XiaoYingAdmin.models.generated_page import GeneratedPage
        page = GeneratedPage.objects.get(pk=occ_id)
        # 从 domains JSON 列表中移除
        if page.domains:
            page.domains = [d for d in page.domains if d.strip().lower() != domain]
        # 如果旧字段匹配则清空
        if page.domain and page.domain.strip().lower() == domain:
            page.domain = None
        page.save(update_fields=['domains', 'domain'])


@require_POST
def api_multi_page_enable(request, project_id):
    """启用多页面项目，绑定域名。

    支持 force 参数：当域名被占用时，自动解除原占用并重新绑定。
    """
    project = get_object_or_404(MultiPageProject, pk=project_id)

    if project.status != MultiPageProject.Status.COMPLETED:
        return JsonResponse(
            {'error': '只有「已完成」的项目才能启用'},
            status=400,
        )

    if not project.pages.exists():
        return JsonResponse(
            {'error': '项目中没有页面，请先生成页面'},
            status=400,
        )

    import json as _json
    body = _json.loads(request.body)
    domain = body.get('domain', '').strip().lower()
    force = body.get('force', False)

    if not domain:
        return JsonResponse({'error': '请输入要绑定的域名'}, status=400)

    # 检查域名占用
    occupied, info = _check_domain_occupied(domain, exclude_project_id=project_id)

    if occupied:
        if force:
            # 强制模式：解除原占用
            _release_domain(info)
        else:
            # 非强制模式：返回占用信息，让前端确认
            return JsonResponse({
                'error': f'域名 "{domain}" 已被{info["type"]=="multi_page" and "多页面项目" or "单页面"}「{info["name"]}」占用',
                'occupied': True,
                'occupier': info,
            }, status=409)  # 409 Conflict

    project.is_enabled = True
    project.enabled_domain = domain
    project.save(update_fields=['is_enabled', 'enabled_domain', 'updated_time'])

    return JsonResponse({
        'message': f'项目已启用，域名 {domain} 绑定成功',
        'is_enabled': True,
        'enabled_domain': domain,
    })


@require_POST
def api_multi_page_disable(request, project_id):
    """停用多页面项目，解绑域名。"""
    project = get_object_or_404(MultiPageProject, pk=project_id)
    project.is_enabled = False
    project.enabled_domain = ''
    project.save(update_fields=['is_enabled', 'enabled_domain', 'updated_time'])

    return JsonResponse({
        'message': '项目已停用，域名已解绑',
        'is_enabled': False,
    })

@require_POST
def api_multi_page_delete_project(request, project_id):
    """删除项目（级联删除所有页面）。"""
    project = get_object_or_404(MultiPageProject, pk=project_id)
    project.delete()
    return JsonResponse({'message': '项目已删除'})


@require_POST
def api_multi_page_regenerate(request, project_id):
    """重新生成（重置并重新生成）。"""
    project = get_object_or_404(MultiPageProject, pk=project_id)
    # 删除已有页面
    project.pages.all().delete()
    project.nav_config = []
    project.status = MultiPageProject.Status.DRAFT
    project.save(update_fields=['nav_config', 'status', 'updated_time'])

    start_multi_page_generation(project_id)
    return JsonResponse({
        'message': '已重新启动生成',
        'project_id': project_id,
    })


# ---------------------------------------------------------------------------
# API: 页面 CRUD
# ---------------------------------------------------------------------------

@require_POST
def api_multi_page_delete_page(request, page_id):
    """删除页面。"""
    page = get_object_or_404(MultiPage, pk=page_id)
    project_id = page.project_id
    page.delete()

    # 更新已保存的 nav_config
    try:
        project = MultiPageProject.objects.get(pk=project_id)
        project.nav_config = [n for n in project.nav_config if n.get('url_path') != page.url_path]
        project.save(update_fields=['nav_config', 'updated_time'])
    except Exception:
        pass

    return JsonResponse({'message': '页面已删除', 'project_id': project_id})


@require_POST
def api_multi_page_add_page(request, project_id):
    """手动添加页面到项目。"""
    project = get_object_or_404(MultiPageProject, pk=project_id)
    body = json.loads(request.body)
    name = body.get('name', '').strip()
    url_path = body.get('url_path', '').strip()
    html_content = body.get('html_content', '<!DOCTYPE html><html><head><title>新页面</title></head><body><h1>新页面</h1></body></html>')

    if not name:
        return JsonResponse({'error': '页面名称不能为空'}, status=400)
    if not url_path:
        url_path = '/' + name.lower().replace(' ', '-') + '.html'
    if not url_path.startswith('/'):
        url_path = '/' + url_path

    # 检查同项目下 URL 冲突
    if MultiPage.objects.filter(project=project, url_path=url_path).exists():
        return JsonResponse({'error': f'URL 路径 {url_path} 已存在'}, status=400)

    page = MultiPage(
        project=project,
        name=name,
        url_path=url_path,
        title=body.get('title', name)[:500],
        description=body.get('description', '')[:500],
        keywords=body.get('keywords', '')[:500],
        html_content=html_content,
        nav_title=body.get('nav_title', name)[:100],
        sort_order=project.pages.count(),
    )
    page.save()

    return JsonResponse({'message': '页面已添加', 'page': page.to_dict()})


@require_POST
def api_multi_page_update_nav(request, project_id):
    """更新导航栏配置（拖拽排序后保存）。"""
    project = get_object_or_404(MultiPageProject, pk=project_id)
    body = json.loads(request.body)
    nav = body.get('nav_config', [])
    project.nav_config = nav
    project.save(update_fields=['nav_config', 'updated_time'])
    return JsonResponse({'message': '导航配置已更新'})


@require_GET
def api_multi_page_list_pages(request, project_id):
    """获取项目所有页面列表（JSON）。"""
    project = get_object_or_404(MultiPageProject, pk=project_id)
    pages = project.pages.all()
    return JsonResponse({
        'project': project.to_dict(),
        'pages': [p.to_dict() for p in pages],
    })


@require_GET
def api_multi_page_tree(request):
    """返回按根域名分组的树型数据：根域名 → 项目 → 页面。

    用于项目列表的树形视图。
    分组规则：
      - root_domain 非空的项目按 root_domain 分组
      - root_domain 为空的项目归入"未绑定域名"组
    """
    projects = (
        MultiPageProject.objects
        .select_related('created_by')
        .prefetch_related('pages')
        .all()
    )

    # 按 root_domain 分组
    groups = {}  # root_domain -> list[project_dict]
    unbound = []  # root_domain 为空的项目

    for proj in projects:
        pages_qs = proj.pages.all().order_by('sort_order', 'id')
        pages_list = []
        for p in pages_qs:
            pages_list.append({
                'id': p.id,
                'name': p.name,
                'url_path': p.url_path,
                'nav_title': p.nav_title or p.name,
                'title': p.title,
                'sort_order': p.sort_order,
            })

        proj_dict = {
            'id': proj.id,
            'name': proj.name,
            'root_domain': proj.root_domain,
            'theme': (proj.theme or '')[:120],
            'style': proj.style,
            'status': proj.status,
            'status_display': proj.get_status_display(),
            'is_enabled': proj.is_enabled,
            'enabled_domain': proj.enabled_domain,
            'total_pages': len(pages_list),
            'create_time': proj.create_time.isoformat() if proj.create_time else None,
            'detail_url': reverse('multi_page_project_detail', args=[proj.id]),
            'pages': pages_list,
        }

        root = (proj.root_domain or '').strip()
        if root:
            groups.setdefault(root, []).append(proj_dict)
        else:
            unbound.append(proj_dict)

    root_domains = []
    for root, projs in groups.items():
        total_pages = sum(p['total_pages'] for p in projs)
        root_domains.append({
            'root_domain': root,
            'project_count': len(projs),
            'page_count': total_pages,
            'projects': projs,
        })
    # 按项目数倒序
    root_domains.sort(key=lambda x: x['project_count'], reverse=True)

    return JsonResponse({
        'root_domains': root_domains,
        'unbound': {
            'project_count': len(unbound),
            'projects': unbound,
        },
        'total_projects': projects.count(),
        'total_bound': sum(g['project_count'] for g in root_domains),
        'total_unbound': len(unbound),
    })


# ---------------------------------------------------------------------------
# AI 生成配置
# ---------------------------------------------------------------------------

def multi_page_config_view(request):
    """多页面生成配置页面"""
    config = MultiPageConfig.get_config()
    return render(request, 'XiaoYingAdmin/页面管理/多页面管理/生成配置.html', {
        'config': config,
    })


@require_POST
def api_multi_page_config_save(request):
    """保存多页面生成配置"""
    body = json.loads(request.body)
    config = MultiPageConfig.get_config()

    # 只更新允许的字段
    allowed_fields = {
        'max_tokens', 'timeout',
        'max_pages', 'page_content_max_chars',
        'system_prompt', 'extra_config',
    }
    for key in allowed_fields:
        if key in body:
            setattr(config, key, body[key])

    config.save()
    return JsonResponse({
        'message': '配置已保存',
        'config': config.to_dict(),
    })


# ---------------------------------------------------------------------------
# 智能互链（项目间互链）
# ---------------------------------------------------------------------------

# 互链块标记（与单页面智能互链保持一致，便于统一替换）
_CROSSLINK_TAG_START = '<!-- ====== 智能互链 ====== -->'
_CROSSLINK_TAG_END = '<!-- ====== /智能互链 ====== -->'


def _mp_extract_root_domain(domain: str) -> str:
    """规范化域名为 https://域名/ 格式。

    - 移除 *. 通配符前缀
    - 统一小写
    - 移除 www. 前缀
    - 保留端口号（如 127.0.0.1:8001）
    """
    d = (domain or '').strip().lower()
    d = d.lstrip('*').lstrip('.')
    if d.startswith('www.'):
        d = d[4:]
    if not d:
        return ''
    # 统一为 https://域名/ 格式
    return f'https://{d}/'


def _mp_to_attr(s: str) -> str:
    """HTML 属性转义。"""
    return str(s).replace('&', '&amp;').replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')


def _mp_crosslink_items_html(partner_links):
    """生成 <a> 标签行（不含外层包装）。"""
    if not partner_links:
        return ''
    return '\n'.join(
        f'      <a href="{_mp_to_attr(p["url"])}" '
        f'title="{_mp_to_attr(p["title"])}" '
        f'rel="friend" target="_blank">{_mp_to_attr(p["title"])}</a>'
        for p in partner_links
    )


def _mp_crosslink_html_block(partner_links):
    """生成完整的互链 HTML 块（含 header + wrapper）。"""
    items_html = _mp_crosslink_items_html(partner_links)
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


def _mp_replace_crosslink_block(html, new_block_html):
    """替换 HTML 中的互链块。若无则末尾追加。

    优先在 </body> 之前插入（保证页面布局正常），
    找不到 </body> 时退化为末尾追加。
    """
    start_idx = html.find(_CROSSLINK_TAG_START)
    end_idx = html.find(_CROSSLINK_TAG_END)
    if start_idx != -1 and end_idx != -1:
        # 已有互链块 → 整体替换
        end_idx += len(_CROSSLINK_TAG_END)
        return html[:start_idx] + new_block_html + html[end_idx:]
    # 没有互链块 → 在 </body> 前插入
    body_close = html.rfind('</body>')
    if body_close != -1:
        return html[:body_close] + new_block_html + html[body_close:]
    return html + '\n' + new_block_html


@require_POST
def api_multi_page_crosslinks_generate(request):
    """全量生成多页面项目间智能互链。

    行为：
      1. 遍历所有「已启用 + 未排除」的多页面项目
      2. 每个项目以 enabled_domain（无则 root_domain）作为代表域名
      3. 为每个项目下所有页面的 html_content 追加/替换互链块
      4. 互链块包含其他项目的域名链接
      5. 幂等：重复调用结果一致
    """
    # 1. 获取所有参与互链的项目（已启用 + 未排除 + 已完成）
    qs = MultiPageProject.objects.filter(
        is_enabled=True,
        crosslink_excluded=False,
        status=MultiPageProject.Status.COMPLETED,
    )
    projects = list(qs)

    if len(projects) < 2:
        return JsonResponse({
            'message': '参与互链的项目不足 2 个，无需生成',
            'updated_count': 0,
            'new_link_count': 0,
            'total_projects': len(projects),
        })

    # 2. 为每个项目计算代表域名
    project_domains = {}  # project_id -> root_url
    for p in projects:
        domain = (p.enabled_domain or p.root_domain or '').strip()
        root_url = _mp_extract_root_domain(domain)
        if root_url:
            project_domains[p.id] = root_url

    # 3. 为每个项目下的所有页面追加/替换互链块
    updated_count = 0
    new_link_count = 0
    pages_updated = 0

    for proj in projects:
        own_url = project_domains.get(proj.id, '')
        if not own_url:
            continue

        # 构建合作链接（其他项目的域名，去重）
        seen = set()
        desired_links = []
        for other_proj in projects:
            if other_proj.id == proj.id:
                continue
            other_url = project_domains.get(other_proj.id, '')
            if not other_url or other_url in seen:
                continue
            seen.add(other_url)
            desired_links.append({
                'url': other_url,
                'title': other_proj.name,
            })

        if not desired_links:
            continue

        new_block = _mp_crosslink_html_block(desired_links)

        # 遍历该项目下的所有页面
        pages_qs = MultiPage.objects.filter(project=proj)
        for page in pages_qs:
            new_html = _mp_replace_crosslink_block(page.html_content, new_block)
            if new_html == page.html_content:
                continue  # 无变化
            page.html_content = new_html
            page.save(update_fields=['html_content', 'updated_time'])
            pages_updated += 1

        updated_count += 1
        new_link_count += len(desired_links)

    if updated_count == 0:
        return JsonResponse({
            'message': '所有项目互链已是最新，无需更新',
            'updated_count': 0,
            'new_link_count': 0,
            'total_projects': len(projects),
            'pages_updated': 0,
        })

    return JsonResponse({
        'message': f'智能互链完成，已更新 {updated_count} 个项目 / {pages_updated} 个页面，每页 {new_link_count} 条链接',
        'updated_count': updated_count,
        'new_link_count': new_link_count,
        'total_projects': len(projects),
        'pages_updated': pages_updated,
    })


@require_POST
def api_multi_page_crosslink_exclude_toggle(request, project_id):
    """切换项目的智能互链排除状态。

    请求体：{"excluded": true/false}
    """
    proj = get_object_or_404(MultiPageProject, pk=project_id)
    try:
        body = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        body = {}
    excluded = bool(body.get('excluded', not proj.crosslink_excluded))

    proj.crosslink_excluded = excluded
    proj.save(update_fields=['crosslink_excluded', 'updated_time'])

    return JsonResponse({
        'message': '已排除互链' if excluded else '已纳入互链',
        'crosslink_excluded': proj.crosslink_excluded,
    })

