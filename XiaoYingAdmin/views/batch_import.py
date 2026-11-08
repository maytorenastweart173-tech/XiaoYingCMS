"""
批量导入单页面功能。

流程:
  1. 用户提供批量域名（一行一个）
  2. 自动检测/添加 https:// 协议
  3. 用户上传 zip 文件（内含多个子文件夹，每个子文件夹含一个 .html 文件）
  4. 智能随机分配 .html 给每个域名（不重复）
  5. 自动保存到指定分类下
  6. 上传的文件仅临时提取，不持久保存

当 .html 数量不足时，支持三种处理模式（mode 参数）：
  - 空/未传：返回 need_choice，由前端弹出选择对话框
  - 'abort'       ：放弃导入，让用户补齐后重试
  - 'duplicate'   ：允许重复，先全部分配 .html，剩余域名随机复用
  - 'ai_generate' ：剩余域名通过 AI 自动生成页面，绑定域名和分类
"""
import concurrent.futures
import json
import os
import random
import tempfile
import uuid
import zipfile

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from XiaoYingAdmin.common.http import parse_json_body
from XiaoYingAdmin.middleware.operation_log import log_operation
from XiaoYingAdmin.models.generated_page import GeneratedPage
from XiaoYingAdmin.models.page_category import PageCategory


def _normalize_domain(raw: str):
    """智能检测协议，若缺失则默认加 https://"""
    d = raw.strip()
    if not d:
        return None
    if d.startswith('http://') or d.startswith('https://'):
        return d
    return 'https://' + d


def _collect_html_files(main_path: str) -> list[str]:
    """
    扫描主文件夹，在每个子文件夹中收集 .html 文件路径。
    若某子文件夹下有多个 .html 文件，则随机选一个。
    仅扫描第一层子目录（不递归更深层级）。
    """
    if not os.path.isdir(main_path):
        raise FileNotFoundError(f'文件夹路径不存在: {main_path}')

    html_files = []
    seen = set()

    for entry in os.scandir(main_path):
        if not entry.is_dir():
            continue
        real = os.path.realpath(entry.path)
        if real in seen:
            continue
        seen.add(real)

        candidates = []
        try:
            for child in os.scandir(entry.path):
                if child.is_file() and child.name.lower().endswith('.html'):
                    candidates.append(child.path)
        except PermissionError:
            continue

        if candidates:
            html_files.append(random.choice(candidates))

    return html_files


def _extract_html_from_zip(file_obj) -> list[dict]:
    """
    从上传的 zip 文件中提取 .html 文件内容（每个子文件夹选一个）。
    临时解压到临时目录，提取完即删除，不持久保存。

    返回: [{'content': '...', 'name': '...', 'source': '...'}, ...]
    """
    results = []
    with tempfile.TemporaryDirectory() as tmpdir:
        # 解压到临时目录
        with zipfile.ZipFile(file_obj, 'r') as zf:
            zf.extractall(tmpdir)

        # 扫描临时目录的所有子文件夹
        for entry in os.scandir(tmpdir):
            if not entry.is_dir():
                continue
            candidates = []
            try:
                for child in os.scandir(entry.path):
                    if child.is_file() and child.name.lower().endswith('.html'):
                        candidates.append(child.path)
            except PermissionError:
                continue

            if candidates:
                chosen = random.choice(candidates)
                with open(chosen, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
                basename = os.path.splitext(os.path.basename(chosen))[0]
                subdir_name = os.path.basename(entry.path)
                results.append({
                    'content': content,
                    'name': basename,
                    'source': f'{subdir_name}/{os.path.basename(chosen)}',
                })

    return results


def _ai_generate_page_for_domain(domain: str, domain_display: str, cats, request) -> dict:
    """
    通过 AI 为单个域名生成页面（同步调用，可能耗时 30-120 秒）。

    参数：
      domain:         标准化后的域名（含 https://）
      domain_display: 用于展示的域名（不含协议前缀）
      cats:           PageCategory queryset，可为 None
      request:        当前 HTTP 请求（用于日志记录）

    返回：
      {'ok': True, 'page': {...}}  成功
      {'ok': False, 'error': '...'} 失败
    """
    # 延迟导入，避免循环依赖
    from XiaoYingAdmin.views.page_generator import _call_deepseek, _strip_code_fence
    from XiaoYingAdmin.models.prompt import Prompt

    # 构造用户内容 —— 以域名主题生成着陆页
    user_content = (
        f'请为域名 {domain_display} 生成一个完整的着陆页（Landing Page）。\n'
        f'要求：\n'
        f'1. 页面内容与域名主题相关\n'
        f'2. SEO 友好，包含完整的 meta 标签\n'
        f'3. 响应式设计，适配桌面和移动端\n'
        f'4. 使用中文文案\n'
        f'5. 只返回完整的 HTML 代码，不要加任何说明\n\n'
        f'【本次生成页面将绑定的域名】：{domain}\n'
        f'请在生成 HTML 时，将该域名用于 SEO 相关的 meta 标签、canonical 链接、'
        f'Open Graph url 等需要引用站点地址的位置。'
    )

    # 获取活跃的页面生成提示词
    system_prompt_list = Prompt.get_all_active_contents('page_generation')
    system_prompt_json = json.dumps(system_prompt_list, ensure_ascii=False)

    try:
        result = _call_deepseek(user_content, system_prompt_json)
    except RuntimeError as e:
        return {'ok': False, 'error': f'AI 调用失败: {e}'}
    except Exception as e:
        return {'ok': False, 'error': f'AI 调用异常: {type(e).__name__}: {e}'}

    if not result or not result.strip():
        return {'ok': False, 'error': 'AI 返回内容为空'}

    result = _strip_code_fence(result)

    # 从域名提取简短页面名称（取第一段，如 asdf.sghf-whatsapp.hl.cn → asdf）
    host = domain_display.split(':')[0]
    parts = host.split('.')
    page_name = (parts[0] if parts else domain_display)[:128] or 'AI生成页面'

    page = GeneratedPage(
        name=page_name,
        html_content=result,
        input_content=f'批量导入 AI 生成 — {domain_display}',
        domains=[domain_display],
        task_id=uuid.uuid4(),
        created_by=request.user if request.user.is_authenticated else None,
    )
    page.save()

    if cats and cats.exists():
        page.categories.set(cats)

    log_operation(
        request, 'batch_import_ai', 'GeneratedPage', page.id,
        f'批量导入 AI 生成页面「{page_name}」→ {domain_display}',
        detail={
            'changes': {
                '页面名称': {'new': page_name},
                '域名': {'new': domain_display},
                '来源': {'new': 'AI 生成'},
            }
        },
    )

    return {'ok': True, 'page': {
        'id': page.id,
        'name': page_name,
        'domain': domain,
        'source': 'AI 生成',
    }}


@csrf_exempt
@require_POST
def api_batch_import_pages(request):
    """
    POST /xiaoying_admin/api/pages/saved/batch-import/

    支持两种请求方式：

    方式一（推荐）：multipart/form-data 文件上传
    - domains: 域名文本（每行一个）
    - file: zip 文件（内含子文件夹，每个子文件夹一个 .html）
    - category_ids: 可选，分类 ID（逗号分隔的字符串）

    方式二（兼容）：application/json
    {
        "domains": ["..."],
        "folder_path": "/path/to/folder",
        "category_ids": [1, 2]
    }

    返回:
    {
        "ok": true,
        "message": "成功导入 N 个页面",
        "created": [...],
        "errors": [...]
    }
    """
    # ---------- 判断请求类型 ----------
    is_upload = request.content_type and 'multipart' in request.content_type

    raw_domains = []
    folder_path = ''
    category_ids = []
    uploaded_file = None
    mode = ''  # '' | 'duplicate' | 'ai_generate'

    if is_upload:
        # multipart/form-data 上传模式
        domains_text = (request.POST.get('domains') or '').strip()
        if domains_text:
            raw_domains = [d.strip() for d in domains_text.split('\n') if d.strip()]
        else:
            raw_domains = []

        cat_ids_str = (request.POST.get('category_ids') or '').strip()
        if cat_ids_str:
            category_ids = [int(x) for x in cat_ids_str.split(',') if x.strip().isdigit()]

        uploaded_file = request.FILES.get('file')
        mode = (request.POST.get('mode') or '').strip()
    else:
        # JSON 模式（兼容老方式）
        body, error = parse_json_body(request)
        if error is not None:
            return error
        raw_domains = body.get('domains', [])
        folder_path = (body.get('folder_path') or '').strip()
        category_ids = body.get('category_ids', [])
        mode = (body.get('mode') or '').strip()

        # JSON 模式校验
        if not raw_domains:
            return JsonResponse({'error': '请提供域名列表'}, status=400)
        if not folder_path:
            return JsonResponse({'error': '请提供主文件夹路径（folder_path），或使用文件上传模式'}, status=400)

    # ---------- 校验 ----------
    if not raw_domains:
        return JsonResponse({'error': '请提供域名列表'}, status=400)

    if is_upload and not uploaded_file:
        return JsonResponse({'error': '请上传 zip 压缩包文件'}, status=400)

    if uploaded_file:
        fname = getattr(uploaded_file, 'name', '') or ''
        if not fname.lower().endswith('.zip'):
            return JsonResponse({'error': '只支持 .zip 格式的压缩包'}, status=400)

    # ---------- 1. 标准化域名 ----------
    normalized = []
    for d in raw_domains:
        nd = _normalize_domain(d)
        if nd:
            normalized.append(nd)

    if not normalized:
        return JsonResponse({'error': '没有有效的域名'}, status=400)

    # ---------- 2. 获取 HTML 内容 ----------
    html_list = []  # [{'content': '...', 'name': '...', 'source': '...'}, ...]

    if uploaded_file:
        # 从上传的 zip 中提取
        try:
            html_list = _extract_html_from_zip(uploaded_file)
        except zipfile.BadZipFile:
            return JsonResponse({'error': '上传的文件不是有效的 zip 压缩包'}, status=400)
        except Exception as e:
            return JsonResponse({'error': f'解析 zip 文件失败: {e}'}, status=400)

        if not html_list:
            return JsonResponse({
                'error': '压缩包中未找到任何 .html 文件（请确认每个子文件夹内含一个 .html）'
            }, status=400)
    else:
        # 从服务器路径读取（兼容模式）
        try:
            html_paths = _collect_html_files(folder_path)
        except FileNotFoundError as e:
            return JsonResponse({'error': str(e)}, status=400)
        except PermissionError as e:
            return JsonResponse({'error': f'无权限读取文件夹: {e}'}, status=400)

        if not html_paths:
            return JsonResponse({'error': f'在「{folder_path}」中未找到任何 .html 文件'}, status=400)

        for p in html_paths:
            try:
                with open(p, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
                basename = os.path.splitext(os.path.basename(p))[0]
                html_list.append({
                    'content': content,
                    'name': basename,
                    'source': p,
                })
            except Exception as e:
                continue

    domain_count = len(normalized)
    html_count = len(html_list)

    # ---------- 3. 分配策略 ----------
    # 当 .html 不足时根据 mode 决定行为：
    #   无 mode      → 返回 need_choice 让前端弹窗
    #   duplicate    → 先全部分配 .html，剩余域名随机复用
    #   ai_generate  → 先全部分配 .html，剩余域名走 AI 生成
    ai_domains = []  # 需要 AI 生成的域名列表

    if html_count < domain_count:
        if not mode:
            # 未指定模式 → 让前端弹窗让用户选择
            return JsonResponse({
                'need_choice': True,
                'domain_count': domain_count,
                'html_count': html_count,
                'message': (
                    f'.html 文件数量不足。需要 {domain_count} 个（域名数），'
                    f'实际提取到 {html_count} 个。请选择处理方式：'
                ),
            })

        if mode == 'duplicate':
            # 允许重复：先全部分配 .html，剩余域名随机复用
            random.shuffle(html_list)
            assignments = []
            for i in range(domain_count):
                if i < html_count:
                    assignments.append((normalized[i], html_list[i]))
                else:
                    assignments.append((normalized[i], random.choice(html_list)))
        elif mode == 'ai_generate':
            # AI 生成：先全部分配 .html，剩余域名走 AI
            random.shuffle(html_list)
            assignments = list(zip(normalized[:html_count], html_list))
            ai_domains = normalized[html_count:]
        else:
            return JsonResponse({'error': f'未知的处理模式: {mode}'}, status=400)
    else:
        # 正常情况：html 数量充足，随机分配
        random.shuffle(html_list)
        assignments = list(zip(normalized, html_list[:domain_count]))

    # ---------- 4. 批量创建页面 ----------
    created_pages = []
    errors = []

    cats = None
    if category_ids:
        cats = PageCategory.objects.filter(id__in=[c for c in category_ids if c and isinstance(c, int)])

    for domain, html_item in assignments:
        html_content = html_item['content']
        if not html_content.strip():
            errors.append({'domain': domain, 'error': 'HTML 内容为空'})
            continue

        page_name = html_item['name'][:128]  # 截断到模型最大长度
        domain_display = domain.replace('https://', '').replace('http://', '')

        page = GeneratedPage(
            name=page_name,
            html_content=html_content,
            input_content=f'批量导入 — {domain_display}',
            domains=[domain_display],
            task_id=uuid.uuid4(),
            created_by=request.user if request.user.is_authenticated else None,
        )
        page.save()

        if cats and cats.exists():
            page.categories.set(cats)

        log_operation(
            request, 'batch_import', 'GeneratedPage', page.id,
            f'批量导入页面「{page_name}」→ {domain_display}',
            detail={
                'changes': {
                    '页面名称': {'new': page_name},
                    '域名': {'new': domain_display},
                    '来源': {'new': html_item['source']},
                    '分类IDs': {'new': category_ids},
                }
            },
        )

        created_pages.append({
            'id': page.id,
            'name': page_name,
            'domain': domain,
            'source': html_item['source'],
        })

    # ---------- 5. AI 生成剩余域名页面（仅 mode=ai_generate 时执行） ----------
    # 使用线程池并行调用 AI，减少总等待时间（N×120s → ~120s）
    if ai_domains:
        ai_futures = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(5, len(ai_domains))) as executor:
            for domain in ai_domains:
                domain_display = domain.replace('https://', '').replace('http://', '')
                fut = executor.submit(_ai_generate_page_for_domain, domain, domain_display, cats, request)
                ai_futures.append((domain, fut))

        for domain, fut in ai_futures:
            try:
                result = fut.result(timeout=180)  # 单个 AI 调用最多等 180 秒
                if result['ok']:
                    created_pages.append(result['page'])
                else:
                    errors.append({'domain': domain, 'error': result['error']})
            except concurrent.futures.TimeoutError:
                errors.append({'domain': domain, 'error': 'AI 生成超时（超过 180 秒）'})
            except Exception as e:
                errors.append({'domain': domain, 'error': f'AI 生成异常: {type(e).__name__}: {e}'})

    return JsonResponse({
        'ok': True,
        'message': f'成功导入 {len(created_pages)} 个页面',
        'created': created_pages,
        'errors': errors,
    })
