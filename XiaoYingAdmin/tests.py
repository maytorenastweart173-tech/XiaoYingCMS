"""
自动备份功能 + 域名SEO记录树形展示 测试

运行方式: python manage.py test XiaoYingAdmin.tests --verbosity=2
"""
import json
import os
import shutil
import tempfile

from django.conf import settings
from django.test import TestCase, RequestFactory

from XiaoYingAdmin.models.operation_log import OperationLog
from XiaoYingAdmin.models.site_settings import SiteSettings
from XiaoYingAdmin.models.seo_domain import SeoDomain
from XiaoYingAdmin.models.spider_log import SpiderAccessLog
from XiaoYingAdmin.utils.backup import (
    check_and_auto_backup,
    get_backup_dir,
    list_backup_files,
)
from XiaoYingAdmin.views.seo.domain_records import api_seo_domains_sync, api_seo_domains_tree


class TestCheckAndAutoBackup(TestCase):
    """测试 check_and_auto_backup 函数"""

    def setUp(self):
        # 使用临时目录作为备份目录
        self.temp_dir = tempfile.mkdtemp()
        self._orig_backup_dir = getattr(settings, 'BACKUP_DIR', None)
        settings.BACKUP_DIR = self.temp_dir

        # 确保 SiteSettings 存在
        SiteSettings.objects.get_or_create(pk=1)

    def tearDown(self):
        # 清理临时目录
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        if self._orig_backup_dir is not None:
            settings.BACKUP_DIR = self._orig_backup_dir

    def _create_spider_logs(self, count: int):
        """批量创建蜘蛛日志"""
        for i in range(count):
            SpiderAccessLog.objects.create(
                ip='127.0.0.1',
                user_agent=f'test-bot-{i}',
                path=f'/test/path/{i}',
            )

    def _create_operation_logs(self, count: int):
        """批量创建操作日志"""
        for i in range(count):
            OperationLog.objects.create(
                username=f'testuser-{i}',
                action='create',
                target_type='Test',
                target_id=str(i),
                target_repr=f'测试操作 {i}',
            )

    # ================================================================
    # 蜘蛛日志 - auto_backup_spider_threshold
    # ================================================================

    def test_spider_threshold_zero_no_backup(self):
        """阈值=0 时，不触发自动备份"""
        self._create_spider_logs(10)
        ss = SiteSettings.objects.get(pk=1)
        ss.auto_backup_spider_threshold = 0
        ss.save()

        check_and_auto_backup(SpiderAccessLog, 'spider_logs', 'auto_backup_spider_threshold')

        self.assertEqual(SpiderAccessLog.objects.count(), 10,
                         '阈值=0 不应删除日志')
        self.assertEqual(len(list_backup_files('spider_logs')), 0,
                         '阈值=0 不应生成备份')

    def test_spider_threshold_not_reached(self):
        """未达到阈值时，不触发自动备份"""
        self._create_spider_logs(5)
        ss = SiteSettings.objects.get(pk=1)
        ss.auto_backup_spider_threshold = 10
        ss.save()

        check_and_auto_backup(SpiderAccessLog, 'spider_logs', 'auto_backup_spider_threshold')

        self.assertEqual(SpiderAccessLog.objects.count(), 5,
                         '未达阈值不应删除数据')

    def test_spider_threshold_exact_match(self):
        """日志数刚好等于阈值时，触发自动备份"""
        self._create_spider_logs(10)
        ss = SiteSettings.objects.get(pk=1)
        ss.auto_backup_spider_threshold = 10
        ss.save()

        check_and_auto_backup(SpiderAccessLog, 'spider_logs', 'auto_backup_spider_threshold')

        self.assertEqual(SpiderAccessLog.objects.count(), 0,
                         '达到阈值应清空日志')
        files = list_backup_files('spider_logs')
        self.assertEqual(len(files), 1,
                         '应生成一个备份文件')
        self.assertGreater(files[0]['size_bytes'], 0,
                           '备份文件不应为空')

    def test_spider_threshold_exceeded(self):
        """日志数超过阈值时，也应触发自动备份"""
        self._create_spider_logs(15)
        ss = SiteSettings.objects.get(pk=1)
        ss.auto_backup_spider_threshold = 10
        ss.save()

        check_and_auto_backup(SpiderAccessLog, 'spider_logs', 'auto_backup_spider_threshold')

        self.assertEqual(SpiderAccessLog.objects.count(), 0,
                         '超过阈值应清空日志')
        files = list_backup_files('spider_logs')
        self.assertEqual(len(files), 1,
                         '应生成一个备份文件')

    # ================================================================
    # 操作日志 - auto_backup_operation_threshold
    # ================================================================

    def test_operation_threshold_zero_no_backup(self):
        """阈值=0 时，不触发自动备份"""
        self._create_operation_logs(10)
        ss = SiteSettings.objects.get(pk=1)
        ss.auto_backup_operation_threshold = 0
        ss.save()

        check_and_auto_backup(OperationLog, 'op_logs', 'auto_backup_operation_threshold')

        self.assertEqual(OperationLog.objects.count(), 10,
                         '阈值=0 不应删除日志')
        self.assertEqual(len(list_backup_files('op_logs')), 0,
                         '阈值=0 不应生成备份')

    def test_operation_threshold_exact_match(self):
        """操作日志数刚好等于阈值时，触发自动备份"""
        self._create_operation_logs(10)
        ss = SiteSettings.objects.get(pk=1)
        ss.auto_backup_operation_threshold = 10
        ss.save()

        check_and_auto_backup(OperationLog, 'op_logs', 'auto_backup_operation_threshold')

        self.assertEqual(OperationLog.objects.count(), 0,
                         '达到阈值应清空操作日志')
        files = list_backup_files('op_logs')
        self.assertEqual(len(files), 1,
                         '应生成一个操作日志备份文件')


class TestSeoDomainTree(TestCase):
    """测试域名SEO记录的树形展示 API"""

    def setUp(self):
        self.factory = RequestFactory()

    def _collect_all_domains(self, nodes):
        """递归收集树中所有域名字符串"""
        result = set()
        for node in nodes:
            result.add(node['domain'])
            if node.get('children'):
                result.update(self._collect_all_domains(node['children']))
        return result

    def _find_node(self, nodes, domain):
        """在树中递归查找域名节点"""
        for node in nodes:
            if node['domain'] == domain:
                return node
            if node.get('children'):
                found = self._find_node(node['children'], domain)
                if found:
                    return found
        return None

    def test_subdomains_with_virtual_root_appear_in_tree(self):
        """
        复现 bug：多个子域名共享一个虚拟根域名时，树形 API 不显示这些域名。

        场景：同步了两个域名 m.web-apply-whatsapp.com.cn 和
        www.web-apply-whatsapp.com.cn，group_domains_by_root 会自动推断
        虚拟根域名 web-apply-whatsapp.com.cn，但该域名不在 SeoDomain 表中，
        导致树形 API 跳过整个分组。
        """
        # 创建两个子域名（模拟用户通过批量导入 zip 上传的）
        SeoDomain.objects.create(
            domain='m.web-apply-whatsapp.com.cn',
            domain_type='root',
            remark='来自批量导入',
        )
        SeoDomain.objects.create(
            domain='www.web-apply-whatsapp.com.cn',
            domain_type='root',
            remark='来自批量导入',
        )

        # 调用树形 API
        request = self.factory.get('/xiaoying_admin/api/seo/domains/tree/')
        response = api_seo_domains_tree(request)
        data = json.loads(response.content)

        self.assertTrue(data['ok'], f'API 应返回 ok=True，实际: {data}')

        # 检查 tree 中包含这两个域名
        all_domains_in_tree = self._collect_all_domains(data['tree'])
        self.assertIn(
            'm.web-apply-whatsapp.com.cn',
            all_domains_in_tree,
            f'树中应包含 m.web-apply-whatsapp.com.cn，实际树节点: {all_domains_in_tree}',
        )
        self.assertIn(
            'www.web-apply-whatsapp.com.cn',
            all_domains_in_tree,
            f'树中应包含 www.web-apply-whatsapp.com.cn，实际树节点: {all_domains_in_tree}',
        )

        # 验证虚拟根域名有效作为根节点展现，且子域名嵌套在它下面
        virtual_root = None
        for node in data['tree']:
            if node['domain'] == 'web-apply-whatsapp.com.cn':
                virtual_root = node
                break
        self.assertIsNotNone(
            virtual_root,
            '虚拟根域名 web-apply-whatsapp.com.cn 应作为根节点出现',
        )
        self.assertEqual(virtual_root['id'], 0, '虚拟根域名 id 应为 0')
        child_domains = [c['domain'] for c in (virtual_root.get('children') or [])]
        self.assertIn('m.web-apply-whatsapp.com.cn', child_domains)
        self.assertIn('www.web-apply-whatsapp.com.cn', child_domains)

    def test_single_subdomain_works(self):
        """单个子域名（没有虚拟根域名）应正常显示"""
        SeoDomain.objects.create(
            domain='m.web-apply-whatsapp.com.cn',
            domain_type='root',
        )

        request = self.factory.get('/xiaoying_admin/api/seo/domains/tree/')
        response = api_seo_domains_tree(request)
        data = json.loads(response.content)

        self.assertTrue(data['ok'])
        all_domains_in_tree = set()
        for node in data['tree']:
            all_domains_in_tree.add(node['domain'])
            for child in node.get('children', []):
                all_domains_in_tree.add(child['domain'])

        self.assertIn('m.web-apply-whatsapp.com.cn', all_domains_in_tree)


class TestBuildDomainHierarchy(TestCase):
    """测试 build_domain_hierarchy 多层级树构建"""

    def test_two_levels(self):
        """二级域名结构：a.com + www.a.com"""
        from XiaoYingAdmin.utils.domain_utils import build_domain_hierarchy
        result = build_domain_hierarchy(['a.com', 'www.a.com'])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['domain'], 'a.com')
        self.assertEqual(len(result[0]['children']), 1)
        self.assertEqual(result[0]['children'][0]['domain'], 'www.a.com')

    def test_three_levels(self):
        """三级域名结构：a.com > www.a.com > sub.www.a.com"""
        from XiaoYingAdmin.utils.domain_utils import build_domain_hierarchy
        result = build_domain_hierarchy(['a.com', 'www.a.com', 'sub.www.a.com'])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['domain'], 'a.com')
        self.assertEqual(len(result[0]['children']), 1)
        self.assertEqual(result[0]['children'][0]['domain'], 'www.a.com')
        self.assertEqual(len(result[0]['children'][0]['children']), 1)
        self.assertEqual(
            result[0]['children'][0]['children'][0]['domain'],
            'sub.www.a.com',
        )

    def test_four_levels(self):
        """四级域名结构：a.com > www.a.com > sub.www.a.com > deep.sub.www.a.com"""
        from XiaoYingAdmin.utils.domain_utils import build_domain_hierarchy
        result = build_domain_hierarchy([
            'a.com', 'www.a.com', 'sub.www.a.com', 'deep.sub.www.a.com',
        ])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['domain'], 'a.com')
        # 追溯最深层的域名
        deepest = result[0]['children'][0]['children'][0]['children'][0]
        self.assertEqual(deepest['domain'], 'deep.sub.www.a.com')

    def test_multiple_roots(self):
        """多根域名混合"""
        from XiaoYingAdmin.utils.domain_utils import build_domain_hierarchy
        result = build_domain_hierarchy([
            'a.com', 'www.a.com', 'b.com', 'api.b.com',
        ])
        self.assertEqual(len(result), 2)
        # 按 domain 排序
        result.sort(key=lambda n: n['domain'])
        self.assertEqual(result[0]['domain'], 'a.com')
        self.assertEqual(len(result[0]['children']), 1)
        self.assertEqual(result[1]['domain'], 'b.com')
        self.assertEqual(len(result[1]['children']), 1)


class TestSeoDomainTreeMultiLevel(TestCase):
    """测试多层级域名树的 API 返回"""

    def setUp(self):
        self.factory = RequestFactory()

    def _collect_all_domains(self, nodes):
        """递归收集树中所有域名字符串"""
        result = set()
        for node in nodes:
            result.add(node['domain'])
            if node.get('children'):
                result.update(self._collect_all_domains(node['children']))
        return result

    def _has_hierarchy(self, nodes, parent_domain, child_domain):
        """检查 parent_domain 下是否有直接的 child_domain 子节点"""
        for node in nodes:
            if node['domain'] == parent_domain and node.get('children'):
                for child in node['children']:
                    if child['domain'] == child_domain:
                        return True
                # 递归检查更深层
                for child in node['children']:
                    if self._has_hierarchy([child], parent_domain, child_domain):
                        return True
            elif node.get('children'):
                if self._has_hierarchy(node['children'], parent_domain, child_domain):
                    return True
        return False

    def test_multi_level_tree(self):
        """2级+3级+4级域名混合的树形展示"""
        # 根域名
        SeoDomain.objects.create(domain='example.com', domain_type='root')
        SeoDomain.objects.create(domain='test.org', domain_type='root')
        # 二级域名
        SeoDomain.objects.create(domain='www.example.com', domain_type='multi')
        SeoDomain.objects.create(domain='api.example.com', domain_type='multi')
        # 三级域名
        SeoDomain.objects.create(domain='sub.www.example.com', domain_type='multi')
        # 四级域名
        SeoDomain.objects.create(
            domain='deep.sub.www.example.com', domain_type='multi',
        )

        request = self.factory.get('/xiaoying_admin/api/seo/domains/tree/')
        response = api_seo_domains_tree(request)
        data = json.loads(response.content)
        self.assertTrue(data['ok'])

        # 所有域名都存在
        all_domains = self._collect_all_domains(data['tree'])
        for d in ['example.com', 'test.org', 'www.example.com',
                   'api.example.com', 'sub.www.example.com',
                   'deep.sub.www.example.com']:
            self.assertIn(d, all_domains, f'{d} 应在树中')

        # 检查层级结构：example.com is root
        example_root = None
        for node in data['tree']:
            if node['domain'] == 'example.com':
                example_root = node
                break
        self.assertIsNotNone(example_root, 'example.com 应作为根节点')
        self.assertIsNotNone(example_root['children'], 'example.com 应有子节点')

        # www.example.com 在 example.com 下
        www = None
        for child in example_root['children']:
            if child['domain'] == 'www.example.com':
                www = child
                break
        self.assertIsNotNone(www, 'www.example.com 应在 example.com 下')

        # sub.www.example.com 在 www.example.com 下
        sub = None
        for child in www['children']:
            if child['domain'] == 'sub.www.example.com':
                sub = child
                break
        self.assertIsNotNone(sub, 'sub.www.example.com 应在 www.example.com 下')

        # deep.sub.www.example.com 在 sub.www.example.com 下
        deep = None
        for child in sub['children']:
            if child['domain'] == 'deep.sub.www.example.com':
                deep = child
                break
        self.assertIsNotNone(
            deep,
            'deep.sub.www.example.com 应在 sub.www.example.com 下',
        )

    def test_virtual_root_two_levels(self):
        """虚拟根域名下仍然保留多层级结构"""
        # 创建两级子域名，但根域名不存在
        SeoDomain.objects.create(domain='www.example.com', domain_type='multi')
        SeoDomain.objects.create(domain='api.example.com', domain_type='multi')

        request = self.factory.get('/xiaoying_admin/api/seo/domains/tree/')
        response = api_seo_domains_tree(request)
        data = json.loads(response.content)
        self.assertTrue(data['ok'])

        all_domains = self._collect_all_domains(data['tree'])
        for d in ['www.example.com', 'api.example.com']:
            self.assertIn(d, all_domains, f'{d} 应在树中')

        # 验证虚拟根域名 example.com 作为根节点出现
        virtual_root = None
        for node in data['tree']:
            if node['domain'] == 'example.com':
                virtual_root = node
                break
        self.assertIsNotNone(virtual_root, '虚拟根域名 example.com 应作为根节点出现')
        child_domains = [c['domain'] for c in (virtual_root.get('children') or [])]
        self.assertIn('www.example.com', child_domains)
        self.assertIn('api.example.com', child_domains)


class TestPageTreeDomainGrouping(TestCase):
    """页面列表树形视图中域名分组正确性"""

    def test_com_cn_domains_not_grouped_under_com_cn(self):
        """多个 *.com.cn 域名不应被错误分组到 2 段虚拟根域名 com.cn 下"""
        from XiaoYingAdmin.views.page_tree import _build_domain_tree
        from XiaoYingAdmin.models.generated_page import GeneratedPage

        # 模拟线上：多个独立的 *.com.cn 域名，其中 new-xiaoying.com.cn 有子域名
        pages_data = [
            {'name': 'page1', 'domains': ['new-xiaoying.com.cn']},
            {'name': 'page2', 'domains': ['app.new-xiaoying.com.cn']},
            {'name': 'page3', 'domains': ['im.new-xiaoying.com.cn']},
            {'name': 'page4', 'domains': ['help-xiaoying-chat.com.cn']},
            {'name': 'page5', 'domains': ['m-xiaoying-chat.com.cn']},
        ]
        pages = [GeneratedPage(name=d['name'], domains=d['domains']) for d in pages_data]
        result = _build_domain_tree(pages)

        # 不应出现 com.cn 作为根域名
        root_names = [r['root'] for r in result]
        self.assertNotIn('com.cn', root_names, 'com.cn 不应作为根域名出现')

        # new-xiaoying.com.cn 应作为根域名，且包含 app. 和 im. 子域名
        root_new = None
        for r in result:
            if r['root'] == 'new-xiaoying.com.cn':
                root_new = r
                break
        self.assertIsNotNone(root_new, 'new-xiaoying.com.cn 应作为根域名')
        child_domains = [d['domain'] for d in root_new['domains']]
        self.assertIn('app.new-xiaoying.com.cn', child_domains)
        self.assertIn('im.new-xiaoying.com.cn', child_domains)
        self.assertIn('new-xiaoying.com.cn', child_domains)

        # help-xiaoying-chat.com.cn 和 m-xiaoying-chat.com.cn 应作为独立根域名
        self.assertIn('help-xiaoying-chat.com.cn', root_names)
        self.assertIn('m-xiaoying-chat.com.cn', root_names)

    def test_hl_cn_domains_grouped_correctly(self):
        """多个 *.hl.cn 域名，chat-xiaoying.hl.cn 应作为根域名包含其子域名"""
        from XiaoYingAdmin.views.page_tree import _build_domain_tree
        from XiaoYingAdmin.models.generated_page import GeneratedPage

        pages_data = [
            {'name': 'page1', 'domains': ['chat-xiaoying.hl.cn']},
            {'name': 'page2', 'domains': ['app.chat-xiaoying.hl.cn']},
            {'name': 'page3', 'domains': ['www.chat-xiaoying.hl.cn']},
            {'name': 'page4', 'domains': ['for-xiaoiyng.hl.cn']},
        ]
        pages = [GeneratedPage(name=d['name'], domains=d['domains']) for d in pages_data]
        result = _build_domain_tree(pages)

        root_names = [r['root'] for r in result]
        self.assertNotIn('hl.cn', root_names, 'hl.cn 不应作为根域名出现')

        # chat-xiaoying.hl.cn 应作为根域名，包含 app. 和 www. 子域名
        root_chat = None
        for r in result:
            if r['root'] == 'chat-xiaoying.hl.cn':
                root_chat = r
                break
        self.assertIsNotNone(root_chat, 'chat-xiaoying.hl.cn 应作为根域名')
        child_domains = [d['domain'] for d in root_chat['domains']]
        self.assertIn('chat-xiaoying.hl.cn', child_domains)
        self.assertIn('app.chat-xiaoying.hl.cn', child_domains)
        self.assertIn('www.chat-xiaoying.hl.cn', child_domains)

        # for-xiaoiyng.hl.cn 没有子域名，独立作为根域名
        self.assertIn('for-xiaoiyng.hl.cn', root_names)

    def test_virtual_root_three_segments_preserved(self):
        """3 段虚拟根域名（如 web-apply-whatsapp.com.cn）仍应被正常推断"""
        from XiaoYingAdmin.views.page_tree import _build_domain_tree
        from XiaoYingAdmin.models.generated_page import GeneratedPage

        # 4 段域名共享同一个 3 段父域名 → 虚拟根域名应被正常保留
        pages_data = [
            {'name': 'page1', 'domains': ['m.web-apply-whatsapp.com.cn']},
            {'name': 'page2', 'domains': ['www.web-apply-whatsapp.com.cn']},
        ]
        pages = [GeneratedPage(name=d['name'], domains=d['domains']) for d in pages_data]
        result = _build_domain_tree(pages)

        root_names = [r['root'] for r in result]
        self.assertIn('web-apply-whatsapp.com.cn', root_names,
                      '3 段虚拟根域名应被正常推断保留')


class TestSyncAndTreeEndToEnd(TestCase):
    """端到端测试：批量导入 → 同步 → 树形展示"""

    def setUp(self):
        self.factory = RequestFactory()

    def _collect_all_domains(self, nodes):
        result = set()
        for node in nodes:
            result.add(node['domain'])
            if node.get('children'):
                result.update(self._collect_all_domains(node['children']))
        return result

    def test_sync_from_generated_page_and_show_in_tree(self):
        """从单页面同步域名到SeoDomain，并在树形API中展示，且在虚拟根下正确嵌套"""
        from XiaoYingAdmin.models.generated_page import GeneratedPage
        import uuid

        # 1. 模拟批量导入：创建两个 GeneratedPage，各有一个域名
        GeneratedPage.objects.create(
            name='page1.html',
            html_content='<h1>Page 1</h1>',
            domains=['m.web-apply-whatsapp.com.cn'],
            task_id=uuid.uuid4(),
        )
        GeneratedPage.objects.create(
            name='page2.html',
            html_content='<h1>Page 2</h1>',
            domains=['www.web-apply-whatsapp.com.cn'],
            task_id=uuid.uuid4(),
        )

        # 2. 同步
        sync_request = self.factory.post('/xiaoying_admin/api/seo/domains/sync/')
        sync_response = api_seo_domains_sync(sync_request)
        sync_data = json.loads(sync_response.content)
        self.assertTrue(sync_data['ok'], '同步应成功')
        self.assertEqual(sync_data['message'], '同步完成，新增 2 个域名',
                         '应新增 2 个域名')

        # 3. 验证 SeoDomain 记录已创建
        self.assertTrue(
            SeoDomain.objects.filter(domain='m.web-apply-whatsapp.com.cn').exists(),
        )
        self.assertTrue(
            SeoDomain.objects.filter(domain='www.web-apply-whatsapp.com.cn').exists(),
        )

        # 4. 树形 API 应展示这两个域名，且嵌套在虚拟根下
        tree_request = self.factory.get('/xiaoying_admin/api/seo/domains/tree/')
        tree_response = api_seo_domains_tree(tree_request)
        tree_data = json.loads(tree_response.content)
        self.assertTrue(tree_data['ok'])

        tree_domains = self._collect_all_domains(tree_data['tree'])
        self.assertIn('m.web-apply-whatsapp.com.cn', tree_domains,
                      '同步后树中应包含 m.web-apply-whatsapp.com.cn')
        self.assertIn('www.web-apply-whatsapp.com.cn', tree_domains,
                      '同步后树中应包含 www.web-apply-whatsapp.com.cn')

        # 验证虚拟根域名出现，且子域名嵌套在它下面
        virtual_root = None
        for node in tree_data['tree']:
            if node['domain'] == 'web-apply-whatsapp.com.cn':
                virtual_root = node
                break
        self.assertIsNotNone(
            virtual_root,
            '虚拟根域名 web-apply-whatsapp.com.cn 应作为根节点',
        )
        child_domains = [c['domain'] for c in (virtual_root.get('children') or [])]
        self.assertIn('m.web-apply-whatsapp.com.cn', child_domains)
        self.assertIn('www.web-apply-whatsapp.com.cn', child_domains)

    def test_sync_idempotent(self):
        """重复同步不应新增重复域名"""
        from XiaoYingAdmin.models.generated_page import GeneratedPage
        import uuid

        GeneratedPage.objects.create(
            name='test.html',
            html_content='<h1>Test</h1>',
            domains=['test.example.com'],
            task_id=uuid.uuid4(),
        )

        # 第一次同步
        sync_request = self.factory.post('/xiaoying_admin/api/seo/domains/sync/')
        sync_data = json.loads(api_seo_domains_sync(sync_request).content)
        self.assertEqual(sync_data['message'], '同步完成，新增 1 个域名')

        # 第二次同步 — 应新增 0 个
        sync_data2 = json.loads(api_seo_domains_sync(sync_request).content)
        self.assertEqual(sync_data2['message'], '同步完成，新增 0 个域名',
                         '重复同步不应新增域名')
        self.assertEqual(SeoDomain.objects.filter(domain='test.example.com').count(),
                         1, '不应有重复域名记录')
