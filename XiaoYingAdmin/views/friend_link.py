"""
友情链接管理 API。

提供友情链接的列表、添加、删除接口，
用于在单页面管理列表中维护自定义友情链接（标题 + URL）。

友情链接支持配置应用范围（全部页面 / 指定分类），
添加或删除时自动同步到目标页面的 HTML 中。

与智能互链共用同一个"友情链接"块：
  - 页面已有智能互链块 → 自定义友链直接追加进该块（带 data-fl 标记，可区分）
  - 页面没有互链块 → 新建一个块（样式与智能互链一致）
"""

import re

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from XiaoYingAdmin.common.http import err, get_or_404, parse_json_body
from XiaoYingAdmin.middleware.operation_log import log_operation
from XiaoYingAdmin.models.friend_link import FriendLink

# ---------------------------------------------------------------------------
# 友情链接块标记（与智能互链 request.py 中保持一致，共用同一个块）
# ---------------------------------------------------------------------------
_CROSSLINK_TAG_START = '<!-- ====== 智能互链 ====== -->'
_CROSSLINK_TAG_END = '<!-- ====== /智能互链 ====== -->'


def _escape_attr(s: str) -> str:
    """HTML 属性转义。"""
    return str(s).replace('&', '&amp;').replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')


def _friend_link_item_html(link) -> str:
    """生成单个自定义友链 <a>（带 data-fl 标记，便于与智能互链链接区分）。"""
    return (
        f'      <a href="{_escape_attr(link.url)}" '
        f'title="{_escape_attr(link.title)}" '
        f'rel="friend" target="_blank" data-fl="1">{_escape_attr(link.title)}</a>'
    )


def _build_block_html(existing_items: list, links: list) -> str:
    """
    生成完整的友情链接块（样式与智能互链一致）。

    existing_items: 块中已存在的智能互链 <a> 标签字符串列表
    links: 本次要注入的自定义友情链接 [FriendLink, ...]
    """
    items = list(existing_items)
    items += [_friend_link_item_html(link) for link in links]
    if not items:
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
        + '\n'.join(items) + '\n'
        '  </div>\n'
        '</div>\n'
        f'{_CROSSLINK_TAG_END}\n'
    )


def _apply_friend_link_block(html: str, links: list) -> str:
    """
    将自定义友情链接追加/移除到页面的友情链接块中。

    - 页面已有块 → 保留块中智能互链链接，追加当前自定义友链（带 data-fl）
    - 页面没有块 → 有友链则新建块；无友链则不变
    - 块内无任何链接 → 移除整个块
    """
    start_idx = html.find(_CROSSLINK_TAG_START)
    end_idx = html.find(_CROSSLINK_TAG_END)

    # 页面没有友情链接块
    if start_idx == -1 or end_idx == -1:
        if not links:
            return html
        new_block = _build_block_html([], links)
        body_idx = html.lower().rfind('</body>')
        if body_idx != -1:
            return html[:body_idx] + new_block + '\n' + html[body_idx:]
        return html + '\n' + new_block

    end_idx += len(_CROSSLINK_TAG_END)
    block = html[start_idx:end_idx]
    # 提取块中已有的智能互链链接（排除带 data-fl 的自定义友链）
    existing = [
        it for it in re.findall(r'<a\b[^>]*>.*?</a>', block, flags=re.DOTALL)
        if 'data-fl' not in it
    ]
    new_block = _build_block_html(existing, links)
    # 块内无任何链接 → 移除整个块
    if not new_block:
        return html[:start_idx] + html[end_idx:]
    # 替换已有块时去掉块首尾换行（周围已自带换行），保证重复同步幂等
    return html[:start_idx] + new_block.strip('\n') + html[end_idx:]


def sync_friend_links_to_pages(pages=None) -> dict:
    """
    全量同步友情链接到页面 HTML。

    对每个页面重新计算其应展示的友情链接（scope=all 的全局友链 +
    页面所属分类匹配的友链），并追加/移除页面友情链接块中的自定义友链。
    幂等操作：无论调用多少次，结果一致。

    pages: 可选，只同步指定页面（页面保存/更新后调用）。
           None 时同步所有页面。

    返回: {"updated": int, "total": int}
    """
    from XiaoYingAdmin.models.generated_page import GeneratedPage

    # 1. 收集所有友链，按范围分组
    all_links = list(FriendLink.objects.select_related('category').all())
    global_links = [l for l in all_links if l.scope == FriendLink.SCOPE_ALL]
    links_by_cat = {}
    for l in all_links:
        if l.scope == FriendLink.SCOPE_CATEGORY and l.category_id:
            links_by_cat.setdefault(l.category_id, []).append(l)

    # 2. 遍历页面（pages 为列表时直接遍历；None 时遍历全量查询集）
    if pages is not None:
        page_iter = pages
    else:
        page_iter = GeneratedPage.objects.all().iterator()

    updated = 0
    total = 0
    for page in page_iter:
        total += 1
        # 收集该页面应展示的友链（去重）
        merged = list(global_links)
        for cat_id in page.categories.values_list('id', flat=True):
            merged.extend(links_by_cat.get(cat_id, []))
        seen = set()
        final_links = []
        for l in merged:
            if l.id not in seen:
                seen.add(l.id)
                final_links.append(l)

        new_html = _apply_friend_link_block(page.html_content, final_links)
        if new_html != page.html_content:
            page.html_content = new_html
            page.save(update_fields=['html_content', 'updated_time'])
            updated += 1

    return {'updated': updated, 'total': total}


@require_GET
def friend_link_list(request):
    """获取友情链接列表。响应: {"items": [{...}]}"""
    items = FriendLink.objects.select_related('category').all()
    return JsonResponse({'items': [l.to_dict() for l in items]})


@csrf_exempt
@require_POST
def friend_link_create(request):
    """
    添加友情链接。

    请求: application/json
      {"title": "链接标题", "url": "https://example.com",
       "scope": "all" | "category", "category_id": 1}
    添加后自动同步注入到目标页面。
    """
    body, error = parse_json_body(request)
    if error is not None:
        return error

    title = (body.get('title') or '').strip()
    url = (body.get('url') or '').strip()
    scope = (body.get('scope') or FriendLink.SCOPE_ALL).strip()
    category_id = body.get('category_id')

    if not title:
        return err('链接标题不能为空')
    if not url:
        return err('链接 URL 不能为空')
    if scope not in (FriendLink.SCOPE_ALL, FriendLink.SCOPE_CATEGORY):
        return err('应用范围不合法')

    category = None
    if scope == FriendLink.SCOPE_CATEGORY:
        if not category_id:
            return err('请选择应用分类')
        from XiaoYingAdmin.models.page_category import PageCategory
        category, error = get_or_404(
            PageCategory, id=category_id, not_found_msg='分类不存在',
        )
        if error is not None:
            return error

    link = FriendLink.objects.create(title=title, url=url, scope=scope, category=category)

    # 同步注入到目标页面
    sync_result = sync_friend_links_to_pages()

    log_operation(request, 'create', 'FriendLink', link.id,
                  f'添加友情链接「{link.title}」',
                  detail={'changes': {
                      '标题': {'new': title},
                      'URL': {'new': url},
                      '应用范围': {'new': link.to_dict()['scope_label']},
                  }})

    return JsonResponse({
        'message': f'添加成功，已同步到 {sync_result["updated"]} 个页面',
        'link': link.to_dict(),
        'sync': sync_result,
    })


@csrf_exempt
@require_POST
def friend_link_delete(request):
    """
    删除友情链接。

    请求: application/json
      {"id": 1}
    删除后自动从所有页面中移除该友链。
    """
    body, error = parse_json_body(request)
    if error is not None:
        return error

    link, error = get_or_404(FriendLink, id=body.get('id'), not_found_msg='友情链接不存在')
    if error is not None:
        return error

    title = link.title
    link.delete()

    # 同步移除该友链
    sync_result = sync_friend_links_to_pages()

    log_operation(request, 'delete', 'FriendLink', link.id,
                  f'删除友情链接「{title}」')

    return JsonResponse({
        'message': f'删除成功，已同步 {sync_result["updated"]} 个页面',
        'sync': sync_result,
    })
