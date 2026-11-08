"""
参考网址分析服务 — 多个子 Agent 并行学习参考站，主 Agent 汇总为页面需求描述。

流程：
  1. 校验并规范化用户提交的 1-5 个网址（仅访问该 URL，不递归站内链接）
  2. 并行抓取 HTML（纯 HTTP，15 秒超时，最多 2MB）
  3. 每个站点派一个子 Agent：提取 SEO / 区块结构 / 组件 / head / footer /
     视觉风格 / 文案要点 / 友链形式，输出结构化 JSON
  4. 主 Agent 汇总全部子 Agent 结论，输出一段可直接粘贴的中文需求描述
  5. 友链只做文字说明，绝不把对方链接放进描述词

说明：
  - 抓不到正文（Vue/React 空壳、反爬、超时）的站点会被标记为「暂不支持」，
    不影响其他站点，也不会让用户复制到任何异常内容
  - 抓取前做内网地址校验，避免被用于探测内网
"""

import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from XiaoYingAdmin.common.http import err, parse_json_body
from XiaoYingAdmin.views.page_generator import (
    DEEPSEEK_API_URL,
    DEEPSEEK_MODEL,
    _get_deepseek_config,
)

# ── 限制 ──
MAX_URLS = 5
FETCH_TIMEOUT = 15
MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_PAGE_TEXT = 9000
SUB_MAX_TOKENS = 2500
MAIN_MAX_TOKENS = 5000
SUB_TIMEOUT = 150
MAIN_TIMEOUT = 240

USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/124.0 Safari/537.36'
)

UNSUPPORTED_MSG = '该站点暂不支持解析（可能由前端框架渲染或存在访问限制），系统正在升级以支持更多站点'

# 前端框架空壳特征
_FRAMEWORK_HINTS = (
    'id="app"', "id='app'", 'id="root"', "id='root'",
    '__NUXT__', '__NEXT_DATA__', 'ng-app', 'data-v-',
)

_FRIEND_LINK_KEYWORDS = (
    '友情链接', '友链', '合作伙伴', '合作站点', '友情合作', 'partner', 'partners',
)


# =============================================================================
# LLM 调用
# =============================================================================

def _call_llm(messages, *, api_key, thinking, max_tokens, timeout, json_mode=False):
    """调用 DeepSeek Chat API（线程内使用，不做任何 ORM 操作）。"""
    payload = {
        'model': DEEPSEEK_MODEL,
        'messages': messages,
        'stream': False,
        'thinking': {'type': 'enabled' if thinking else 'disabled'},
        'max_tokens': int(max_tokens),
    }
    if thinking:
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

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode('utf-8')
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', errors='replace')[:300]
        raise RuntimeError(f'DeepSeek HTTP {e.code}: {detail}')
    except urllib.error.URLError as e:
        raise RuntimeError(f'DeepSeek 连接失败: {e.reason}')
    except TimeoutError:
        raise RuntimeError(f'DeepSeek 请求超时（{timeout} 秒）')

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise RuntimeError('DeepSeek 返回格式异常')

    if isinstance(data, dict) and data.get('error'):
        message = data['error'].get('message') if isinstance(data['error'], dict) else data['error']
        raise RuntimeError(f'DeepSeek 错误: {message}')

    choices = data.get('choices') or []
    if choices:
        content = (choices[0].get('message') or {}).get('content') or ''
        if content:
            return content
    raise RuntimeError('DeepSeek 返回内容为空')


def _parse_json_object(text):
    """从模型输出中尽量解析出 JSON 对象，失败返回 None。"""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith('```'):
        cleaned = re.sub(r'^```[a-zA-Z]*\s*', '', cleaned)
        cleaned = re.sub(r'\s*```$', '', cleaned)
    start, end = cleaned.find('{'), cleaned.rfind('}')
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return None


# =============================================================================
# 网址校验与抓取
# =============================================================================

def _normalize_url(raw):
    """补全协议并校验，返回规范化 URL；非法返回 None。"""
    if not isinstance(raw, str):
        return None
    url = raw.strip()
    if not url:
        return None

    # 已带协议：只接受 http / https
    if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', url):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme.lower() not in ('http', 'https') or not parsed.netloc:
            return None
        return url

    # javascript: / mailto: / data: 等非网址协议一律拒绝
    if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', url):
        return None

    url = 'https://' + url
    parsed = urllib.parse.urlparse(url)
    if not parsed.netloc:
        return None
    return url


def _assert_public_host(host):
    """禁止内网 / 回环 / 链路本地地址，防止被用于探测内网。"""
    host = (host or '').strip().lower()
    if not host:
        raise ValueError('网址缺少域名')
    if host in ('localhost', 'localhost.localdomain'):
        raise ValueError('不允许访问本机地址')

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ValueError(f'域名无法解析：{host}')

    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise ValueError(f'不允许访问内网地址：{host}')


def _decode_html(raw_bytes, content_type):
    """按 Content-Type / meta charset / utf-8 依次尝试解码。"""
    charset = ''
    match = re.search(r'charset=([\w-]+)', content_type or '', re.IGNORECASE)
    if match:
        charset = match.group(1)

    if not charset:
        head = raw_bytes[:2048].decode('ascii', errors='ignore')
        match = re.search(r'charset=["\']?([\w-]+)', head, re.IGNORECASE)
        if match:
            charset = match.group(1)

    for enc in (charset, 'utf-8', 'gb18030'):
        if not enc:
            continue
        try:
            return raw_bytes.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw_bytes.decode('utf-8', errors='replace')


def _fetch_html(url):
    """抓取单页 HTML。返回 (html, None) 或 (None, 失败原因)。"""
    parsed = urllib.parse.urlparse(url)
    _assert_public_host(parsed.hostname)

    req = urllib.request.Request(url, headers={
        'User-Agent': USER_AGENT,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Cache-Control': 'no-cache',
    }, method='GET')

    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            raw = resp.read(MAX_HTML_BYTES)
            content_type = resp.headers.get('Content-Type', '')
            if 'html' not in content_type.lower() and raw.lstrip()[:1] not in (b'<', b'\xef'):
                return None, '该地址返回的不是 HTML 页面'
            return _decode_html(raw, content_type), None
    except urllib.error.HTTPError as e:
        return None, f'目标站点返回 HTTP {e.code}'
    except urllib.error.URLError as e:
        return None, f'无法访问该站点：{e.reason}'
    except TimeoutError:
        return None, f'抓取超时（{FETCH_TIMEOUT} 秒）'
    except OSError as e:
        return None, f'网络错误：{e}'


# =============================================================================
# HTML 特征提取（确定性分析，作为子 Agent 的事实输入）
# =============================================================================

def _strip_tags(html):
    """去脚本 / 样式 / 标签，返回纯文本。"""
    text = re.sub(r'<script\b[^>]*>.*?</script>', ' ', html or '', flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<style\b[^>]*>.*?</style>', ' ', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<noscript\b[^>]*>.*?</noscript>', ' ', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<svg\b[^>]*>.*?</svg>', ' ', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<!--.*?-->', ' ', text, flags=re.DOTALL)
    text = re.sub(r'<(br|/p|/div|/section|/li|/h[1-6])\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = _unescape(text)
    text = re.sub(r'[ \t\u00a0]+', ' ', text)
    text = re.sub(r'\n\s*\n+', '\n', text)
    return text.strip()


def _unescape(text):
    import html as html_module
    return html_module.unescape(text or '')


def _find_meta(html):
    """提取 meta 标签与 title / canonical / JSON-LD。"""
    metas = {}
    for tag in re.findall(r'<meta\b[^>]*>', html or '', flags=re.IGNORECASE):
        name_m = re.search(r'(?:name|property)\s*=\s*["\']([^"\']+)["\']', tag, re.IGNORECASE)
        content_m = re.search(r'content\s*=\s*["\']([^"\']*)["\']', tag, re.IGNORECASE)
        if name_m and content_m:
            metas[name_m.group(1).strip().lower()] = content_m.group(1).strip()

    title_m = re.search(r'<title[^>]*>(.*?)</title>', html or '', flags=re.IGNORECASE | re.DOTALL)
    title = _unescape(re.sub(r'\s+', ' ', title_m.group(1))).strip() if title_m else ''

    canonical = ''
    for tag in re.findall(r'<link\b[^>]*>', html or '', flags=re.IGNORECASE):
        if 'canonical' in tag.lower():
            href_m = re.search(r'href\s*=\s*["\']([^"\']+)["\']', tag, re.IGNORECASE)
            if href_m:
                canonical = href_m.group(1)
                break

    ld_types = []
    for block in re.findall(
        r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>',
        html or '', flags=re.IGNORECASE | re.DOTALL,
    ):
        for t in re.findall(r'"@type"\s*:\s*"([^"]+)"', block):
            if t not in ld_types:
                ld_types.append(t)

    return {
        'title': title,
        'description': metas.get('description', ''),
        'keywords': metas.get('keywords', ''),
        'og': {k: v for k, v in metas.items() if k.startswith('og:')},
        'twitter': {k: v for k, v in metas.items() if k.startswith('twitter:')},
        'canonical': canonical,
        'structured_data_types': ld_types[:8],
    }


def _extract_structure(html):
    """统计区块 / 组件 / 视觉风格等结构信息。"""
    html = html or ''
    lower = html.lower()

    def _count(pattern):
        return len(re.findall(pattern, html, flags=re.IGNORECASE))

    headings = []
    for level in ('h1', 'h2', 'h3'):
        items = []
        for m in re.finditer(rf'<{level}\b[^>]*>(.*?)</{level}>', html, flags=re.IGNORECASE | re.DOTALL):
            text = re.sub(r'<[^>]+>', '', m.group(1))
            text = re.sub(r'\s+', ' ', _unescape(text)).strip()
            if text:
                items.append(text)
        headings.append({'level': level.upper(), 'items': items[:12], 'count': len(items)})

    colors = {}
    for c in re.findall(r'#[0-9a-fA-F]{6}\b', html):
        colors[c.lower()] = colors.get(c.lower(), 0) + 1
    top_colors = sorted(colors.items(), key=lambda kv: -kv[1])[:8]

    fonts = {}
    for f in re.findall(r'font-family\s*:\s*([^;}"\']+)', html, flags=re.IGNORECASE):
        key = f.strip()[:60]
        fonts[key] = fonts.get(key, 0) + 1
    top_fonts = sorted(fonts.items(), key=lambda kv: -kv[1])[:3]

    footer_m = re.search(r'<footer\b[^>]*>(.*?)</footer>', html, flags=re.IGNORECASE | re.DOTALL)
    footer_text = _strip_tags(footer_m.group(1))[:600] if footer_m else ''

    nav_m = re.search(r'<nav\b[^>]*>(.*?)</nav>', html, flags=re.IGNORECASE | re.DOTALL)
    nav_text = _strip_tags(nav_m.group(1))[:300] if nav_m else ''

    return {
        'has_semantic_tags': {
            'header': '<header' in lower,
            'nav': '<nav' in lower,
            'main': '<main' in lower,
            'section': '<section' in lower,
            'footer': '<footer' in lower,
        },
        'counts': {
            'section': _count(r'<section\b'),
            'div': _count(r'<div\b'),
            'img': _count(r'<img\b'),
            'svg': _count(r'<svg\b'),
            'form': _count(r'<form\b'),
            'table': _count(r'<table\b'),
            'details': _count(r'<details\b'),
            'card_like': _count(r'class="[^"]*(card|item|feature)[^"]*"'),
            'cta_like': _count(r'class="[^"]*(btn|button|cta)[^"]*"'),
        },
        'headings': headings,
        'top_colors': [c for c, _ in top_colors],
        'top_fonts': [f for f, _ in top_fonts],
        'nav_text': nav_text,
        'footer_text': footer_text,
        'has_external_css': bool(re.search(r'<link[^>]+stylesheet', html, flags=re.IGNORECASE)),
        'has_media_queries': '@media' in lower,
    }


def _detect_friend_links(html, page_text):
    """检测友链区块是否存在（只判断形式，不提取链接）。"""
    source = page_text or ''
    hit = ''
    for kw in _FRIEND_LINK_KEYWORDS:
        if kw in source or kw in source.lower():
            hit = kw
            break
    if not hit:
        return {'exists': False, 'keyword': '', 'form_hint': ''}

    # 友链的典型形态是「关键词 + 多个链接」，
    # 仅出现关键词（如正文提到"友情链接"）且链接很少时不算友链区块
    footer_m = re.search(r'<footer\b[^>]*>(.*?)</footer>', html or '', flags=re.IGNORECASE | re.DOTALL)
    if footer_m:
        footer_html = footer_m.group(1)
        if hit in _strip_tags(footer_html):
            link_count = len(re.findall(r'<a\b', footer_html, flags=re.IGNORECASE))
            if link_count >= 3:
                return {
                    'exists': True, 'keyword': hit,
                    'form_hint': f'位于页脚区域的链接列表（约 {link_count} 个链接）',
                }

    total_links = len(re.findall(r'<a\b', html or '', flags=re.IGNORECASE))
    if total_links >= 3 and (hit in source):
        return {
            'exists': True, 'keyword': hit,
            'form_hint': '独立的友链/合作伙伴区块（横向排列的品牌链接）',
        }

    return {'exists': False, 'keyword': hit, 'form_hint': ''}


def _looks_unsupported(html, page_text):
    """判断是否为前端框架空壳或反爬拦截页。"""
    if not html:
        return True
    if len(page_text or '') < 180:
        return True
    lower = html.lower()
    if any(hint in lower for hint in _FRAMEWORK_HINTS) and len(page_text or '') < 400:
        return True
    return False


# =============================================================================
# 子 Agent / 主 Agent
# =============================================================================

_SUB_SYSTEM_PROMPT = """你是一位资深前端架构师 + SEO 专家。用户会给你一个参考网站的页面特征与正文摘要，请分析该页面并输出 JSON。

只返回一个 JSON 对象，不要任何解释文字，结构如下：

{
  "page_type": "页面类型，如 SaaS 落地页 / 电商首页 / 企业官网",
  "positioning": "这个页面在卖什么、目标用户是谁，一句话",
  "blocks": [
    {"name": "Hero 首屏", "purpose": "为什么放这里", "content": "该区块包含哪些元素与文案要点"}
  ],
  "components": ["导航栏", "功能卡片组", "FAQ 手风琴"],
  "visual": {
    "colors": ["主色", "辅助色"],
    "style": "整体视觉风格描述",
    "typography": "字体与排版特征",
    "motion": "动效特征"
  },
  "copywriting": "文案风格：语气、长度、是否用数字背书、CTA 写法",
  "seo": {
    "title_pattern": "标题写法规律",
    "description_pattern": "描述写法规律",
    "keywords": ["关键词1", "关键词2"],
    "structured_data": ["FAQPage"]
  },
  "friend_links": {
    "exists": true,
    "form": "友链的展示形式（位置、排列方式、锚文本风格）",
    "count_hint": "大致数量"
  },
  "highlights": ["值得借鉴的设计点"]
}

硬性要求：
1. blocks 必须按页面从上到下的真实顺序列出，至少 5 个（不足则按实际数量）
2. 只描述结构与形式，**不要输出任何具体网址、域名或友链链接**
3. friend_links 只描述形式，exists=false 时 form 留空
4. 全部使用中文
"""


_MAIN_SYSTEM_PROMPT = """你是一位资深产品经理兼前端架构师。用户会给你多个参考网站的分析结论，你需要把它们整合成一段可以直接交给 AI 用来生成页面的「需求描述」。

输出要求：
1. 只输出一段中文自然语言描述，**总长度控制在 1000-1500 字**，分成 5-7 个自然段，不要使用 markdown 标题、列表符号、代码块
2. 必须覆盖：页面定位与目标用户；页面结构（按从上到下的顺序说明每个区块要放什么内容，8 个左右即可，不要逐像素罗列）；关键组件与交互；视觉风格（配色、字体、圆角、留白、动效）；文案风格；SEO 要求（标题、描述、关键词、结构化数据）；行动号召
3. 若多个参考站有差异，选择更适合落地页的做法并说明理由，不要罗列“参考站 A 用的…参考站 B 用的…”
4. **绝对不能出现任何第三方网址、域名、品牌名或友链链接**，需要提到友链时只写“页脚可放置自己的友情链接区块”
5. 不要提到“参考网站分析”“子 agent”之类的过程性描述，直接以需求口吻书写
6. 结尾明确要求生成完整、可直接上线的响应式 HTML 页面，样式内嵌、不依赖外部框架
"""


def _sub_agent(url, html, page_text, features, friend_info, *, api_key, thinking):
    """单个站点的子 Agent：返回结构化分析 dict。"""
    host = urllib.parse.urlparse(url).hostname or url
    user_prompt = (
        f'分析对象域名：{host}\n\n'
        f'【提取到的 SEO 与结构特征】\n'
        f'{json.dumps(features, ensure_ascii=False, indent=2)}\n\n'
        f'【友链检测结果】\n'
        f'{json.dumps(friend_info, ensure_ascii=False)}\n\n'
        f'【页面正文摘要】\n{page_text[:MAX_PAGE_TEXT]}'
    )

    content = _call_llm(
        [
            {'role': 'system', 'content': _SUB_SYSTEM_PROMPT},
            {'role': 'user', 'content': user_prompt},
        ],
        api_key=api_key,
        thinking=False,  # 子 Agent 固定关闭思考，控制耗时与成本
        max_tokens=SUB_MAX_TOKENS,
        timeout=SUB_TIMEOUT,
        json_mode=True,
    )

    parsed = _parse_json_object(content)
    if not isinstance(parsed, dict):
        parsed = {
            'page_type': '未知',
            'positioning': '',
            'blocks': [],
            'components': [],
            'visual': {},
            'copywriting': '',
            'seo': {},
            'friend_links': friend_info,
            'highlights': [],
            'raw_analysis': content[:1500],
        }
    return parsed


def _main_agent(analyses, *, api_key, thinking):
    """主 Agent：把多个子 Agent 结论汇总为一段中文需求描述。"""
    payload = [
        {
            'domain': urllib.parse.urlparse(item['url']).hostname,
            'analysis': item['analysis'],
        }
        for item in analyses
    ]
    return _call_llm(
        [
            {'role': 'system', 'content': _MAIN_SYSTEM_PROMPT},
            {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False, indent=2)},
        ],
        api_key=api_key,
        thinking=thinking,  # 主 Agent 跟随后台思考模式开关
        max_tokens=MAIN_MAX_TOKENS,
        timeout=MAIN_TIMEOUT,
    ).strip()


def _build_friend_note(sites):
    """根据子 Agent 结论生成友链提醒（不含任何链接）。"""
    hits = []
    for site in sites:
        analysis = site.get('analysis') or {}
        fl = analysis.get('friend_links') or {}
        if isinstance(fl, dict) and fl.get('exists'):
            host = urllib.parse.urlparse(site['url']).hostname
            form = (fl.get('form') or fl.get('form_hint') or '存在友链区块').strip()
            hits.append(f'{host}：{form}')

    if not hits:
        return ''
    return (
        f'检测到 {len(hits)} 个参考站存在友情链接区块 —— ' + '；'.join(hits) +
        '。建议生成页面时在页脚放置你自己的友链，不要照搬对方的链接。'
    )


# =============================================================================
# 对外入口
# =============================================================================

def analyze_reference_urls(raw_urls):
    """
    分析参考网址并生成需求描述。

    返回 dict：
      {'description': str, 'friend_link_note': str, 'sites': [...]}

    抛出 ValueError（参数问题）/ RuntimeError（AI 调用问题）。
    """
    if not isinstance(raw_urls, list):
        raise ValueError('网址列表格式错误')

    cleaned = []
    for raw in raw_urls:
        url = _normalize_url(raw)
        if url and url not in cleaned:
            cleaned.append(url)

    if not cleaned:
        raise ValueError('请至少提供一个有效的参考网址')
    if len(cleaned) > MAX_URLS:
        raise ValueError(f'最多支持 {MAX_URLS} 个参考网址')

    # 在主线程读取配置，线程内不再访问数据库
    api_key, thinking = _get_deepseek_config()

    # ---- 1. 并行抓取 ----
    fetched = []
    with ThreadPoolExecutor(max_workers=min(len(cleaned), MAX_URLS)) as pool:
        future_map = {pool.submit(_fetch_html, url): url for url in cleaned}
        for future in as_completed(future_map):
            url = future_map[future]
            try:
                html, failure = future.result()
            except ValueError as e:
                html, failure = None, str(e)
            except Exception as e:  # noqa: BLE001 — 单站失败不影响整体
                html, failure = None, f'抓取异常：{e}'

            site = {'url': url, 'ok': False, 'reason': failure or ''}
            if html:
                page_text = _strip_tags(html)
                if _looks_unsupported(html, page_text):
                    site['reason'] = UNSUPPORTED_MSG
                else:
                    site['ok'] = True
                    site['html'] = html
                    site['page_text'] = page_text
                    site['features'] = _extract_structure(html)
                    site['features']['seo'] = _find_meta(html)
            fetched.append(site)

    # 保持用户输入顺序
    order = {url: i for i, url in enumerate(cleaned)}
    fetched.sort(key=lambda s: order.get(s['url'], 99))

    ok_sites = [s for s in fetched if s['ok']]
    if not ok_sites:
        reasons = '；'.join(f'{urllib.parse.urlparse(s["url"]).hostname}：{s["reason"]}' for s in fetched)
        raise RuntimeError(f'所有参考网址都无法解析。{reasons}')

    # ---- 2. 并行子 Agent 分析 ----
    analyses = []
    with ThreadPoolExecutor(max_workers=min(len(ok_sites), MAX_URLS)) as pool:
        future_map = {}
        for site in ok_sites:
            future = pool.submit(
                _sub_agent,
                site['url'], site['html'], site['page_text'],
                site['features'], _detect_friend_links(site['html'], site['page_text']),
                api_key=api_key, thinking=thinking,
            )
            future_map[future] = site

        for future in as_completed(future_map):
            site = future_map[future]
            try:
                site['analysis'] = future.result()
            except Exception as e:  # noqa: BLE001
                site['ok'] = False
                site['reason'] = f'AI 分析失败：{e}'
                site['analysis'] = {}
            analyses.append(site)

    analyses.sort(key=lambda s: order.get(s['url'], 99))
    usable = [s for s in analyses if s.get('analysis')]

    if not usable:
        raise RuntimeError('参考站点解析成功，但 AI 分析全部失败，请稍后重试')

    # ---- 3. 主 Agent 汇总 ----
    description = _main_agent(usable, api_key=api_key, thinking=thinking)
    if not description:
        raise RuntimeError('AI 未返回描述内容，请稍后重试')

    # ---- 4. 汇总站点状态（不含 HTML 等大字段） ----
    sites = []
    for site in analyses:
        sites.append({
            'url': site['url'],
            'host': urllib.parse.urlparse(site['url']).hostname or site['url'],
            'ok': bool(site.get('analysis')),
            'reason': '' if site.get('analysis') else (site.get('reason') or UNSUPPORTED_MSG),
            'page_type': (site.get('analysis') or {}).get('page_type', ''),
        })

    return {
        'ok': True,
        'description': description,
        'friend_link_note': _build_friend_note(usable),
        'sites': sites,
        'analyzed_count': len(usable),
    }


@csrf_exempt
@require_POST
def api_reference_analyze(request):
    """POST /xiaoying_admin/api/pages/reference-analyze/

    请求体：{"urls": ["https://a.com", ...]} 或 {"urls": "多行文本"}
    返回：{ok, description, friend_link_note, sites, analyzed_count}
    """
    body, body_error = parse_json_body(request)
    if body_error:
        return body_error

    raw_urls = body.get('urls')
    if isinstance(raw_urls, str):
        raw_urls = [u for u in re.split(r'[\s,;，；]+', raw_urls) if u.strip()]

    if not raw_urls:
        return err('请至少填写一个参考网址')

    try:
        result = analyze_reference_urls(raw_urls)
    except ValueError as e:
        return err(str(e), status=400)
    except RuntimeError as e:
        return err(str(e), status=400)
    except Exception as e:  # noqa: BLE001 — 兜底，避免 500 页面暴露细节
        return err(f'分析失败：{type(e).__name__}', status=500)

    return JsonResponse(result)
