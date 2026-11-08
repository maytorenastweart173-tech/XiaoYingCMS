"""
多页面生成服务 — 后台线程中调用 DeepSeek AI 生成完整多页面站点。

工作流程：
  1. start_multi_page_generation(project_id)
     - 从 DB 读取 MultiPageProject
     - 构造 system prompt（含主题/风格/域名）
     - 启动 daemon 线程执行 _run_multi_generation
     - 立即返回 task_id

  2. _run_multi_generation(project_id) — 后台线程
     - 调用 DeepSeek API
     - 解析返回的 JSON
     - 创建 nav_config 和所有 MultiPage 记录
     - 更新项目状态

  3. get_multi_gen_progress(project_id, task_id) — 前端轮询
"""

import json
import re
import threading
import logging
import traceback

from django.db import connection

from XiaoYingAdmin.views.page_generator import _call_deepseek, _postprocess_html

logger = logging.getLogger('XiaoYingAdmin.multi_page_generator')

# ── 内置默认值（仅在配置表无数据时使用） ──
_DEFAULT_TIMEOUT = 300
_DEFAULT_MAX_TOKENS = 32768
_DEFAULT_MAX_PAGES = 4
_DEFAULT_PAGE_CONTENT_MAX_CHARS = 8000

# 兜底导航栏 CSS：基于 nav / nav a 标签选择器（不依赖类名），
# 在 _rebuild_nav_in_html 重建 nav 后注入到 <style> 最前面，
# 确保即使 AI 生成的 CSS 用了 .nav-links a 等类选择器（与重建后的
# <nav><a> 结构不匹配），导航栏仍有基础样式。
# color:inherit 让链接继承 header 颜色，适配深色/浅色背景
# （<a> 默认蓝色会被覆盖）。
# 原有页面 CSS 可覆盖这些兜底样式（相同优先级时后者生效）。
_PUBLIC_NAV_CSS = (
    'nav{display:flex;gap:4px;flex-wrap:wrap;align-items:center;'
    'justify-content:center;max-width:1200px;margin:0 auto;padding:0 20px}'
    'nav a{display:block;padding:12px 16px;color:inherit;text-decoration:none;'
    'font-size:15px;font-weight:500;transition:all .2s;white-space:nowrap;border-radius:4px}'
    'nav a:hover{opacity:.7}'
)

_DEFAULT_SYSTEM_PROMPT = """你是一个专业的前端工程师与 SEO 专家。
请根据以下信息生成一个完整的多页面企业/产品网站。

【根域名】{root_domain}
【主题】{theme}
【风格】{style}

## 输出要求
- 返回 **纯 JSON**，不要包含任何 markdown 代码包裹（不要 ```json），不要任何额外文字说明
- ⚠️ html_content 中的双引号 `"` 请使用 &quot; 代替，不要使用 \" 转义，也不要使用原始 "
- ⚠️ html_content 中的 & 符号请使用 &amp; 代替
- ⚠️ html_content 必须是一个不换行的单行字符串（不要在里面加换行），否则 JSON 解析会失败
- 不要在 html 中使用模板语法（如 {{ }} 或 {% %}）
- JSON 必须语法正确，不要截断或省略

## 网站结构要求
1. 确定网站导航结构（**{max_pages} 个核心页面**），每个页面 URL 路径使用语义化名称
2. 为每个页面生成完整的、独立的 HTML 文件（<!DOCTYPE html> 开头，</html> 结尾）
3. 所有页面必须风格统一、样式一致
4. 导航栏在每页完全相同，使用 <nav> 标签
5. Footer 在每页完全相同，使用 <footer> 标签
6. 每页 html_content 请精简，省略不必要的重复 CSS，使用极简类名，控制在 {page_content_max_chars} 字符以内
7. SEO 要求：
   - 每个页面的 <title> 必须包含关键词，格式为 "页面关键描述 | 网站名称"
   - <meta name="description"> 写一段吸引人的描述（80-160 字）
   - <meta name="keywords"> 包含 5-10 个相关关键词
   - 使用语义化 HTML5 标签（<header>, <main>, <section>, <article> 等）
   - 合理的 heading 层级（h1 → h2 → h3）
8. 整体设计规范：
   - 使用现代的 CSS（Flexbox/Grid，不要用 LayUI）
   - 响应式设计（桌面 + 移动端）
   - 配色协调，符合品牌调性
   - 每个页面内容充实，图文并茂
   - 包含 CTA（Call to Action）按钮
   - ⚠️ 所有 CSS 样式必须内嵌在 <style> 标签中，放在每个页面的 <head> 内
   - ⚠️ 不允许使用 <link rel="stylesheet"> 引用外部 CSS 文件
   - ⚠️ 每个页面必须独立包含完整样式，确保单独访问时样式完全正常

## JSON 格式（严格按照以下结构，不要添加额外字段）
{{"navigation": [{{"title": "首页", "url_path": "/index.html"}}], "pages": [{{"name": "首页", "url_path": "/index.html", "nav_title": "首页", "title": "页面标题 | 网站名称", "description": "SEO 描述", "keywords": "关键词", "html_content": "<!DOCTYPE html><html>..."}}]}}"""

# 内存中的进度缓存 — 避免频繁查 DB
_gen_progress: dict = {}


def _strip_code_fence(text: str) -> str:
    t = text.strip()
    for fence in ('```json', '```html', '```'):
        if t.startswith(fence):
            t = t[len(fence):]
    if t.endswith('```'):
        t = t[:-3]
    return t.strip()


def _is_complete_json(text: str) -> bool:
    """快速检测文本是否为完整 JSON（能成功 parse 即视为完整）。"""
    cleaned = _strip_code_fence(text)
    start = cleaned.find('{')
    end = cleaned.rfind('}')
    if start < 0 or end <= start:
        return False
    try:
        json.loads(cleaned[start:end + 1])
        return True
    except json.JSONDecodeError:
        return False


def _safe_parse_json(text: str) -> dict:
    """安全解析 JSON，失败时尝试修复后解析。返回 dict 或 None。"""
    cleaned = _strip_code_fence(text)
    start = cleaned.find('{')
    end = cleaned.rfind('}')
    if start >= 0 and end > start:
        try:
            return json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            pass
    # 尝试修复
    repaired = _try_repair_json(text)
    start2 = repaired.find('{')
    end2 = repaired.rfind('}')
    if start2 >= 0 and end2 > start2:
        try:
            return json.loads(repaired[start2:end2 + 1])
        except json.JSONDecodeError:
            pass
    # 最后尝试 strict=False
    try:
        return json.loads(cleaned[start:end + 1], strict=False)
    except (json.JSONDecodeError, Exception):
        return None


def _call_with_continuation(
    content: str,
    system_prompt_json: str,
    project_id: int = None,
    max_continuations: int = 3,
) -> str:
    """调用 AI API，支持分页追加生成以处理长输出截断问题。

    采用「分页追加」策略（已验证稳定可靠）：
      - 第一次调用让 AI 自由生成完整 JSON，_strip_code_fence 处理 markdown 包裹
      - 若首次输出页数不足 max_pages 或解析失败，提取已完成页面，
        再调 AI 生成剩余页面，按 url_path 去重合并

    ⚠️ 不使用 prefix 引导和 stop 参数的原因：
      - prefix_content 会改变 AI 输出行为，可能导致 navigation/pages 不完整
      - stop='```' 会在 HTML 内容中的代码块处提前终止，导致 JSON 截断
        （技术主题页面常含 ``` 代码示例）
      - system_prompt 已明确要求"返回纯 JSON，不要 markdown 包裹"，
        _strip_code_fence 也能兜底处理 markdown 包裹

    流程：
      1. 第一次调用 AI（不带 prefix/stop），尝试解析
      2. 若解析失败（截断），用 _try_repair_json 修复并提取已完成的页面
      3. 若已完成页面数 < max_pages，进入追加生成：
         a. 告知 AI 已有哪些页面（含 url_path 列表），请生成剩余的
         b. 合并 pages 和 navigation（按 url_path 去重）
      4. 最多追加 max_continuations 次

    参数：
      content: 用户输入
      system_prompt_json: 系统提示词 JSON
      project_id: 项目 ID（用于更新进度提示，可选）
      max_continuations: 最大追加次数（默认 3，应对较多截断场景）
    """
    cfg = _get_config_dict()
    target_pages = cfg['max_pages']

    # 第一次调用（不使用 prefix/stop，避免副作用）
    raw = _call_deepseek(
        content, system_prompt_json,
        max_tokens=cfg['max_tokens'], timeout=cfg['timeout'],
        json_mode=True,
    )

    # 尝试解析
    data = _safe_parse_json(raw)

    if data and len(data.get('pages', [])) >= target_pages:
        # 完整且页面数达标
        if project_id:
            _update_progress(
                project_id, 50,
                f'AI 生成完成，共 {len(data.get("pages", []))} 页，正在解析...'
            )
        return raw

    # 提取已完成的页面
    existing_pages = []
    existing_nav = []
    if data:
        existing_pages = data.get('pages', [])
        existing_nav = data.get('navigation', [])
        logger.info('多页面生成 #%s: 第一次调用得到 %d/%d 页，需要追加',
                    project_id, len(existing_pages), target_pages)
    else:
        logger.warning('多页面生成 #%s: 第一次调用 JSON 解析失败，尝试修复...', project_id)
        # 尝试修复后再次提取
        repaired = _try_repair_json(raw)
        repaired_data = _safe_parse_json(repaired)
        if repaired_data:
            existing_pages = repaired_data.get('pages', [])
            existing_nav = repaired_data.get('navigation', [])
            logger.info('多页面生成 #%s: 修复后提取到 %d 页',
                        project_id, len(existing_pages))

    # 追加生成
    for attempt in range(max_continuations):
        if len(existing_pages) >= target_pages:
            break

        remaining = target_pages - len(existing_pages)
        if project_id:
            _update_progress(
                project_id, 30 + attempt * 8,
                f'已生成 {len(existing_pages)}/{target_pages} 页，'
                f'正在追加生成剩余 {remaining} 页（第 {attempt + 1}/{max_continuations} 次追加）...'
            )
        logger.info('多页面生成 #%s: 开始第 %d 次追加，还需 %d 页',
                    project_id, attempt + 1, remaining)

        # 构造追加提示：明确列出已有 url_path，避免 AI 重复生成
        existing_info = [
            {'name': p.get('name', ''), 'url_path': p.get('url_path', ''), 'nav_title': p.get('nav_title', '')}
            for p in existing_pages
        ]
        existing_urls_list = [p.get('url_path', '') for p in existing_pages]
        append_content = (
            f'这是一个多页面网站的追加生成任务。之前已生成以下页面（共 {len(existing_pages)} 页）：\n'
            f'{json.dumps(existing_info, ensure_ascii=False, indent=2)}\n\n'
            f'已占用的 url_path（禁止重复）：{json.dumps(existing_urls_list, ensure_ascii=False)}\n\n'
            f'请继续生成剩余的 {remaining} 个页面。要求：\n'
            f'1. 返回纯 JSON，格式为 {{"navigation":[...],"pages":[...]}}\n'
            f'2. navigation 数组只包含这次新生成页面的导航项（{remaining} 项）\n'
            f'3. pages 数组只包含这次新生成的页面（{remaining} 个）\n'
            f'4. 不要重复已有页面的 url_path\n'
            f'5. 必须生成恰好 {remaining} 个页面，不能多也不能少\n'
            f'6. 每个页面必须包含完整字段：name, url_path, nav_title, title, description, keywords, html_content\n'
            f'7. html_content 必须是完整的 HTML 文档（<!DOCTYPE html> 开头，</html> 结尾）'
        )

        # 追加调用（不使用 prefix/stop）
        append_raw = _call_deepseek(
            append_content, system_prompt_json,
            max_tokens=cfg['max_tokens'], timeout=cfg['timeout'],
            json_mode=True,
        )

        # 解析追加响应
        append_data = _safe_parse_json(append_raw)
        if not append_data:
            logger.warning('多页面生成 #%s: 第 %d 次追加 JSON 解析失败，尝试修复...',
                          project_id, attempt + 1)
            repaired = _try_repair_json(append_raw)
            append_data = _safe_parse_json(repaired)
            if not append_data:
                logger.warning('多页面生成 #%s: 第 %d 次追加修复后仍失败，跳过',
                             project_id, attempt + 1)
                continue

        new_pages = append_data.get('pages', [])
        new_nav = append_data.get('navigation', [])

        if not new_pages:
            logger.warning('多页面生成 #%s: 第 %d 次追加 pages 为空', project_id, attempt + 1)
            continue

        # 合并（按 url_path 去重）
        existing_urls = set(p.get('url_path', '') for p in existing_pages)
        added_count = 0
        for page in new_pages:
            url = page.get('url_path', '')
            if url and url not in existing_urls:
                existing_pages.append(page)
                existing_urls.add(url)
                added_count += 1
        for nav in new_nav:
            url = nav.get('url_path', '')
            if url and url not in existing_urls:
                existing_nav.append(nav)

        logger.info('多页面生成 #%s: 第 %d 次追加得到 %d 页（新增 %d），累计 %d 页',
                    project_id, attempt + 1, len(new_pages), added_count, len(existing_pages))

    # 构建最终 JSON
    # 确保 navigation 与 pages 一致
    page_urls = set(p.get('url_path', '') for p in existing_pages)
    nav_filtered = [n for n in existing_nav if n.get('url_path', '') in page_urls]

    # 如果 navigation 不完整，从 pages 补全
    if len(nav_filtered) < len(existing_pages):
        nav_urls = set(n.get('url_path', '') for n in nav_filtered)
        for p in existing_pages:
            url = p.get('url_path', '')
            if url and url not in nav_urls:
                nav_filtered.append({
                    'title': p.get('nav_title', p.get('name', '')),
                    'url_path': url,
                })
                nav_urls.add(url)

    merged = {
        'navigation': nav_filtered[:target_pages],
        'pages': existing_pages[:target_pages],
    }
    if project_id:
        _update_progress(project_id, 50, f'AI 生成完成，共 {len(merged["pages"])} 页，正在解析...')
    return json.dumps(merged, ensure_ascii=False)


def _update_progress(project_id: int, percent: int, message: str):
    """更新内存进度缓存。"""
    _gen_progress[project_id] = {
        'percent': min(100, max(0, percent)),
        'message': message,
    }


def get_multi_gen_progress(project_id: int) -> dict:
    """前端轮询接口。"""
    data = _gen_progress.get(project_id, {})
    return {
        'percent': data.get('percent', 0),
        'message': data.get('message', '等待开始...'),
        'done': data.get('percent', 0) >= 100,
        'error': data.get('error', ''),
    }


def _try_repair_json(raw: str) -> str:
    """尝试修复常见 JSON 问题。"""
    import re
    s = raw.strip()

    # 1. 去掉首尾 ```json / ```html / ``` 包裹
    for fence in ('```json', '```html', '```'):
        if s.startswith(fence):
            s = s[len(fence):]
    if s.endswith('```'):
        s = s[:-3]
    s = s.strip()

    # 2. 截取第一个 { 到最后一个 }
    start = s.find('{')
    end = s.rfind('}')
    if start < 0 or end <= start:
        return raw
    s = s[start:end + 1]

    # 3. 尝试直接解析
    try:
        json.loads(s)
        return s
    except json.JSONDecodeError:
        pass

    # 4. 尝试用 strict=False 解析（允许控制字符等）
    try:
        json.loads(s, strict=False)
        return s
    except json.JSONDecodeError:
        pass

    # 5. 修复 html_content 中的常见问题
    def _fix_html_content_value(s_in: str) -> str:
        """找到 html_content 的值区域，将其中未转义的 " 替换为 &quot;。"""
        result = []
        pattern = re.compile(r'("html_content"\s*:\s*")(.*?)("(?=\s*[,}\n\]]))', re.DOTALL)
        last_end = 0
        for m in pattern.finditer(s_in):
            prefix = m.group(1)
            val = m.group(2)
            fixed_val = val.replace('\\"', '\u0000').replace('"', '&quot;').replace('\u0000', '\\"')
            result.append(s_in[last_end:m.start()])
            result.append(prefix)
            result.append(fixed_val)
            result.append(m.group(3))
            last_end = m.end()
        result.append(s_in[last_end:])
        return ''.join(result)

    s_fixed = _fix_html_content_value(s)
    if s_fixed != s:
        try:
            json.loads(s_fixed)
            return s_fixed
        except json.JSONDecodeError:
            s = s_fixed

    # 6. 处理多对象合并：}{ → },{
    if '}{' in s:
        s_rejoin = s.replace('}{', '},{')
        try:
            json.loads(s_rejoin)
            return s_rejoin
        except json.JSONDecodeError:
            pass

    # 7. 清理不可见/控制字符
    s_clean = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', s)
    try:
        json.loads(s_clean)
        return s_clean
    except json.JSONDecodeError:
        s = s_clean

    # 8. 处理截断：找到最后一个完整闭合点并补全
    def _string_aware_scan(text: str):
        """逐字符扫描 JSON，跟踪深度和字符串状态。
        返回：(最后深度, 是否在字符串内, 最后一个 depth=0 的 }位, 最后一个 depth=1 的 }位)
        """
        depth = 0
        in_str = False
        escape = False
        last_at_depth0 = -1
        last_at_depth1 = -1
        for i, ch in enumerate(text):
            if escape:
                escape = False
                continue
            if ch == '\\' and in_str:
                escape = True
                continue
            if ch == '"' and not escape:
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    last_at_depth0 = i
                elif depth == 1:
                    last_at_depth1 = i
        return depth, in_str, last_at_depth0, last_at_depth1

    depth, in_str, last_d0, last_d1 = _string_aware_scan(s)

    # 如果 JSON 被截断（未闭合），尝试补全
    if depth > 0 or in_str:
        # 优先用 depth=0 的截断点（完整的外层对象）
        if last_d0 >= 0:
            prefix = s[:last_d0 + 1]
            try:
                json.loads(prefix)
                return prefix
            except json.JSONDecodeError:
                pass
        # 然后用 depth=1 的截断点（完整的页面对象后），补全 ]}
        if last_d1 >= 0:
            prefix = s[:last_d1 + 1]
            for suffix in [']}', ']}}', ']}', '}}', '}']:
                candidate = prefix + suffix
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError:
                    pass

    return raw  # 放弃修复


def _get_config_dict() -> dict:
    """从 DB 读取配置，合并默认值后返回 dict。"""
    try:
        from XiaoYingAdmin.models.multi_page_config import MultiPageConfig
        cfg = MultiPageConfig.get_config()
        return {
            'max_tokens': cfg.max_tokens or _DEFAULT_MAX_TOKENS,
            'timeout': cfg.timeout or _DEFAULT_TIMEOUT,
            'max_pages': cfg.max_pages or _DEFAULT_MAX_PAGES,
            'page_content_max_chars': cfg.page_content_max_chars or _DEFAULT_PAGE_CONTENT_MAX_CHARS,
            'system_prompt': cfg.system_prompt or '',
        }
    except Exception:
        return {
            'max_tokens': _DEFAULT_MAX_TOKENS,
            'timeout': _DEFAULT_TIMEOUT,
            'max_pages': _DEFAULT_MAX_PAGES,
            'page_content_max_chars': _DEFAULT_PAGE_CONTENT_MAX_CHARS,
            'system_prompt': '',
        }


def _build_system_prompt(root_domain: str, theme: str, style: str) -> str:
    """构造多页面生成的 system prompt。

    优先使用配置表中的 system_prompt（支持变量替换），
    然后回退到内置默认提示词。

    无论用户如何自定义提示词，末尾都会追加不可覆盖的硬性约束
    （页面数量 + 导航一致性 + JSON 格式），确保生成结果可靠。
    """
    cfg = _get_config_dict()
    prompt_text = cfg['system_prompt'].strip() or _DEFAULT_SYSTEM_PROMPT

    # 替换模板变量
    prompt_text = prompt_text.replace('{root_domain}', root_domain or '（未提供）')
    prompt_text = prompt_text.replace('{theme}', theme)
    prompt_text = prompt_text.replace('{style}', style or 'modern')
    prompt_text = prompt_text.replace('{max_pages}', str(cfg['max_pages']))
    prompt_text = prompt_text.replace('{page_content_max_chars}', str(cfg['page_content_max_chars']))

    # 追加不可覆盖的硬性约束（即使用户自定义提示词也会生效）
    hard_constraints = f"""

---

# ⚠️ 硬性约束（不可违反，优先级最高）

1. **页面数量**：必须生成恰好 {cfg['max_pages']} 个页面，不能多也不能少
   - pages 数组的长度必须等于 {cfg['max_pages']}
   - 如果 {cfg['max_pages']} = 4，则 pages 数组必须包含 4 个页面对象

2. **导航一致性**：navigation 数组与 pages 数组必须一一对应
   - navigation 数组的长度必须等于 pages 数组的长度
   - navigation 中每个 url_path 必须在 pages 中有对应页面
   - pages 中每个页面的 url_path 必须在 navigation 中有对应项
   - 不允许出现"导航有但页面没有"或"页面有但导航没有"的情况

3. **HTML 导航栏一致性**：每个页面 HTML 中的 <nav> 导航栏必须只包含 navigation 数组中的链接
   - <nav> 中的链接数量必须等于 navigation 数组的长度
   - <nav> 中的每个 href 必须在 navigation 数组中有对应项
   - 禁止在 <nav> 中添加任何不在 navigation 数组中的链接

4. **⚠️ 样式一致性（重要）**：所有页面的 <nav> 必须使用**相同的 HTML 结构和 CSS**
   - <nav> 内部结构统一为 `<nav><a href="...">...</a>...</nav>`，不要用 .nav-links / ul+li 等容器
   - CSS 中**必须包含 `nav` 和 `nav a` 标签选择器的样式**（不要只用 .nav-links a 等类选择器）
   - 示例：`nav{{...}} nav a{{...}} nav a:hover{{...}}`
   - 所有 6 个页面的 nav 相关 CSS 必须**完全相同**，复制粘贴即可
   - 同样地，header / footer 的 CSS 在所有页面中也必须完全相同

5. **页面 URL 唯一性**：每个页面的 url_path 必须唯一，不能重复

6. **首页约定**：第一个页面必须是首页，url_path 为 /index.html
"""
    prompt_text = prompt_text.rstrip() + hard_constraints

    return json.dumps([{
        'role': 'system',
        'content': prompt_text,
    }])


def _rebuild_nav_in_html(html: str, nav_config: list) -> str:
    """用 nav_config 重建 HTML 中的 <nav> 导航栏，确保导航链接与实际页面一致。

    行为：
      1. 匹配 <nav ...>...</nav> 块
      2. 从原有第一个 <a> 标签提取 class/style 等属性作为模板
      3. 用 nav_config 重建所有 <a> 标签（统一为 <nav><a>...</a></nav> 结构）
      4. 注入兜底导航栏 CSS（_PUBLIC_NAV_CSS）到 <style> 最前面
         — 因为重建后的 nav 结构可能与 AI 原始 CSS 选择器不匹配
           （如 .nav-links a），兜底 CSS 基于 nav a 标签选择器确保基础样式
         — 不设置 color，让继承 header 颜色，适配深色/浅色背景
         — 原有页面 CSS 可覆盖兜底样式（CSS 层叠规则）
    """
    if not nav_config:
        return html

    nav_pattern = re.compile(r'(<nav[^>]*>)(.*?)(</nav>)', re.DOTALL | re.IGNORECASE)

    def _replace_nav(m):
        nav_open = m.group(1)
        nav_content = m.group(2)
        nav_close = m.group(3)

        # 尝试从原有 <a> 标签提取 class/style 作为模板
        first_a = re.search(r'<a\s+([^>]*?)href=', nav_content)
        a_attrs = first_a.group(1).strip() if first_a else ''
        # 清理可能的多余空格
        a_attrs = ' '.join(a_attrs.split())

        # 用 nav_config 重建链接
        attr_prefix = (a_attrs + ' ') if a_attrs else ''
        links_html = ''.join(
            f'<a {attr_prefix}href="{item.get("url_path", "/")}">'
            f'{item.get("title", "")}</a>'
            for item in nav_config
        )

        return nav_open + links_html + nav_close

    result = nav_pattern.sub(_replace_nav, html)
    nav_changed = result != html

    # 注入兜底导航栏 CSS（放在 <style> 最前面，原有样式可覆盖）
    # 标记位避免重复注入（多次调用 _rebuild_nav_in_html 时）
    _NAV_CSS_MARKER = '/*__public_nav_css__*/'
    if _NAV_CSS_MARKER not in result:
        css_to_inject = _NAV_CSS_MARKER + _PUBLIC_NAV_CSS
        style_pattern = re.compile(r'(<style[^>]*>)', re.IGNORECASE)
        if style_pattern.search(result):
            # 注入到第一个 <style> 开头
            result = style_pattern.sub(lambda m: m.group(1) + css_to_inject, result, count=1)
        elif '</head>' in result.lower():
            # 没有 <style>，在 </head> 前创建一个
            result = re.sub(r'</head>', f'<style>{css_to_inject}</style></head>',
                            result, count=1, flags=re.IGNORECASE)

    return result if (nav_changed or result != html) else html


def start_multi_page_generation(project_id: int):
    """启动多页面生成（异步）。"""
    from XiaoYingAdmin.models.multi_page_project import MultiPageProject
    try:
        project = MultiPageProject.objects.get(pk=project_id)
    except MultiPageProject.DoesNotExist:
        _update_progress(project_id, 0, '项目不存在')
        return

    # 同步设置状态为 GENERATING，确保前端立即看到"生成中"
    # （避免创建后重定向到详情页时仍显示 draft 状态）
    project.status = MultiPageProject.Status.GENERATING
    project.save(update_fields=['status', 'updated_time'])

    _update_progress(project_id, 0, '初始化...')

    t = threading.Thread(
        target=_run_multi_generation,
        args=(project_id,),
        daemon=True,
        name=f'multi-gen-{project_id}',
    )
    t.start()


def _run_multi_generation(project_id: int):
    """后台线程：调用 AI 生成并保存多页面。"""
    from XiaoYingAdmin.models.multi_page_project import MultiPageProject
    from XiaoYingAdmin.models.multi_page import MultiPage

    try:
        # 关闭旧连接，线程内重新建立
        connection.close()

        project = MultiPageProject.objects.get(pk=project_id)
        project.status = MultiPageProject.Status.GENERATING
        project.save(update_fields=['status', 'updated_time'])

        _update_progress(project_id, 5, '正在构建提示词...')

        system_prompt_json = _build_system_prompt(
            project.root_domain, project.theme, project.style
        )
        user_content = (
            f'请为主题「{project.theme}」生成一个完整的多页面网站。'
            f'根域名：{project.root_domain or "未提供"}。'
            f'请严格按照 JSON 格式返回。'
        )

        _update_progress(project_id, 10, '正在请求 AI 生成多页面...（预计 30-180 秒）')

        # 调用 AI（支持前缀续写，处理长输出截断）
        raw = _call_with_continuation(user_content, system_prompt_json, project_id=project_id)

        _update_progress(project_id, 50, 'AI 响应已收到，正在解析...')

        # _call_with_continuation 已返回可解析的 JSON 字符串
        # 这里做最终解析（带修复兜底）
        data = _safe_parse_json(raw)
        if not data:
            _update_progress(project_id, 45, 'JSON 格式异常，尝试自动修复...')
            repaired = _try_repair_json(raw)
            start2 = repaired.find('{')
            end2 = repaired.rfind('}')
            if start2 >= 0 and end2 > start2:
                data = json.loads(repaired[start2:end2 + 1])
            else:
                raise ValueError('AI 返回的 JSON 无法解析')

        _update_progress(project_id, 60, '正在保存页面...')

        # 先保存页面，导航配置在后面根据保存结果生成
        pages_data = data.get('pages', [])
        if not pages_data:
            # 记录详细调试信息，便于诊断 AI 实际返回了什么
            data_keys = list(data.keys()) if isinstance(data, dict) else type(data).__name__
            nav_count = len(data.get('navigation', [])) if isinstance(data, dict) else 0
            logger.error(
                '多页面生成 #%s: pages 为空。data keys=%s, navigation 数量=%d, raw 长度=%d, raw 前500字符=%s',
                project_id, data_keys, nav_count, len(raw), raw[:500]
            )
            # 写入调试文件供分析
            try:
                debug_path = f'P:/XiaoYingCMS/_debug_empty_pages_{project_id}.txt'
                with open(debug_path, 'w', encoding='utf-8') as f:
                    f.write(f'data keys: {data_keys}\n')
                    f.write(f'navigation count: {nav_count}\n')
                    f.write(f'raw length: {len(raw)}\n\n')
                    f.write(f'raw response:\n{raw}')
                logger.info('调试信息已保存到 %s', debug_path)
            except Exception:
                pass
            raise ValueError(
                f'AI 返回的 pages 为空（data 字段: {data_keys}, '
                f'navigation: {nav_count} 项）。请重试，若持续失败请检查系统提示词配置。'
            )

        total = len(pages_data)
        _update_progress(project_id, 65, f'共 {total} 个页面，开始保存...')

        saved_count = 0
        saved_url_paths = []
        errors = []

        for idx, page_data in enumerate(pages_data):
            try:
                name = page_data.get('name', '').strip()
                url_path = page_data.get('url_path', '').strip()
                html_content = page_data.get('html_content', '').strip()

                if not name:
                    name = f'页面{idx + 1}'
                if not url_path:
                    url_path = f'/page{idx + 1}.html'
                if not url_path.startswith('/'):
                    url_path = '/' + url_path

                # 移除首尾可能的反引号
                html_content = _strip_code_fence(html_content)
                # 还原 html entity（AI 可能把 " → &quot; 以避免 JSON 转义问题）
                html_content = html_content.replace('&quot;', '"').replace('&amp;', '&')
                # 安全：移除任何外部 CSS 文件引用
                html_content = re.sub(
                    r'<link\s[^>]*rel=["\']stylesheet["\'][^>]*/?>',
                    '',
                    html_content,
                    flags=re.IGNORECASE,
                )
                # 移动端安全兜底（防横向滚动 / favicon）
                html_content = _postprocess_html(html_content)

                # Full URL 在 MultiPage.save() 中自动计算
                page = MultiPage(
                    project=project,
                    name=name,
                    url_path=url_path,
                    title=page_data.get('title', name)[:500],
                    description=page_data.get('description', '')[:500],
                    keywords=page_data.get('keywords', '')[:500],
                    html_content=html_content,
                    nav_title=page_data.get('nav_title', name)[:100],
                    sort_order=idx,
                )
                page.save()

                saved_count += 1
                saved_url_paths.append(url_path)
                progress = 65 + int((idx + 1) / total * 30)
                _update_progress(project_id, progress, f'已保存 {saved_count}/{total}：{name}')

            except Exception as e:
                errors.append(f'页面 "{page_data.get("name", "?")}" 保存失败: {e}')
                logger.warning('多页面保存失败（第 %d 页）: %s', idx + 1, e)

        # 根据实际保存成功的页面构建导航配置，确保与页面列表一致
        nav = data.get('navigation', [])
        nav_filtered = [n for n in nav if n.get('url_path') in saved_url_paths]
        if not nav_filtered:
            # 如果导航全都不匹配，尝试用页面数据重建导航
            nav_filtered = [{'title': p.get('nav_title') or p.get('name'), 'url_path': p.get('url_path')}
                           for p in pages_data if p.get('url_path') in saved_url_paths]

        project.nav_config = nav_filtered
        project.save(update_fields=['nav_config', 'updated_time'])

        # 用 nav_config 同步重建每个页面 HTML 中的 <nav> 导航栏，
        # 确保 AI 硬编码的导航链接与实际页面一致（避免 404 链接）
        _update_progress(project_id, 93, '同步导航栏...')
        updated_nav_count = 0
        for page in MultiPage.objects.filter(project=project):
            new_html = _rebuild_nav_in_html(page.html_content, nav_filtered)
            if new_html != page.html_content:
                page.html_content = new_html
                page.save(update_fields=['html_content', 'updated_time'])
                updated_nav_count += 1
        if updated_nav_count:
            logger.info('项目 #%d: 已同步 %d 个页面的导航栏', project.id, updated_nav_count)

        # 更新项目状态
        if errors:
            project.status = MultiPageProject.Status.COMPLETED
            project.save(update_fields=['status', 'updated_time'])
            _update_progress(project_id, 95, f'完成，但 {len(errors)} 个页面保存出错')
            # 记录错误详情
            for err in errors:
                logger.error('多页面保存错误: %s', err)
        else:
            project.status = MultiPageProject.Status.COMPLETED
            project.save(update_fields=['status', 'updated_time'])
            _update_progress(project_id, 100, f'全部完成！共生成 {saved_count} 个页面')

    except json.JSONDecodeError as e:
        logger.error('JSON 解析失败: %s\n原始响应前2000字符: %s', e, raw[:2000])
        # 写入调试文件
        try:
            debug_path = f'P:/XiaoYingCMS/_debug_json_fail_{project_id}.txt'
            with open(debug_path, 'w', encoding='utf-8') as f:
                f.write(f'Error: {e}\n\nFull raw response:\n{raw}')
            logger.info('原始响应已保存到 %s', debug_path)
        except Exception:
            pass
        error_msg = f'AI 返回的 JSON 格式无效，请重试。{e}'
        _gen_progress[project_id] = {
            'percent': 100,
            'message': error_msg,
            'error': error_msg,
        }
        _mark_project_failed(project_id, error_msg)

    except Exception as e:
        logger.error('多页面生成失败: %s\n%s', e, traceback.format_exc())
        error_msg = f'生成失败: {e}'
        _gen_progress[project_id] = {
            'percent': 100,
            'message': error_msg,
            'error': error_msg,
        }
        _mark_project_failed(project_id, str(e))

    finally:
        connection.close()


def _mark_project_failed(project_id: int, error_msg: str):
    """标记项目为失败状态。"""
    from XiaoYingAdmin.models.multi_page_project import MultiPageProject
    try:
        project = MultiPageProject.objects.get(pk=project_id)
        project.status = MultiPageProject.Status.FAILED
        project.save(update_fields=['status', 'updated_time'])
    except Exception:
        pass
