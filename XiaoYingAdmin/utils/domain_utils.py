"""
域名解析工具集 — 根域名提取、子域名判断、域名分组。

用于页面列表树形视图中按根域名聚合展示所有子域名。
"""

from typing import Optional


# ---------------------------------------------------------------------------
# 辅助：域名清洗
# ---------------------------------------------------------------------------

def _clean_domain(domain_str: str) -> str:
    """去掉端口、通配符等杂质，返回纯净域名。"""
    if not domain_str or not isinstance(domain_str, str):
        return ''
    domain = domain_str.split(':')[0]   # 去掉端口
    domain = domain.lstrip('*.')        # 去掉通配符前缀
    return domain


def _is_ip_or_local(domain: str) -> bool:
    """判断是否为 IP 地址或 localhost。"""
    if domain in ('localhost', '0.0.0.0'):
        return True
    parts = domain.split('.')
    if len(parts) == 4:
        try:
            return all(0 <= int(p) <= 255 for p in parts)
        except (ValueError, TypeError):
            pass
    return False


# ---------------------------------------------------------------------------
# 智能父域名查找（核心逻辑）
# ---------------------------------------------------------------------------

def _find_parent_domain(domain: str, candidates: list) -> str:
    """
    在 candidates 中查找 domain 的父域名。

    父域名定义：domain 以 ".父域名" 结尾，且父域名在 candidates 中存在。
    如果有多个候选，取"最短"的那个（即最近的上层域名）。

    若找不到父域名，返回 domain 自身（它自己就是根）。

    示例：
      domain = 'time.app-xiaoying.hl.cn'
      candidates = ['app-xiaoying.hl.cn', 'static.app-xiaoying.hl.cn', 'example.com']
      → 返回 'app-xiaoying.hl.cn'

      domain = 'app-xiaoying.hl.cn'
      candidates 中无更长后缀匹配
      → 返回 'app-xiaoying.hl.cn'（自身即为根）
    """
    cleaned = _clean_domain(domain)
    if _is_ip_or_local(cleaned):
        return domain

    best_parent = domain       # 默认自己就是根
    best_len = len(cleaned)

    for cand in candidates:
        c_clean = _clean_domain(cand)
        if c_clean == cleaned:
            continue
        # domain 以 ".candidate" 结尾 → candidate 是父域名
        dot_candidate = '.' + c_clean
        if cleaned.endswith(dot_candidate):
            # 选最短的那个（最直接的父域名）
            if len(c_clean) < best_len:
                best_parent = cand
                best_len = len(c_clean)

    return best_parent


def find_root_domain(domain: str, all_domains: list) -> str:
    """
    根据域名列表，找到 domain 的最终根域名（递归向上查）。

    如果 domain 本身就是根（没有父域名），返回 domain 自身。
    如果 all_domains 为空，返回 domain 自身。

    示例：
      all_domains = ['example.com', 'www.example.com', 'blog.www.example.com']
      find_root_domain('blog.www.example.com', all_domains) → 'example.com'
    """
    if not all_domains:
        return domain

    visited = set()
    current = domain
    while True:
        parent = _find_parent_domain(current, all_domains)
        if parent == current:
            return current
        if parent in visited:
            return current  # 循环保护
        visited.add(parent)
        current = parent


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------

def extract_root_domain(domain_str: str) -> str:
    """
    简单方式从完整域名中提取根域名（最后两段）。
    注意：此函数已被 group_domains_by_root 替代，后者使用智能父域名查找。
    保留此函数仅用于向后兼容（智能互链等功能仍在使用）。

    示例：
      extract_root_domain('www.example.com') → 'example.com'
      extract_root_domain('time.app-xiaoying.hl.cn') → 'hl.cn'  （不符合预期！）
    """
    if not domain_str or not isinstance(domain_str, str):
        return domain_str or ''

    domain = _clean_domain(domain_str)

    if _is_ip_or_local(domain):
        return domain

    if domain.startswith('www.'):
        domain = domain[4:]

    parts = domain.split('.')
    if len(parts) >= 2:
        return '.'.join(parts[-2:])
    return domain


def is_subdomain(domain_str: str) -> bool:
    """
    判断一个域名是否为子域名（采用父域名逻辑）。
    如果一个域名在候选列表中有父域名，则它是子域名。

    注意：此函数需要传入候选列表，若仅传入单域名，则按层级判断。
    """
    if not domain_str or not isinstance(domain_str, str):
        return False
    domain = _clean_domain(domain_str)
    if _is_ip_or_local(domain):
        return False
    parts = domain.split('.')
    return len(parts) >= 3


def _normalize_domain(domain_str: str) -> str:
    """清洗域名用于比较：去端口、去通配符、转小写。"""
    return _clean_domain(domain_str).lower()


def group_domains_by_root(domains: list) -> dict:
    """
    将域名列表按根域名智能分组。

    采用「父域名后缀匹配」算法，不依赖固定取最后两段，
    因此能正确处理 app-xiaoying.hl.cn 这类多个级次的域名。

    输入:
      [
        'app-xiaoying.hl.cn',
        'time.app-xiaoying.hl.cn',
        'static.app-xiaoying.hl.cn',
        'www.example.com',
        'example.com',
      ]

    输出:
      {
        'app-xiaoying.hl.cn': [
          'app-xiaoying.hl.cn',
          'static.app-xiaoying.hl.cn',
          'time.app-xiaoying.hl.cn',
        ],
        'example.com': [
          'example.com',
          'www.example.com',
        ],
      }

    规则:
      - 每个域名在其根域名分组中都会包含自身
      - 分组内按字母排序
      - IP / localhost 保持自身为根
      - 端口/通配符/大小写差异会自动归一化
      - 当根域名不在列表中时（只有子域名在），自动推断虚拟根域名：
        对 3+ 段的域名去掉最左边一段得到候选根域名，
        仅当被 2+ 个域名共享时才作为虚拟根域名（避免把 com.cn 等 TLD 当根域名）
    """
    if not domains:
        return {}

    # 第一步：去空，保留原始域名
    raw_list = [d for d in domains if d and isinstance(d, str) and d.strip()]
    if not raw_list:
        return {}

    # 第二步：建立 归一化名 → [原始域名] 的映射
    norm_to_raws: dict = {}
    for d in raw_list:
        norm = _normalize_domain(d)
        norm_to_raws.setdefault(norm, []).append(d)

    # 第二步半：推断虚拟根域名
    # 当根域名本身不在列表中（只有子域名在），子域名无法被分组。
    # 对每个 3+ 段的域名，去掉最左边一段得到候选根域名。
    # 仅当候选根域名被 2+ 个域名共享时，才添加为虚拟根域名。
    # 这样 api.web-apply-whatsapp.com.cn 和 m.web-apply-whatsapp.com.cn
    # 会被分组到虚拟根域名 web-apply-whatsapp.com.cn 下。
    parent_count: dict = {}  # {候选根域名: 共享的域名数量}
    for d in raw_list:
        cleaned = _clean_domain(d).lower()
        if _is_ip_or_local(cleaned):
            continue
        parts = cleaned.split('.')
        if len(parts) > 2:  # 只有 3+ 段的域名才推断父域名
            parent = '.'.join(parts[1:])
            parent_count[parent] = parent_count.get(parent, 0) + 1

    # 虚拟父域名集合：不在原始列表中，但被 2+ 个域名共享
    virtual_parents: set = set()
    for parent, count in parent_count.items():
        if count >= 2 and parent not in norm_to_raws:
            virtual_parents.add(parent)

    # 将虚拟父域名加入 norm_to_raws（用于父域名查找）
    for vp in virtual_parents:
        norm_to_raws[vp] = [vp]  # 虚拟父域名的"原始名"用自身

    # 归一化名列表（去重，包含虚拟父域名）
    norm_names = sorted(norm_to_raws.keys())

    # 第三步：找出每个归一化名的父域名（基于归一化名）
    parent_of = {}
    for nd in norm_names:
        parent_of[nd] = _find_parent_domain(nd, norm_names)

    # 第四步：收集根域名并构建分组（使用原始域名的 first occurrence 作为分组 key）
    groups: dict = {}
    # 先把根域名对应的原始名作为 key
    for nd in norm_names:
        root_norm = parent_of[nd]
        root_key = norm_to_raws[root_norm][0]  # 根域名用第一个原始字符串
        if root_key not in groups:
            groups[root_key] = []
        # 虚拟父域名不加入分组内的域名列表（它只是一个分组 key，不是真实页面域名）
        if nd in virtual_parents:
            continue
        # 子域名也用第一个原始字符串
        groups[root_key].append(norm_to_raws[nd][0])

    # 确保每个分组内按字母排序
    for root in groups:
        groups[root].sort()

    # 确保根域名自己在分组中排在第一个
    for root_key in groups:
        raw_root_lower = _normalize_domain(root_key)
        # 把根域名自己移到最前面
        for d in groups[root_key]:
            if _normalize_domain(d) == raw_root_lower and d != root_key:
                groups[root_key].remove(d)
                groups[root_key].insert(0, d)
                break

    return groups


def _find_immediate_parent(domain: str, candidates: list) -> str:
    """
    在 candidates 中查找 domain 的直接父域名（取最长匹配）。

    与 _find_parent_domain 不同，此函数返回最近的上层域名（最长匹配字符串），
    而非最远的根域名（最短匹配）。用于构建多层级域名树。

    示例：
      domain = 'sub.www.example.com'
      candidates = ['example.com', 'www.example.com']
      _find_parent_domain  → 返回 'example.com'（最短匹配，根域名）
      _find_immediate_parent → 返回 'www.example.com'（最长匹配，直接父域名）
    """
    cleaned = _clean_domain(domain)
    if _is_ip_or_local(cleaned):
        return domain

    best_parent = domain  # 默认自己就是根
    best_len = 0

    for cand in candidates:
        c_clean = _clean_domain(cand)
        if c_clean == cleaned:
            continue
        dot_candidate = '.' + c_clean
        if cleaned.endswith(dot_candidate):
            # 选最长的那个（最近的父域名）
            if len(c_clean) > best_len:
                best_parent = cand
                best_len = len(c_clean)
    return best_parent


def build_domain_hierarchy(group_members: list) -> list:
    """
    将扁平分组成员域名列表构建为多层级树结构。

    输入: ['a.com', 'www.a.com', 'sub.www.a.com', 'deep.sub.www.a.com']
    输出:
    [{
        'domain': 'a.com',
        'children': [{
            'domain': 'www.a.com',
            'children': [{
                'domain': 'sub.www.a.com',
                'children': [{
                    'domain': 'deep.sub.www.a.com',
                    'children': []
                }]
            }]
        }]
    }]

    规则：
      - 按域名层级排序（点数少的优先作为父域名）
      - 每个域名找其直接父域名（最长匹配后缀）
      - 返回顶层节点列表
    """
    if not group_members:
        return []

    # 按域名层级排序（点少的在前，父域名排在前面）
    sorted_members = sorted(set(group_members), key=lambda d: d.count('.'))

    # 构建节点映射 {domain: {'domain': d, 'children': []}}
    nodes = {}
    for d in sorted_members:
        nodes[d] = {'domain': d, 'children': []}

    # 为每个域名找直接父域名并挂载
    for d in sorted_members:
        parent = _find_immediate_parent(d, sorted_members)
        if parent != d:
            nodes[parent]['children'].append(nodes[d])

    # 返回所有顶层节点（父域名是自己的）
    return [nodes[d] for d in sorted_members
            if _find_immediate_parent(d, sorted_members) == d]
