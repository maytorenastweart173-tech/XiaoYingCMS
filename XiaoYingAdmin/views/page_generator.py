"""
页面生成服务 — 在独立线程中调用 DeepSeek AI 生成页面 HTML。

工作流程：
  1. start_generation(input_content, session_key)
     - 创建 PageGenerationTask 记录（status=pending, progress=0）
     - 启动 daemon 线程执行 _run_generation
     - 立即返回 task 对象（不等 AI 返回，避免阻塞 HTTP 请求）

  2. _run_generation(task_id) — 后台线程函数
     - 取出最新提示词
     - 调用 DeepSeek 官方 API（POST JSON，Bearer 鉴权）
     - 实时更新 task.progress / task.message
     - 成功 → 写 result_html，自动执行 SEO 优化，状态 completed
     - 失败 → 写 error_message，状态 failed

  3. get_task_progress(task_id) — 给前端轮询用

注意：
  - 线程内的 ORM 操作必须重新获取 task 实例；Django ORM 默认不允许跨线程共享对象
  - 线程开始时关闭旧的 DB 连接，结束时再关闭，让 Django 重新建立新连接
  - daemon=True 保证主进程退出时线程不会卡住
"""

import json
import threading
import urllib.request
import urllib.error

from django.db import connection

from XiaoYingAdmin.models.prompt import Prompt
from XiaoYingAdmin.models.task import PageGenerationTask
from XiaoYingAdmin.models.generated_page import GeneratedPage

# DeepSeek 官方 API（OpenAI 兼容格式）
DEEPSEEK_API_URL = 'https://api.deepseek.com/chat/completions'
DEEPSEEK_MODEL = 'deepseek-v4-pro'

# 单次请求总超时（秒）— AI 生成可能较慢（正常 40-60 秒，高峰更久）。
DEEPSEEK_TIMEOUT = 280

# 默认最大输出 token 数（DeepSeek v4 单次最多支持 32768）
DEEPSEEK_MAX_TOKENS = 32768

# 页面名称兜底提取时需要剥离的常见前缀 / 后缀
_NAME_PREFIXES = ('帮我生成一个', '帮我创建一个', '帮我做个', '生成一个', '创建一个', '做个', '生成', '一个')
_NAME_SUFFIXES = ('的页面', '页面', '的网页', '网页', '的网站', '网站')


def _get_deepseek_config() -> tuple:
    """
    读取网站设置中的 DeepSeek API Key 与思考模式开关。

    API Key 只从后台「网站设置」读取；未配置时抛出明确错误，
    避免使用任何硬编码密钥。
    """
    from XiaoYingAdmin.models.site_settings import SiteSettings

    config, _ = SiteSettings.objects.get_or_create(pk=1)
    api_key = (config.deepseek_api_key or '').strip()
    if not api_key:
        raise RuntimeError(
            '未配置 DeepSeek API Key，请前往「核心设置 → 网站设置 → DeepSeek AI 配置」填写后重试'
        )
    return api_key, bool(config.deepseek_thinking_enabled)


def _build_messages(content: str, system_prompt_json: str) -> list:
    """
    将系统提示词解析为 DeepSeek messages 列表。

    system_prompt_json 兼容三种形式：
      - JSON 数组：[{"role": "system", "content": "..."}]
      - JSON 对象：{"role": "system", "content": "..."}
      - 纯文本：直接作为 system 消息
    """
    messages = []
    parsed = None
    if system_prompt_json:
        try:
            parsed = json.loads(system_prompt_json)
        except (TypeError, json.JSONDecodeError):
            parsed = None

    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict) and item.get('content'):
                messages.append({
                    'role': item.get('role') or 'system',
                    'content': item['content'],
                })
            elif isinstance(item, str) and item.strip():
                messages.append({'role': 'system', 'content': item})
    elif isinstance(parsed, dict) and parsed.get('content'):
        messages.append({
            'role': parsed.get('role') or 'system',
            'content': parsed['content'],
        })
    elif parsed is None and isinstance(system_prompt_json, str) and system_prompt_json.strip():
        # 非 JSON 的纯文本提示词
        messages.append({'role': 'system', 'content': system_prompt_json.strip()})

    if not messages:
        messages.append({'role': 'system', 'content': '你是一个专业的前端工程师与 SEO 专家。'})

    messages.append({'role': 'user', 'content': content})
    return messages


def _call_deepseek(content: str, system_prompt_json: str, *,
                   max_tokens: int = None, timeout: int = None,
                   json_mode: bool = False) -> str:
    """
    调用 DeepSeek 官方 API（/chat/completions），返回回复正文。

    参数：
      content: 用户输入
      system_prompt_json: 系统提示词（JSON 数组 / JSON 对象 / 纯文本）
      max_tokens: 最大输出 token 数，默认 DEEPSEEK_MAX_TOKENS
      timeout: 请求超时秒数，默认 DEEPSEEK_TIMEOUT
      json_mode: 是否启用 JSON 输出模式（要求提示词中明确提到 JSON）

    抛出：
      RuntimeError: 网络错误、HTTP 非 2xx 或返回内容为空
    """
    api_key, thinking_enabled = _get_deepseek_config()

    payload = {
        'model': DEEPSEEK_MODEL,
        'messages': _build_messages(content, system_prompt_json),
        'stream': False,
        'thinking': {'type': 'enabled' if thinking_enabled else 'disabled'},
        'max_tokens': int(max_tokens or DEEPSEEK_MAX_TOKENS),
    }
    if thinking_enabled:
        payload['reasoning_effort'] = 'high'
    if json_mode:
        payload['response_format'] = {'type': 'json_object'}

    req = urllib.request.Request(
        DEEPSEEK_API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        },
        method='POST',
    )

    timeout = timeout or DEEPSEEK_TIMEOUT
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode('utf-8')
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', errors='replace')[:500]
        raise RuntimeError(f'DeepSeek HTTP {e.code}: {detail}')
    except urllib.error.URLError as e:
        raise RuntimeError(f'DeepSeek 连接失败: {e.reason}')
    except TimeoutError:
        # urlopen 读超时会抛 TimeoutError（socket.timeout）
        raise RuntimeError(f'DeepSeek 请求超时（{timeout} 秒），请稍后重试')

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise RuntimeError(f'DeepSeek 返回格式异常: {body[:200]}')

    if isinstance(data, dict) and data.get('error'):
        error = data['error']
        message = error.get('message') if isinstance(error, dict) else str(error)
        raise RuntimeError(f'DeepSeek 错误: {message}')

    choices = data.get('choices') if isinstance(data, dict) else None
    if choices:
        message = choices[0].get('message') or {}
        reply = message.get('content') or ''
        if reply:
            return reply

    raise RuntimeError('DeepSeek 返回内容为空')


def _generate_page_name(task) -> str:
    """调用 AI 从 input_content 中提取简短页面名称（2-6字），失败时用兜底名。"""
    raw = task.input_content.strip()
    for prefix in _NAME_PREFIXES:
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    for suffix in _NAME_SUFFIXES:
        if raw.endswith(suffix):
            raw = raw[:-len(suffix)]
            break
    raw = raw.strip()[:30]
    fallback = raw if raw else '未命名页面'
    try:
        name = _call_deepseek(
            f'从以下需求中提取简短页面名称（2-6个字），只返回名称不要任何其他文字：{task.input_content}',
            json.dumps([{'role': 'system', 'content': '只返回名称'}], ensure_ascii=False),
        )
        name = name.strip().strip('"\'').strip()[:50]
        if not name or len(name) > 20 or '?' in name or not any(ord(c) > 127 for c in name):
            name = fallback
    except RuntimeError:
        name = fallback
    return name


def _group_domains_by_prompt(task) -> list[dict]:
    """将域名配置展开为"一个域名一个组"的生成组列表。

    每个域名独立生成一个页面（无论提示词是否相同）。
    这样 3 个域名就会产生 3 次独立 AI 调用 → 3 个不同的 HTML 页面。

    每组格式：
      {'prompt_ids': [...], 'domains': [domain_str],
       'system_prompt_list': [...] | None}

    - prompt_ids = []：使用全部活跃提示词（默认）
    - prompt_ids = [1, 3, 5]：仅使用指定的提示词
    - 向后兼容旧版 prompt_id（单值）自动转为 [prompt_id]
    - system_prompt_list = None：由调用方在运行时填充默认值
    - task.domain_prompt_config 为空时向后兼容旧版 task.domain
    """
    config = task.domain_prompt_config or []
    if not config and task.domain:
        config = [{'domain': task.domain, 'prompt_id': None}]
    if not config:
        return []

    result = []
    for item in config:
        domain = (item.get('domain') or '').strip().lower()
        if not domain:
            continue
        # 解析 prompt_ids：支持新旧两种格式
        pids = item.get('prompt_ids')
        if not pids:
            pid = item.get('prompt_id')
            pids = [pid] if pid is not None else []
        else:
            pids = [int(pid) for pid in pids if pid is not None]

        group = {'prompt_ids': pids, 'domains': [domain], 'system_prompt_list': None}
        if pids:
            prompts = list(Prompt.objects.filter(id__in=pids, is_active=True).order_by('-version'))
            if prompts:
                group['system_prompt_list'] = [
                    {'role': 'system', 'content': p.content} for p in prompts
                ]
            # 未找到任何有效提示词 → system_prompt_list 保持 None → 回退默认
        result.append(group)
    return result


def _run_generation(task_id: str):
    """
    后台线程函数：执行 AI 页面生成并实时更新进度。

    支持多域名 + 多提示词分组：
      - 将 domain_prompt_config 按提示词分组
      - 每调用一次 AI 生成一个页面，绑定对应域名
      - 进度按组数平均分配
      - SEO 优化仅对最后生成的页面执行

    参数：
      task_id: PageGenerationTask.task_id (UUID 字符串)
    """
    # 线程内必须关闭继承的 DB 连接，让 Django 重新建立
    connection.close()
    try:
        task = PageGenerationTask.objects.get(task_id=task_id)

        # 中断检查
        if task.status == PageGenerationTask.STATUS_FAILED:
            return

        # ---- 1. 分析生成组（5%） ----
        task.update_progress(5, '正在分析域名与提示词配置...')
        groups = _group_domains_by_prompt(task)
        if not groups:
            groups = [{'prompt_id': None, 'domains': [], 'system_prompt_list': None}]
        total = len(groups)
        pages = []

        for idx, group in enumerate(groups):
            # 中断检查：每个组开始前
            task.refresh_from_db()
            if task.status == PageGenerationTask.STATUS_FAILED:
                for p in pages:
                    p.delete()
                return

            base_progress = idx * 100 // total
            next_progress = (idx + 1) * 100 // total
            range_size = next_progress - base_progress

            # 当前组标签（给进度消息用）
            group_label = f'第 {idx+1}/{total} 组' if total > 1 else ''

            # ---- 2. 加载提示词 ----
            if group['system_prompt_list']:
                prompt_list = group['system_prompt_list']
            else:
                prompt_list = Prompt.get_all_active_contents('page_generation')
            system_prompt_json = json.dumps(prompt_list, ensure_ascii=False)

            # 第一组的快照保留到 task（向后兼容）
            if idx == 0:
                task.prompt_snapshot = prompt_list[0]['content'] if prompt_list else ''
                task.save(update_fields=['prompt_snapshot', 'updated_time'])

            # ---- 3. 构造用户内容（域名注入） ----
            user_content = task.input_content
            group_domains = group['domains']
            if group_domains:
                domain_urls = [_normalize_domain_to_url(d) for d in group_domains]
                user_content = (
                    f'{user_content}\n\n'
                    f'【本次生成页面将绑定的域名】：{", ".join(domain_urls)}\n'
                    f'请在生成 HTML 时，将这些域名用于 SEO 相关的 meta 标签、'
                    f'canonical 链接、Open Graph url 等需要引用站点地址的位置。'
                )

            # ---- 4. 调用 AI ----
            msg = f'正在请求 AI 接口{"（" + group_label + "）" if group_label else ""}...'
            task.update_progress(base_progress + range_size // 3, msg)
            result = _call_deepseek(user_content, system_prompt_json)

            # 中断检查
            task.refresh_from_db()
            if task.status == PageGenerationTask.STATUS_FAILED:
                for p in pages:
                    p.delete()
                return

            progress_after_ai = base_progress + range_size * 2 // 3
            task.update_progress(progress_after_ai, '正在处理返回结果...')

            if not result or not result.strip():
                if idx == 0:
                    task.mark_failed('AI 返回内容为空')
                    return
                continue  # 非首组失败时跳过

            result = _strip_code_fence(result)
            result = _postprocess_html(result)

            # ---- 5. 生成页面名称 + 保存到库 ----
            page_name = _generate_page_name(task)
            try:
                page = GeneratedPage.objects.create(
                    name=page_name,
                    html_content=result,
                    task_id=task.task_id,
                    input_content=task.input_content,
                    created_by=task.created_by,
                    domains=group_domains,
                    domain=group_domains[0] if group_domains else None,
                )
                # 自动注入配置了「全部页面 / 所属分类」的自定义友情链接
                try:
                    from XiaoYingAdmin.views.friend_link import sync_friend_links_to_pages
                    sync_friend_links_to_pages(pages=[page])
                except Exception:
                    pass  # 友链注入失败不影响主流程
                pages.append(page)
            except Exception:
                pass  # 保存失败不影响主流程

        # 所有组处理完毕
        if not pages:
            task.mark_failed('所有分组均生成失败')
            return

        # ---- 6. SEO 优化（仅对最后生成的页面） ----
        last_page = pages[-1]
        task.refresh_from_db()
        if task.status == PageGenerationTask.STATUS_FAILED:
            for p in pages:
                p.delete()
            return

        task.update_progress(95, '正在进行 SEO 优化（可能需要 30-60 秒）...')
        try:
            optimized = seo_optimize_page(last_page)
            if optimized and optimized.strip():
                last_page.html_content = optimized
                last_page.save(update_fields=['html_content', 'updated_time'])
                # SEO 优化会重写 HTML，需重新注入自定义友情链接
                try:
                    from XiaoYingAdmin.views.friend_link import sync_friend_links_to_pages
                    sync_friend_links_to_pages(pages=[last_page])
                except Exception:
                    pass  # 友链注入失败不影响主流程
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f'SEO 优化失败 (task={task_id}): {e}')

        # 中断检查
        task.refresh_from_db()
        if task.status == PageGenerationTask.STATUS_FAILED:
            for p in pages:
                p.delete()
            return

        # 将最后页面的结果回写到 task（前端轮询用）
        task.result_html = last_page.html_content
        task.page_name = last_page.name
        task.save(update_fields=['result_html', 'page_name', 'updated_time'])

        task.update_progress(100, '生成完成')

    except PageGenerationTask.DoesNotExist:
        pass  # 任务记录被删除了，安静退出
    except RuntimeError as e:
        try:
            task = PageGenerationTask.objects.get(task_id=task_id)
            task.mark_failed(str(e))
        except PageGenerationTask.DoesNotExist:
            pass
    except Exception as e:
        try:
            task = PageGenerationTask.objects.get(task_id=task_id)
            task.mark_failed(f'系统错误: {type(e).__name__}: {e}')
        except PageGenerationTask.DoesNotExist:
            pass
    finally:
        connection.close()


def _strip_code_fence(text: str) -> str:
    """去掉 AI 返回中常见的 markdown 代码块包裹。"""
    text = text.strip()
    # 优先匹配 ```html\n（带换行）
    for prefix in ('```html\n', '```html\r\n', '```html', '```\n', '```\r\n', '```'):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    for suffix in ('\n```', '\r\n```', '```'):
        if text.endswith(suffix):
            text = text[:-len(suffix)]
            break
    return text.strip()


# 移动端安全兜底样式：防止横向滚动 + 媒体元素自适应 + 标题均衡换行
_MOBILE_SAFE_CSS = (
    'html,body{overflow-x:hidden;max-width:100%;}'
    'img,svg,video,canvas{max-width:100%;height:auto;}'
    'h1,h2,h3{text-wrap:balance;}'
)

# 内联 SVG favicon，避免 /favicon.ico 404
_FAVICON_LINK = (
    '<link rel="icon" href="data:image/svg+xml,'
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E"
    "%3Ccircle cx='50' cy='50' r='45' fill='%231a73e8'/%3E%3C/svg%3E\">"
)


def _postprocess_html(html: str) -> str:
    """
    对 AI 生成的 HTML 做确定性兜底处理（不依赖模型是否遵守提示词）：
      1. 注入 overflow-x:hidden / 媒体自适应 / 标题均衡换行
      2. 缺少 favicon 时补一个内联 SVG favicon
    """
    if not html:
        return html
    if 'overflow-x' not in html:
        if '</style>' in html:
            html = html.replace('</style>', _MOBILE_SAFE_CSS + '</style>', 1)
        elif '</head>' in html:
            html = html.replace('</head>', f'<style>{_MOBILE_SAFE_CSS}</style></head>', 1)
    if 'rel="icon"' not in html and "rel='icon'" not in html:
        html = html.replace('</head>', _FAVICON_LINK + '</head>', 1)
    return html


def _normalize_domain_to_url(domain: str) -> str:
    """把用户提供的域名标准化为完整站点 URL（带协议前缀）。

    规则：
      - 已带 http:// / https:// 的直接返回
      - 本地地址（localhost / 127.* / 0.0.0.0）使用 http://
      - 其他域名统一使用 https://

    生成阶段和 SEO 优化阶段共用，避免协议前缀判断逻辑重复。
    """
    if not domain:
        return ''
    domain = domain.strip().lower()
    if not domain:
        return ''
    if domain.startswith(('http://', 'https://')):
        return domain
    # 取 host 部分（去掉端口）判断是否本地地址
    host = domain.split(':')[0]
    if host in ('localhost', '127.0.0.1', '0.0.0.0') or host.startswith('127.'):
        return 'http://' + domain
    return 'https://' + domain


def _get_page_site_url(page) -> str:
    """从 GeneratedPage 上读取绑定域名并标准化为完整站点 URL。

    优先取 domains[0]（新字段），回退到 domain（旧字段兼容）。
    未绑定域名时返回空串。
    """
    raw_domain = ''
    if page.domains:
        raw_domain = page.domains[0] if isinstance(page.domains, list) and page.domains else ''
    if not raw_domain and page.domain:
        raw_domain = page.domain
    return _normalize_domain_to_url(raw_domain)


def start_generation(input_content: str, session_key: str = '',
                     created_by=None, domain: str = '',
                     domain_prompt_config: list | None = None) -> PageGenerationTask:
    """
    启动一次页面生成任务（主线程调用，立即返回）。

    参数：
      input_content: 用户输入的页面描述
      session_key: 当前 session_key，用于跨请求查询
      created_by: 发起任务的用户对象（User 实例或 None）
      domain: 用户指定的绑定域名（可选），生成完成后自动绑定
      domain_prompt_config: 多域名配置，格式：
        [{"domain": "...", "prompt_id": 1 | null}, ...]
        每个域名可指定使用的提示词 ID，null 表示默认活跃提示词

    返回：
      新创建的 PageGenerationTask 实例
    """
    task = PageGenerationTask.objects.create(
        input_content=input_content,
        session_key=session_key,
        status=PageGenerationTask.STATUS_PENDING,
        progress=0,
        message='任务已创建，等待执行...',
        created_by=created_by,
        domain=domain,
        domain_prompt_config=domain_prompt_config or [],
    )

    thread = threading.Thread(
        target=_run_generation,
        args=(str(task.task_id),),
        daemon=True,
        name=f'page-gen-{task.task_id}',
    )
    thread.start()

    return task


def get_task_progress(task_id: str) -> dict | None:
    """
    查询任务进度（前端轮询用）。

    返回：
      dict: {task_id, status, progress, message, result_html?, error_message?}
      None: 任务不存在
    """
    try:
        task = PageGenerationTask.objects.get(task_id=task_id)
    except (PageGenerationTask.DoesNotExist, ValueError):
        return None

    data = {
        'task_id': str(task.task_id),
        'status': task.status,
        'status_display': task.get_status_display(),
        'progress': task.progress,
        'message': task.message,
        'input_content': task.input_content,
        'domain': task.domain,
        'domain_prompt_config': task.domain_prompt_config if task.domain_prompt_config else [],
    }
    if task.status == PageGenerationTask.STATUS_COMPLETED:
        data['result_html'] = task.result_html
        data['page_name'] = task.page_name
    if task.status == PageGenerationTask.STATUS_FAILED:
        data['error_message'] = task.error_message
    return data


def seo_optimize_page(page) -> str:
    """
    对已保存页面进行 SEO 优化（同步调用，可能耗时 30-60 秒）。

    参数：
      page: GeneratedPage 实例

    返回：
      优化后的 HTML 字符串

    抛出：
      RuntimeError: AI 调用失败或返回空
    """
    system_prompt_list = Prompt.get_active_for_categories(['page_seo_optimization'])
    system_prompt_json = json.dumps(system_prompt_list, ensure_ascii=False)

    user_content = _build_seo_input(page)

    result = _call_deepseek(user_content, system_prompt_json)
    if not result or not result.strip():
        raise RuntimeError('AI 返回内容为空')

    result = _strip_code_fence(result)
    result = _postprocess_html(result)
    return result


def _build_seo_input(page) -> str:
    """构造 SEO 优化的用户输入内容（需求描述 + 当前 HTML + 绑定域名）。

    关键：必须把用户绑定的域名注入进去，并明确强制 AI 在所有 URL 相关
    SEO 标签（canonical、og:url 等）中严格使用该域名，否则 AI 会沿用
    原 HTML 中的示例域名或自行编造（如 web.whatsapp.com）。
    """
    parts = [
        f'用户原始需求描述：\n'
        f'{page.input_content or "（无需求描述）"}\n\n'
        f'当前生成的 HTML 代码：\n'
        f'```html\n{page.html_content}\n```'
    ]
    # 注入用户绑定的域名，强制 AI 在所有 URL 相关 SEO 标签中严格使用该域名
    site_url = _get_page_site_url(page)
    if site_url:
        parts.append(
            f'\n\n【重要 - 绑定域名】：该页面将绑定到域名 {site_url}\n'
            f'在进行 SEO 优化时，必须严格遵守以下规则：\n'
            f'1. <link rel="canonical" href="{site_url}"> 必须严格使用该域名\n'
            f'2. <meta property="og:url" content="{site_url}"> 必须严格使用该域名\n'
            f'3. 任何需要引用页面规范地址或站点根的位置（如 og:image 等绝对 URL '
            f'路径前缀、Schema.org 中 url 字段）都必须使用 {site_url}，'
            f'不得使用 web.whatsapp.com 等其他示例域名\n'
            f'4. 不得擅自更改或编造域名，必须严格按照上述域名填写\n'
            f'5. 如果原 HTML 中已存在其他域名的 canonical / og:url，必须替换为 {site_url}'
        )
    # 通用规则：返回纯净 HTML，不包含任何注释
    parts.append(
        f'\n\n【重要 - 代码规范】：\n'
        f'1. 不要在 HTML 中写任何注释（<!-- ... -->），包括 SEO 标签说明、结构说明、'
        f'代码段标记等所有注释，返回纯净的 HTML 代码\n'
        f'2. 如果原 HTML 中已存在注释，必须全部删除'
    )
    return ''.join(parts)


def start_seo_optimization(page_id: int, created_by=None) -> PageGenerationTask:
    """
    启动一次 SEO 优化任务（异步，立即返回）。

    参数：
      page_id: GeneratedPage 的 ID
      created_by: 发起任务的用户对象

    返回：
      新创建的 PageGenerationTask 实例
    """
    task = PageGenerationTask.objects.create(
        input_content=f'SEO 优化 (page_id={page_id})',
        status=PageGenerationTask.STATUS_PENDING,
        progress=0,
        message='正在准备 SEO 优化...',
        created_by=created_by,
    )

    thread = threading.Thread(
        target=_run_seo_optimization,
        args=(str(task.task_id), page_id),
        daemon=True,
        name=f'seo-opt-{task.task_id}',
    )
    thread.start()

    return task


def _run_seo_optimization(task_id: str, page_id: int):
    """
    后台线程函数：执行一次 SEO 优化并实时更新进度。

    参数：
      task_id: PageGenerationTask.task_id (UUID 字符串)
      page_id: GeneratedPage 的 ID
    """
    connection.close()
    try:
        task = PageGenerationTask.objects.get(task_id=task_id)
        from XiaoYingAdmin.models.generated_page import GeneratedPage

        # 1. 加载页面（10%）
        task.update_progress(10, '正在加载页面数据...')
        try:
            page = GeneratedPage.objects.get(id=page_id)
        except GeneratedPage.DoesNotExist:
            task.mark_failed('页面不存在')
            return

        # 2. 加载提示词（20%）
        task.update_progress(20, '正在加载 SEO 优化提示词...')
        system_prompt_list = Prompt.get_active_for_categories(['page_seo_optimization'])
        system_prompt_json = json.dumps(system_prompt_list, ensure_ascii=False)
        task.prompt_snapshot = system_prompt_list[0]['content'] if system_prompt_list else ''
        task.save(update_fields=['prompt_snapshot', 'updated_time'])

        # 3. 调用 AI（30% → 80%）
        task.update_progress(30, '正在请求 AI 进行 SEO 优化（可能需要 30-60 秒）...')
        user_content = _build_seo_input(page)

        result = _call_deepseek(user_content, system_prompt_json)
        task.update_progress(85, '正在处理返回结果...')

        if not result or not result.strip():
            task.mark_failed('AI 返回内容为空')
            return

        result = _strip_code_fence(result)

        # 4. 保存结果到 task（90%）
        task.result_html = result
        task.save(update_fields=['result_html', 'updated_time'])
        task.update_progress(90, '正在保存优化结果...')

        # 5. 更新页面 HTML（95%）
        page.html_content = result
        page.save(update_fields=['html_content', 'updated_time'])
        task.update_progress(100, 'SEO 优化完成')

    except PageGenerationTask.DoesNotExist:
        pass
    except RuntimeError as e:
        try:
            task = PageGenerationTask.objects.get(task_id=task_id)
            task.mark_failed(str(e))
        except PageGenerationTask.DoesNotExist:
            pass
    except Exception as e:
        try:
            task = PageGenerationTask.objects.get(task_id=task_id)
            task.mark_failed(f'系统错误: {type(e).__name__}: {e}')
        except PageGenerationTask.DoesNotExist:
            pass
    finally:
        connection.close()
