================================================================
  左侧菜单 — 使用说明
================================================================

目录结构：
  左侧菜单/
  ├── README.txt              ← 本说明文件
  ├── 左侧菜单.html            ← 侧边栏容器模板
  ├── 左侧菜单_项.html          ← 递归菜单项模板（支持无限层级）
  └── 左侧菜单配置.json         ← 菜单数据结构配置文件

依赖的其它文件：
  XiaoYingAdmin/middleware/layout.py               ← LayoutMiddleware + 上下文处理器
  XiaoYingAdmin/static/left-menu/left-menu.css      ← 侧边栏专用样式（作用域隔离）
  XiaoYingAdmin/templates/XiaoYingAdmin/template.html ← 母版模板（条件引入侧边栏）
  XiaoYingCMS/settings.py                          ← 注册中间件和上下文处理器


================================================================
一、如何添加/修改菜单项
================================================================

编辑 左侧菜单配置.json，每个菜单项的格式如下：

    {
      "name": "菜单显示名称",
      "icon": "",                  // FontAwesome 图标类名（可选，空字符串则不显示图标）
      "url": "/xiaoying_admin/xxx/",  // 点击跳转的 URL
      "children": []               // 子菜单数组，空数组表示无子菜单
    }

示例：

    [
      {
        "name": "首页",
        "icon": "fas fa-home",
        "url": "/xiaoying_admin/index/",
        "children": []
      }
    ]

说明：
  - 支持无限层级的嵌套，children 里继续套 children 即可。
  - url 为空字符串 "" 表示该菜单不可点击（仅作为父级分组）。
  - 图标使用 FontAwesome 类名，例如 "fas fa-cog"、"fas fa-users" 等。
    完整图标列表参考：https://fontawesome.com/icons


================================================================
二、权限控制：仅超级管理员可见
================================================================

如果需要将某个菜单项设置为仅超级管理员可见，在菜单配置对象中添加
"superuser_only": true 即可：

    {
      "name": "安全防护",
      "superuser_only": true,      // ← 仅超级管理员可见
      "icon": "",
      "url": "",
      "children": [
        {
          "name": "防火墙",
          "icon": "",
          "url": "/xiaoying_admin/firewall/",
          "children": []
        }
      ]
    }

效果：
  - 超级管理员（is_superuser=True）登录后，该菜单正常显示。
  - 普通用户和管理员（is_superuser=False）登录后，该菜单项及其所有
    子菜单都会被递归移除，完全不可见。

扩展方法：
  后续需要给更多菜单设置权限时，只需在对应的 JSON 对象中加一行
  "superuser_only": true 即可，无需修改任何 Python 代码。

底层实现：
  XiaoYingAdmin/middleware/layout.py 中的 _filter_superuser_only()
  函数递归过滤菜单数据，在 LayoutMiddleware.__call__() 中调用。


================================================================
三、工作原理
================================================================

请求流程：

  1. LayoutMiddleware 拦截请求
     → 读取 左侧菜单配置.json（带缓存，修改文件后自动刷新）
     → 根据当前用户权限过滤菜单（superuser_only 项）
     → 根据当前 URL 递归匹配激活菜单
     → 将数据注入 request 对象

  2. layout_context_processor（上下文处理器）
     → 将 request 中的数据注入所有模板变量：
       - sidebar_menu_data   → 菜单配置列表（已过滤）
       - sidebar_active_urls → 当前激活的菜单 URL 集合
       - show_sidebar        → 是否显示侧边栏（默认 True）

  3. template.html
     → {% if show_sidebar %} 决定是否渲染侧边栏
     → {% include 'XiaoYingAdmin/左侧菜单/左侧菜单.html' %} 引入菜单

  4. 浏览器端 JS
     → 根据当前 window.location.pathname 自动高亮对应菜单项
     → 自动展开祖先父级菜单


================================================================
四、如何控制侧边栏显隐
================================================================

在视图函数中设置 request.show_sidebar：

    # 显示侧边栏（默认行为，不写也可以）
    def index(request):
        request.show_sidebar = True
        return render(request, 'index.html')

    # 隐藏侧边栏（如登录页、注册页等）
    def login(request):
        request.show_sidebar = False
        return render(request, 'login.html')


================================================================
五、菜单高亮规则
================================================================

高亮逻辑由前端 JS 执行（template.html 底部）：

  - 遍历侧边栏中所有 <a href="...">，查找 href 与当前浏览器
    URL 完全匹配的菜单项。
  - 匹配到的菜单项添加 layui-this 类（高亮）。
  - 该菜单的所有祖先父级菜单添加 layui-nav-itemed 类（展开）。

注意：高亮是浏览器端匹配，与服务端无关。
      所以即使中间件返回的 sidebar_active_urls 当前未被模板使用，
      它仍保留在模板变量中，可供后续扩展。


================================================================
六、启用调试日志
================================================================

在项目根目录的 .env 文件中设置：

    LOG_DEBUG=True

重启服务后，LayoutMiddleware 会打印详细日志：
  - 菜单配置加载成功/失败
  - 当前请求路径与匹配到的激活菜单
  - 权限过滤记录


================================================================
七、常见问题
================================================================

Q: 修改了 左侧菜单配置.json 需要重启服务吗？
A: 不需要。中间件会检测文件修改时间自动刷新缓存。

Q: 新建的页面侧边栏不显示？
A: 检查该页面的视图函数是否误设了 show_sidebar = False。
   另外确认该页面的 URL 是否已在 左侧菜单配置.json 中定义。

Q: 菜单项要加图标，去哪里查有哪些图标？
A: FontAwesome 官方图标库：https://fontawesome.com/icons

Q: 如何修改侧边栏宽度或背景色？
A: 修改 static/left-menu/left-menu.css 中的 .sidebar-menu 样式。

Q: 如何修改顶部的"管理后台"标题？
A: 修改 左侧菜单.html 中 <h3> 标签内的文字。

Q: 菜单配置加了 superuser_only: true，为什么普通用户还能看到？
A: 检查中间件 LayoutMiddleware 是否已正确注册到 settings.py 的
   MIDDLEWARE 列表中，且位置在上下文处理器之前。
   另外确认用户确实是 is_superuser=False。
