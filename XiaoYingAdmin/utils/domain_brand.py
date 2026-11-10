# -*- coding: utf-8 -*-
"""域名品牌资源处理 —— 图标生成、HTML 注入、资源响应。

三个入口：
  - generate_icon_assets(brand)   由一张原图生成 ico / 32 / 180 / 192 / 512 / OG 图
  - apply_brand_to_html(html, host)  把 favicon、SEO 图片与页面内 Logo 注入 HTML
  - brand_asset_response(brand, path) 按 /favicon.ico 等稳定路径返回文件

所有函数都做了兜底：出错时返回原值 / None，绝不因为品牌配置破坏页面渲染。
"""

import html as html_lib
import io
import logging
import re

from django.core.files.base import ContentFile
from django.http import FileResponse, Http404, HttpResponse

from XiaoYingAdmin.models.domain_brand import DomainBrand

logger = logging.getLogger(__name__)

ICON_SIZES = {
    'icon_32': 32,
    'icon_180': 180,
    'icon_192': 192,
    'icon_512': 512,
}

# 必应验证码：字母、数字、下划线、短横线，长度 8-128
BING_CODE_RE = re.compile(r'^[A-Za-z0-9_-]{8,128}$')
_BING_META_RE = re.compile(
    r'<meta\b[^>]*\bname\s*=\s*["\']msvalidate\.01["\'][^>]*>',
    re.IGNORECASE,
)


def normalize_bing_code(value):
    return (value or '').strip()


def validate_bing_code(value):
    """校验并返回必应验证码；空值返回空字符串，格式错误抛 ValueError。"""
    code = normalize_bing_code(value)
    if not code:
        return ''
    if not BING_CODE_RE.match(code):
        raise ValueError('必应验证码格式不正确，应为 8-128 位字母、数字、下划线或短横线')
    return code


def parse_bing_auth_xml(content):
    """从 BingSiteAuth.xml 文件内容中解析出验证码。"""
    if isinstance(content, bytes):
        try:
            text = content.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = content.decode('utf-8', 'ignore')
    else:
        text = str(content)

    match = re.search(r'<user>\s*([^<]+?)\s*</user>', text, re.IGNORECASE)
    if match:
        return validate_bing_code(match.group(1))

    # 兼容只包含验证码的纯文本文件
    candidate = text.strip()
    if candidate and '<' not in candidate:
        return validate_bing_code(candidate)

    raise ValueError('未在文件中找到 <user> 验证码，请确认上传的是 Bing 的 BingSiteAuth.xml')


def bing_auth_response(brand):
    """按域名动态返回 BingSiteAuth.xml 内容（不落物理文件）。"""
    try:
        code = validate_bing_code(getattr(brand, 'bing_verify_code', ''))
    except ValueError:
        raise Http404('验证码格式无效')
    if not code:
        raise Http404('未配置必应验证码')

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<users>\n'
        f'  <user>{code}</user>\n'
        '</users>\n'
    )
    response = HttpResponse(xml, content_type='text/xml; charset=utf-8')
    response['Cache-Control'] = 'public, max-age=300'
    response['X-Robots-Tag'] = 'noindex'
    return response


BRAND_STYLE = (
    '<style id="xy-brand-style">'
    '.xy-brand-logo{display:block;height:40px;width:auto;max-width:220px;'
    'object-fit:contain}'
    '@media(max-width:768px){.xy-brand-logo{height:32px;max-width:160px}}'
    '</style>'
)


# =============================================================================
# 图标生成
# =============================================================================

def _square(img, size, background=(0, 0, 0, 0)):
    """把图片等比缩放（支持放大）并居中放到正方形画布上。"""
    from PIL import Image

    img = img.convert('RGBA')
    if img.width and img.height:
        ratio = min(size / img.width, size / img.height)
        new_w = max(1, int(round(img.width * ratio)))
        new_h = max(1, int(round(img.height * ratio)))
        if (new_w, new_h) != img.size:
            img = img.resize((new_w, new_h), Image.LANCZOS)
    canvas = Image.new('RGBA', (size, size), background)
    left = (size - img.width) // 2
    top = (size - img.height) // 2
    canvas.paste(img, (left, top), img)
    return canvas


def smart_crop(img, threshold=10, padding_ratio=0.04):
    """裁剪掉图片四周的透明或纯色留白，让 Logo 内容尽量占满画布。

    很多上传的 Logo 是“大画布 + 小图案”，直接缩放会导致生成的图标看起来很小。
    这里先根据透明通道或背景色找出真实内容区域，再按比例留一点边距后裁剪。
    """
    from PIL import Image, ImageChops

    img = img.convert('RGBA')
    width, height = img.size
    if not width or not height:
        return img

    alpha = img.getchannel('A')
    alpha_min, _alpha_max = alpha.getextrema()
    bbox = None

    if alpha_min < 255:
        # 有透明区域：直接用 alpha 通道定位内容
        bbox = alpha.getbbox()
    else:
        # 完全不透明：以左上角颜色作为背景色，找出与背景不同的区域
        background = img.getpixel((0, 0))
        bg_img = Image.new('RGBA', img.size, background)
        diff = ImageChops.difference(img, bg_img).convert('L')
        mask = diff.point(lambda p: 255 if p > threshold else 0)
        bbox = mask.getbbox()

    if not bbox:
        return img

    left, top, right, bottom = bbox
    content_w = right - left
    content_h = bottom - top
    if content_w <= 0 or content_h <= 0:
        return img

    # 内容本来就占满画布时不裁，避免影响正常图片
    if content_w >= width * 0.9 and content_h >= height * 0.9:
        return img

    padding = int(max(content_w, content_h) * padding_ratio)
    left = max(0, left - padding)
    top = max(0, top - padding)
    right = min(width, right + padding)
    bottom = min(height, bottom + padding)
    return img.crop((left, top, right, bottom))


def _save_field(brand, field_name, content, filename):
    """把字节内容写入模型的 FileField（不触发 save）。"""
    field = getattr(brand, field_name)
    if field:
        try:
            field.delete(save=False)
        except Exception:  # noqa: BLE001
            pass
    field.save(filename, ContentFile(content), save=False)
    return True


def generate_icon_assets(brand):
    """按原图生成所有尺寸；手工上传过的尺寸会跳过。

    返回 (ok, message, generated_list)
    """
    try:
        from PIL import Image
    except ImportError:
        return False, '服务器未安装 Pillow，无法自动生成尺寸', []

    source = brand.source_image or brand.logo
    if not source:
        return False, '请先上传原图或 Logo 后再生成', []

    try:
        source.open('rb')
        img = Image.open(source)
        img.load()
        # 自动裁掉四周留白，避免“大画布 + 小图案”导致图标显示过小
        img = smart_crop(img)
    except Exception as e:  # noqa: BLE001
        return False, f'图片读取失败：{e}', []
    finally:
        try:
            source.close()
        except Exception:  # noqa: BLE001
            pass

    manual = set(brand.manual_sizes or [])
    generated = []

    try:
        for field_name, size in ICON_SIZES.items():
            if field_name in manual:
                continue
            icon = _square(img, size)
            buf = io.BytesIO()
            icon.save(buf, format='PNG', optimize=True)
            _save_field(brand, field_name, buf.getvalue(), f'{field_name}-{size}.png')
            generated.append(field_name)

        if 'favicon' not in manual:
            buf = io.BytesIO()
            _square(img, 48).save(buf, format='ICO', sizes=[(16, 16), (32, 32), (48, 48)])
            _save_field(brand, 'favicon', buf.getvalue(), 'favicon.ico')
            generated.append('favicon')

        if 'og_image' not in manual:
            from PIL import Image as _Image
            canvas = _Image.new('RGBA', (1200, 630), (255, 255, 255, 255))
            logo = _square(img, 420)
            canvas.paste(logo, ((1200 - 420) // 2, (630 - 420) // 2), logo)
            buf = io.BytesIO()
            canvas.convert('RGB').save(buf, format='JPEG', quality=88, optimize=True)
            _save_field(brand, 'og_image', buf.getvalue(), 'og-image.jpg')
            generated.append('og_image')

        if 'logo' not in manual:
            # 没有手动上传 Logo 时，用裁剪后的源图生成页面 Logo
            buf = io.BytesIO()
            _square(img, 512).save(buf, format='PNG', optimize=True)
            _save_field(brand, 'logo', buf.getvalue(), 'logo.png')
            generated.append('logo')

    except Exception as e:  # noqa: BLE001
        logger.exception('生成域名品牌图标失败: %s', brand.domain)
        return False, f'生成失败：{e}', generated

    if generated:
        brand.save(update_fields=generated + ['updated_time'])
    return True, f'已生成 {len(generated)} 个资源', generated


# =============================================================================
# HTML 注入
# =============================================================================

_ICON_LINK_RE = re.compile(
    r'<link\b[^>]*\brel\s*=\s*["\'](?:icon|shortcut icon|apple-touch-icon'
    r'|apple-touch-icon-precomposed|mask-icon)["\'][^>]*>',
    re.IGNORECASE,
)

_SEO_META_RE = re.compile(
    r'<meta\b[^>]*\b(?:property|name)\s*=\s*["\'](?:og:image|og:image:width'
    r'|og:image:height|og:site_name|twitter:image|twitter:card)["\'][^>]*>',
    re.IGNORECASE,
)

_LOGO_CONTAINER_RE = re.compile(
    r'<(a|div|span|h1|p|li)\b([^>]*\bclass\s*=\s*["\'][^"\']*(?:logo|brand)[^"\']*["\'][^>]*)>'
    r'(.*?)</\1>',
    re.IGNORECASE | re.DOTALL,
)

_LOGO_IMG_RE = re.compile(
    r'<img\b[^>]*\b(?:class|id|alt)\s*=\s*["\'][^"\']*(?:logo|brand)[^"\']*["\'][^>]*>',
    re.IGNORECASE,
)

_HEADER_RE = re.compile(r'<header\b[^>]*>.*?</header>', re.IGNORECASE | re.DOTALL)
_SVG_RE = re.compile(r'<svg\b.*?</svg>', re.IGNORECASE | re.DOTALL)
_IMG_RE = re.compile(r'<img\b[^>]*>', re.IGNORECASE)


def _attr(tag, name):
    m = re.search(rf'\b{name}\s*=\s*["\']([^"\']*)["\']', tag, re.IGNORECASE)
    return m.group(1) if m else ''


def _set_img_src(tag, url):
    """替换 img 标签的 src，并去掉 srcset（避免浏览器仍用旧图）。"""
    tag = re.sub(r'\ssrcset\s*=\s*["\'][^"\']*["\']', '', tag, flags=re.IGNORECASE)
    tag = re.sub(r'\ssizes\s*=\s*["\'][^"\']*["\']', '', tag, flags=re.IGNORECASE)
    if re.search(r'\ssrc\s*=\s*["\'][^"\']*["\']', tag, flags=re.IGNORECASE):
        return re.sub(r'(\ssrc\s*=\s*)["\'][^"\']*["\']', rf'\1"{url}"', tag, count=1, flags=re.IGNORECASE)
    return tag[:-1].rstrip('/') + f' src="{url}">' if tag.endswith('>') else tag


def _build_logo_img(url, alt):
    return f'<img src="{url}" alt="{alt}" class="xy-brand-logo" data-xy-brand="logo">'


def _replace_page_logo(html, brand, logo_url):
    """把页面内可见的品牌 Logo 换成配置的图片。

    优先级：显式选择器 → 带 logo/brand 类的 img → 带 logo/brand 类的容器 → header 内首个 svg/img
    """
    alt = brand.brand_name or brand.domain

    # 1) 显式选择器（仅支持 #id 与 .class 的简单形式）
    selector = (brand.logo_selector or '').strip()
    if selector and selector[0] in '#.':
        marker = selector[1:].strip()
        attr = 'id' if selector[0] == '#' else 'class'
        pattern = re.compile(
            rf'<(\w+)\b[^>]*\b{attr}\s*=\s*["\'][^"\']*\b{re.escape(marker)}\b[^"\']*["\'][^>]*>',
            re.IGNORECASE,
        )
        m = pattern.search(html)
        if m and m.group(1).lower() == 'img':
            return html[:m.start()] + _set_img_src(m.group(0), logo_url) + html[m.end():]
        if m:
            close = f'</{m.group(1)}>'
            end = html.find(close, m.end())
            if end != -1:
                return html[:m.end()] + _build_logo_img(logo_url, alt) + html[end:]

    # 2) 带 logo / brand 特征的 img
    m = _LOGO_IMG_RE.search(html)
    if m:
        return html[:m.start()] + _set_img_src(m.group(0), logo_url) + html[m.end():]

    # 3) 带 logo / brand 类的容器（如 <a class="logo">文字</a>）
    for m in _LOGO_CONTAINER_RE.finditer(html):
        inner = m.group(3) or ''
        if len(inner) > 3000:
            continue
        return html[:m.start(3)] + _build_logo_img(logo_url, alt) + html[m.end(3):]

    # 4) header 里的首个 svg / img
    header = _HEADER_RE.search(html)
    if header:
        block = header.group(0)
        svg = _SVG_RE.search(block)
        if svg:
            new_block = block[:svg.start()] + _build_logo_img(logo_url, alt) + block[svg.end():]
            return html[:header.start()] + new_block + html[header.end():]
        img = _IMG_RE.search(block)
        if img:
            new_block = block[:img.start()] + _set_img_src(img.group(0), logo_url) + block[img.end():]
            return html[:header.start()] + new_block + html[header.end():]

    return html


def build_head_tags(brand, host):
    """生成 favicon / apple-touch / OG / JSON-LD 等标签。"""
    import json

    tags = [
        f'<link rel="icon" type="image/x-icon" href="{brand.asset_url("favicon")}">'
        if brand.favicon else '',
        f'<link rel="icon" type="image/png" sizes="32x32" href="{brand.asset_url("icon_32")}">'
        if brand.icon_32 else '',
        f'<link rel="icon" type="image/png" sizes="192x192" href="{brand.asset_url("icon_192")}">'
        if brand.icon_192 else '',
        f'<link rel="apple-touch-icon" sizes="180x180" href="{brand.asset_url("icon_180")}">'
        if brand.icon_180 else '',
    ]

    # 必应站点验证 Meta 标签（与 /BingSiteAuth.xml 同时提供）
    bing_code = normalize_bing_code(brand.bing_verify_code)
    if bing_code:
        tags.append(
            '<meta name="msvalidate.01" content="{}">'.format(
                html_lib.escape(bing_code, quote=True)
            )
        )

    if brand.seo_enabled and brand.og_image:
        og = brand.asset_url('og_image', absolute=True, host=host)
        name = brand.brand_name or brand.domain
        tags += [
            f'<meta property="og:image" content="{og}">',
            '<meta property="og:image:width" content="1200">',
            '<meta property="og:image:height" content="630">',
            f'<meta property="og:site_name" content="{name}">',
            f'<meta name="twitter:image" content="{og}">',
            '<meta name="twitter:card" content="summary_large_image">',
        ]
        if brand.icon_512:
            ld = {
                '@context': 'https://schema.org',
                '@type': 'Organization',
                'name': name,
                'url': f'https://{host}/',
                'logo': brand.asset_url('icon_512', absolute=True, host=host),
            }
            tags.append(
                '<script type="application/ld+json">'
                + json.dumps(ld, ensure_ascii=False)
                + '</script>'
            )

    return BRAND_STYLE + ''.join(t for t in tags if t)


_BRAND_STYLE_RE = re.compile(
    r'<style\s+id=["\']xy-brand-style["\'][^>]*>.*?</style>',
    re.IGNORECASE | re.DOTALL,
)


def apply_brand_to_html(html, host=None, brand=None):
    """把域名品牌注入 HTML；无配置时原样返回。"""
    if not html:
        return html
    try:
        brand = brand or DomainBrand.resolve(host)
        if not brand or not brand.enabled:
            return html

        host = DomainBrand.normalize_domain(host) or brand.domain

        # 清掉上一次注入的样式与旧图标/分享图/必应 Meta，保证可重复执行
        html = _BRAND_STYLE_RE.sub('', html)
        html = _ICON_LINK_RE.sub('', html)
        html = _BING_META_RE.sub('', html)
        if brand.seo_enabled:
            html = _SEO_META_RE.sub('', html)

        head_tags = build_head_tags(brand, host)
        lower = html.lower()
        pos = lower.rfind('</head>')
        if pos != -1:
            html = html[:pos] + head_tags + html[pos:]
        else:
            html = head_tags + html

        if brand.replace_page_logo and brand.logo:
            html = _replace_page_logo(html, brand, brand.asset_url('logo'))

        return html
    except Exception:  # noqa: BLE001
        logger.exception('注入域名品牌失败: %s', host)
        return html


# =============================================================================
# 资源响应
# =============================================================================

_ASSET_CONTENT_TYPES = {
    'favicon': 'image/x-icon',
    'logo': 'image/png',
    'icon_32': 'image/png',
    'icon_180': 'image/png',
    'icon_192': 'image/png',
    'icon_512': 'image/png',
}


def brand_asset_file(brand, path):
    """按稳定路径返回 (FieldFile, content_type)，不存在返回 (None, None)。"""
    field_name = DomainBrand.ASSET_PATHS.get(path)
    if not field_name:
        return None, None

    field = getattr(brand, field_name, None)
    if not field:
        return None, None

    content_type = _ASSET_CONTENT_TYPES.get(field_name)
    if not content_type:
        name = (field.name or '').lower()
        content_type = 'image/jpeg' if name.endswith(('.jpg', '.jpeg')) else 'image/png'
    return field, content_type


def brand_asset_response(brand, path):
    """构造图标/Logo 的文件响应，带长缓存。"""
    field, content_type = brand_asset_file(brand, path)
    if not field:
        raise Http404('资源不存在')
    try:
        response = FileResponse(field.open('rb'), content_type=content_type)
    except (OSError, ValueError):
        raise Http404('资源不存在')
    response['Cache-Control'] = 'public, max-age=86400'
    response['Access-Control-Allow-Origin'] = '*'
    return response
