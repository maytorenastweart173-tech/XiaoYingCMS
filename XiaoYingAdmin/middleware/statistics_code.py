"""
统计代码注入中间件 — 当 SiteSettings.is_active=True 且 statistics_code 非空时，
在所有非后台 HTML 响应的 </body> 之前注入统计代码。

触发条件（全部满足才注入）：
  1. 请求路径不以 /xiaoying_admin/ 开头（排除后台）
  2. 请求路径不以 /static/ 或 /media/ 开头（静态资源）
  3. 响应 Content-Type 包含 text/html
  4. 响应状态码 2xx
  5. SiteSettings.is_active=True 且 statistics_code 非空

注入位置：
  - 若响应正文含 </body>：插入到第一个 </body> 之前
  - 否则：追加到响应正文末尾

设计要点：
  - 每请求查 DB（单例表，O(1)），方便开发时改配置即时生效
  - 注入后必须更新 Content-Length，否则浏览器/代理可能截断
  - 解码失败时原样返回 response，不影响正常响应
  - 错误页（4xx/5xx）不注入（统计脚本放在错误页没意义）
"""

from XiaoYingAdmin.models.site_settings import SiteSettings


class StatisticsCodeMiddleware:
    """统计代码注入中间件。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # 后台、静态资源路径直接放行
        path = request.path
        if path.startswith('/xiaoying_admin/') or path.startswith('/static/') or path.startswith('/media/'):
            return self.get_response(request)

        response = self.get_response(request)

        # 只对 2xx 的 HTML 响应注入
        if not (200 <= response.status_code < 300):
            return response
        if 'text/html' not in response.get('Content-Type', ''):
            return response

        code, is_active = self._get_statistics_code()
        if not is_active or not code:
            return response

        # 注入到响应正文
        try:
            content = response.content.decode('utf-8')
        except (UnicodeDecodeError, AttributeError):
            return response

        if '</body>' in content:
            new_content = content.replace('</body>', '{}\n</body>'.format(code), 1)
        else:
            new_content = content + '\n' + code

        response.content = new_content.encode('utf-8')
        response['Content-Length'] = str(len(response.content))
        return response

    @staticmethod
    def _get_statistics_code():
        """
        读取 SiteSettings 单例配置。
        返回 (statistics_code: str, is_active: bool)。
        """
        settings = SiteSettings.objects.first()
        if settings is None:
            return '', False
        return settings.statistics_code or '', bool(settings.is_active)
