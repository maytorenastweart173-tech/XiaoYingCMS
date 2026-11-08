# 页面路由
from django.urls import path
from XiaoYingAdmin.views import request as admin_request
from XiaoYingAdmin.views.auth import (
    login_view, logout_view,
    register_view, forgot_password_view,
    change_password_view,
    user_list_view, user_create_view, user_edit_view,
    user_toggle_active_api, user_delete_api,
    user_info_api,
    login_log_view, login_log_list_api,
    operation_log_view, operation_log_list_api, operation_log_backup_api, operation_log_restore_api, operation_log_backup_list_api,
)
from XiaoYingAdmin.views.seo.blackhat.cloak import (
    seo_cloak_view, seo_cloak_config_save,
    seo_cloak_api_list, seo_cloak_api_get, seo_cloak_api_delete,
)
from XiaoYingAdmin.views.seo.domain_records import (
    domain_seo_domains_view,
    domain_seo_timeline_view,
    api_seo_domains_list,
    api_seo_domains_tree,
    api_seo_domains_create,
    api_seo_domains_update,
    api_seo_domains_delete,
    api_seo_domains_sync,
    api_seo_domain_records_list,
    api_seo_domain_records_create,
    api_seo_domain_records_batch_create,
    api_seo_records_update,
    api_seo_records_delete,
    api_seo_records_clear_all,
)
from XiaoYingAdmin.views.tools.export_import import (
    export_import_view,
    api_export,
    api_import_preview,
    api_import_execute,
)
from XiaoYingAdmin.views.page_domain_seo import (
    api_search_engines_list,
    api_page_domain_seo_save,
    api_page_domain_seo_get,
)
from XiaoYingAdmin.views.spider.logs import (
    spider_logs_view,
    spider_logs_api_list,
    spider_logs_api_clear,
    spider_logs_api_export,
    spider_logs_api_backup,
    spider_logs_api_backup_list,
    spider_logs_api_restore,
)
from XiaoYingAdmin.views.spider.ignore_paths import (
    ignore_paths_view,
    ignore_paths_api_list,
    ignore_paths_api_save,
)
from XiaoYingAdmin.views.spider.analytics import spider_analytics_view
from XiaoYingAdmin.views import page_tree as page_tree_views
from XiaoYingAdmin.views.batch_import import api_batch_import_pages
from XiaoYingAdmin.views.friend_link import (
    friend_link_list, friend_link_create, friend_link_delete,
)
from XiaoYingAdmin.views.firewall import (
    firewall_view,
    firewall_api_list, firewall_api_save,
    firewall_api_toggle, firewall_api_delete, firewall_api_test,
)
from XiaoYingAdmin.views.static_file_route import (
    static_file_route_view,
    static_file_route_api_list, static_file_route_api_save,
    static_file_route_api_delete, static_file_route_api_toggle,
)
from XiaoYingAdmin.views.multi_page import (
    multi_page_list_view, multi_page_create_view,
    multi_page_project_detail_view, multi_page_edit_view, multi_page_preview_view,
    api_multi_page_start_generate, api_multi_page_gen_progress,
    api_multi_page_delete_project, api_multi_page_regenerate,
    api_multi_page_delete_page, api_multi_page_add_page,
    api_multi_page_update_nav, api_multi_page_list_pages, api_multi_page_tree,
    api_multi_page_crosslinks_generate, api_multi_page_crosslink_exclude_toggle,
    api_multi_page_enable, api_multi_page_disable,
    multi_page_config_view, api_multi_page_config_save,
)

# 域名前缀: /xiaoying_admin/
urlpatterns = [
    # 认证（无需登录）
    path('login/', login_view, name='login'),
    path('logout/', logout_view, name='logout'),
    path('register/', register_view, name='register'),
    path('forgot_password/', forgot_password_view, name='forgot_password'),

    # 认证（需登录）
    path('change_password/', change_password_view, name='change_password'),

    # 用户管理（需管理员权限）
    path('users/', user_list_view, name='user_list'),
    path('users/create/', user_create_view, name='user_create'),
    path('users/<int:pk>/edit/', user_edit_view, name='user_edit'),

    # AJAX API: 用户管理
    path('users/<int:pk>/toggle_active/', user_toggle_active_api, name='user_toggle_active'),
    path('users/<int:pk>/delete/', user_delete_api, name='user_delete'),
    path('api/user/info/', user_info_api, name='user_info_api'),

    # 登录日志
    path('login_logs/', login_log_view, name='login_logs'),
    path('api/login_logs/list/', login_log_list_api, name='login_logs_api_list'),

    # 操作日志
    path('operation_logs/', operation_log_view, name='operation_logs'),
    path('api/operation_logs/list/', operation_log_list_api, name='operation_logs_api_list'),
    path('api/operation_logs/backup/', operation_log_backup_api, name='operation_logs_backup_api'),
    path('api/operation_logs/backup/list/', operation_log_backup_list_api, name='operation_logs_backup_list_api'),
    path('api/operation_logs/restore/', operation_log_restore_api, name='operation_logs_restore_api'),

    # 页面
    path('template/', admin_request.template_view, name='template'),
    path('index/', admin_request.index_view, name='index'),
    path('site_settings/', admin_request.site_settings_view, name='site_settings'),
    path('pages/generate/', admin_request.page_generate_view, name='page_generate'),
    path('pages/list/', admin_request.page_list_view, name='page_list'),

    # AJAX API: 页面生成
    path('api/generate/start/', admin_request.api_start_generate, name='api_generate_start'),
    path('api/generate/progress/<uuid:task_id>/', admin_request.api_get_progress, name='api_generate_progress'),
    path('api/generate/abort/<uuid:task_id>/', admin_request.api_abort_generate, name='api_generate_abort'),

    # AJAX API: 提示词管理
    path('api/prompts/', admin_request.api_prompt_list, name='api_prompt_list'),
    path('api/prompts/<int:prompt_id>/', admin_request.api_prompt_detail, name='api_prompt_detail'),
    path('api/prompts/save/', admin_request.api_prompt_save, name='api_prompt_save'),
    path('api/prompts/activate/', admin_request.api_prompt_activate, name='api_prompt_activate'),
    path('api/prompts/delete/', admin_request.api_prompt_delete, name='api_prompt_delete'),

    # SEO：斗篷伪装（黑帽）
    path('seo/cloak/', seo_cloak_view, name='seo_cloak'),
    path('seo/cloak/api/config/save/', seo_cloak_config_save, name='seo_cloak_config_save'),
    path('seo/cloak/api/rules/', seo_cloak_api_list, name='seo_cloak_api_list'),
    path('seo/cloak/api/rules/<int:pk>/', seo_cloak_api_get, name='seo_cloak_api_get'),
    path('seo/cloak/api/rules/<int:pk>/delete/', seo_cloak_api_delete, name='seo_cloak_api_delete'),

    # SEO：域名快排手段时间线记录
    path('seo/domain-records/', domain_seo_domains_view, name='domain_seo_domains'),
    path('seo/domain-records/<int:pk>/', domain_seo_timeline_view, name='domain_seo_timeline'),
    path('api/seo/domains/list/', api_seo_domains_list, name='api_seo_domains_list'),
    path('api/seo/domains/tree/', api_seo_domains_tree, name='api_seo_domains_tree'),
    path('api/seo/domains/create/', api_seo_domains_create, name='api_seo_domains_create'),
    path('api/seo/domains/<int:pk>/update/', api_seo_domains_update, name='api_seo_domains_update'),
    path('api/seo/domains/<int:pk>/delete/', api_seo_domains_delete, name='api_seo_domains_delete'),
    path('api/seo/domains/sync/', api_seo_domains_sync, name='api_seo_domains_sync'),
    path('api/seo/domains/<int:pk>/records/', api_seo_domain_records_list, name='api_seo_domain_records_list'),
    path('api/seo/domains/<int:pk>/records/create/', api_seo_domain_records_create, name='api_seo_domain_records_create'),
    path('api/seo/domains/records/batch-create/', api_seo_domain_records_batch_create, name='api_seo_domain_records_batch_create'),
    path('api/seo/records/<int:pk>/update/', api_seo_records_update, name='api_seo_records_update'),
    path('api/seo/records/<int:pk>/delete/', api_seo_records_delete, name='api_seo_records_delete'),
    path('api/seo/records/clear-all/', api_seo_records_clear_all, name='api_seo_records_clear_all'),

    # 工具: 导入/导出
    path('tools/export-import/', export_import_view, name='export_import_view'),
    path('api/tools/export/', api_export, name='api_export'),
    path('api/tools/import/preview/', api_import_preview, name='api_import_preview'),
    path('api/tools/import/execute/', api_import_execute, name='api_import_execute'),

    # AJAX API: 已保存页面
    path('api/pages/saved/', admin_request.api_saved_pages, name='api_saved_pages'),
    path('api/pages/saved/create/', admin_request.api_saved_page_create, name='api_saved_page_create'),
    path('api/pages/saved/<int:page_id>/', admin_request.api_saved_page_detail, name='api_saved_page_detail'),
    path('api/pages/saved/set-domain/', admin_request.api_saved_page_set_domain, name='api_saved_page_set_domain'),
    path('api/pages/saved/set-categories/', page_tree_views.page_set_categories, name='api_page_set_categories'),
    path('api/pages/saved/delete/', admin_request.api_saved_page_delete, name='api_saved_page_delete'),
    path('api/pages/saved/delete-by-root/', admin_request.api_saved_page_delete_by_root, name='api_saved_page_delete_by_root'),
    path('api/pages/saved/update/', admin_request.api_saved_page_update, name='api_saved_page_update'),
    path('api/pages/saved/seo-optimize/', admin_request.api_seo_optimize_page, name='api_seo_optimize_page'),

    # 页面分类 & 树形结构
    path('api/pages/categories/', page_tree_views.page_category_list, name='page_category_list'),
    path('api/pages/categories/create/', page_tree_views.page_category_create, name='page_category_create'),
    path('api/pages/categories/update/', page_tree_views.page_category_update, name='page_category_update'),
    path('api/pages/categories/delete/', page_tree_views.page_category_delete, name='page_category_delete'),
    path('api/pages/categories/clear/', page_tree_views.api_page_category_clear, name='api_page_category_clear'),
    path('api/pages/uncategorized/clear/', page_tree_views.api_page_uncategorized_clear, name='api_page_uncategorized_clear'),
    path('api/pages/unbound/clear/', page_tree_views.api_page_unbound_clear, name='api_page_unbound_clear'),
    path('api/pages/saved/batch-categorize/', page_tree_views.page_batch_categorize, name='api_page_batch_categorize'),
    path('api/pages/saved/batch-import/', api_batch_import_pages, name='api_batch_import_pages'),
    path('api/pages/tree/', page_tree_views.page_tree_api, name='page_tree_api'),

    # 友情链接
    path('api/friend-links/', friend_link_list, name='friend_link_list'),
    path('api/friend-links/create/', friend_link_create, name='friend_link_create'),
    path('api/friend-links/delete/', friend_link_delete, name='friend_link_delete'),

    # 智能互链
    path('api/crosslinks/generate/', admin_request.api_generate_crosslinks, name='api_crosslinks_generate'),
    path('api/crosslinks/exclude-toggle/', admin_request.api_crosslink_exclude_toggle, name='api_crosslinks_exclude_toggle'),

    # 页面域名SEO状态（收录 & 排名）
    path('api/site-settings/search-engines/', admin_request.api_site_settings_get_search_engines, name='api_search_engines_list'),
    path('api/site-settings/search-engines/save/', admin_request.api_site_settings_save_search_engines, name='api_search_engines_save'),
    path('api/pages/domain-seo/get/', api_page_domain_seo_get, name='api_page_domain_seo_get'),
    path('api/pages/domain-seo/save/', api_page_domain_seo_save, name='api_page_domain_seo_save'),

    # 蜘蛛访问日志
    path('spider/logs/', spider_logs_view, name='spider_logs'),
    path('spider/logs/api/list/', spider_logs_api_list, name='spider_logs_api_list'),
    path('spider/logs/api/clear/', spider_logs_api_clear, name='spider_logs_api_clear'),
    path('spider/logs/api/export/', spider_logs_api_export, name='spider_logs_api_export'),
    path('spider/logs/api/backup/', spider_logs_api_backup, name='spider_logs_api_backup'),
    path('spider/logs/api/backup/list/', spider_logs_api_backup_list, name='spider_logs_api_backup_list'),
    path('spider/logs/api/restore/', spider_logs_api_restore, name='spider_logs_api_restore'),

    # 蜘蛛日志 → 路径过滤
    path('spider/logs/ignore-paths/', ignore_paths_view, name='ignore_paths'),
    path('spider/logs/api/ignore-paths/', ignore_paths_api_list, name='ignore_paths_api_list'),
    path('spider/logs/api/ignore-paths/save/', ignore_paths_api_save, name='ignore_paths_api_save'),

    # 蜘蛛日志 → 数据分析
    path('spider/logs/analytics/', spider_analytics_view, name='spider_analytics'),

    # 防火墙管理
    path('firewall/', firewall_view, name='firewall'),
    path('api/firewall/list/', firewall_api_list, name='firewall_api_list'),
    path('api/firewall/save/', firewall_api_save, name='firewall_api_save'),
    path('api/firewall/<int:pk>/toggle/', firewall_api_toggle, name='firewall_api_toggle'),
    path('api/firewall/<int:pk>/delete/', firewall_api_delete, name='firewall_api_delete'),
    path('api/firewall/test/', firewall_api_test, name='firewall_api_test'),

    # 静态文件路由
    path('static-file/', static_file_route_view, name='static_file_route'),
    path('static-file/api/list/', static_file_route_api_list, name='static_file_route_api_list'),
    path('static-file/api/save/', static_file_route_api_save, name='static_file_route_api_save'),
    path('static-file/api/delete/', static_file_route_api_delete, name='static_file_route_api_delete'),
    path('static-file/api/toggle/', static_file_route_api_toggle, name='static_file_route_api_toggle'),

    # 多页面管理
    path('multi-page/', multi_page_list_view, name='multi_page_list'),
    path('multi-page/create/', multi_page_create_view, name='multi_page_create'),
    path('multi-page/<int:project_id>/', multi_page_project_detail_view, name='multi_page_project_detail'),
    path('multi-page/page/<int:page_id>/edit/', multi_page_edit_view, name='multi_page_edit'),
    path('multi-page/page/<int:page_id>/preview/', multi_page_preview_view, name='multi_page_preview'),

    # API: 多页面生成
    path('api/multi-page/<int:project_id>/generate/', api_multi_page_start_generate, name='api_multi_page_start_generate'),
    path('api/multi-page/<int:project_id>/progress/', api_multi_page_gen_progress, name='api_multi_page_gen_progress'),
    path('api/multi-page/<int:project_id>/regenerate/', api_multi_page_regenerate, name='api_multi_page_regenerate'),

    # API: 多页面 CRUD
    path('api/multi-page/<int:project_id>/delete/', api_multi_page_delete_project, name='api_multi_page_delete_project'),
    path('api/multi-page/<int:project_id>/enable/', api_multi_page_enable, name='api_multi_page_enable'),
    path('api/multi-page/<int:project_id>/disable/', api_multi_page_disable, name='api_multi_page_disable'),
    path('api/multi-page/<int:project_id>/add-page/', api_multi_page_add_page, name='api_multi_page_add_page'),
    path('api/multi-page/<int:project_id>/update-nav/', api_multi_page_update_nav, name='api_multi_page_update_nav'),
    path('api/multi-page/<int:project_id>/pages/', api_multi_page_list_pages, name='api_multi_page_list_pages'),
    path('api/multi-page/page/<int:page_id>/delete/', api_multi_page_delete_page, name='api_multi_page_delete_page'),
    path('api/multi-page/tree/', api_multi_page_tree, name='api_multi_page_tree'),
    path('api/multi-page/crosslinks/generate/', api_multi_page_crosslinks_generate, name='api_multi_page_crosslinks_generate'),
    path('api/multi-page/<int:project_id>/crosslink-exclude-toggle/', api_multi_page_crosslink_exclude_toggle, name='api_multi_page_crosslink_exclude_toggle'),

    # 多页面 AI 配置
    path('multi-page/config/', multi_page_config_view, name='multi_page_config'),
    path('api/multi-page/config/save/', api_multi_page_config_save, name='api_multi_page_config_save'),
]
