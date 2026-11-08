/**
 * 顶部标签页导航栏
 *
 * 功能：
 *   - 打开页面后自动在顶部新增标签（细长紧凑风格）
 *   - 点击标签快速切换页面
 *   - × 关闭标签
 *   - 右键菜单：关闭当前/关闭其它/关闭右侧/关闭左侧/关闭所有
 *   - localStorage 持久化，刷新/重登后保留
 *   - 超出视口时横向滚动
 *
 * 数据来源：从左侧菜单 #sidebar-menu 的 <a> 标签提取 URL→{title,icon} 映射
 * 不依赖 layui，纯原生 JS + FontAwesome 图标
 */
(function () {
    'use strict';

    var STORAGE_KEY = 'xiaoYing_nav_tabs';
    var HOME_URL = '/xiaoying_admin/';

    /* ===== 转义工具 ===== */
    function escHtml(s) {
        return s ? String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') : '';
    }
    function escAttr(s) {
        return s ? String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;') : '';
    }

    /* ===== 从左侧菜单提取 URL → {title, icon} 映射 ===== */
    function buildMenuMap() {
        var map = {};
        var menu = document.getElementById('sidebar-menu');
        if (!menu) return map;
        var links = menu.querySelectorAll('a[href]');
        for (var i = 0; i < links.length; i++) {
            var a = links[i];
            var href = a.getAttribute('href');
            if (!href || href === 'javascript:;' || href.charAt(0) === '#') continue;
            var title = a.textContent.trim();
            var iconEl = a.querySelector('i');
            var icon = iconEl ? iconEl.className : '';
            map[href] = { title: title, icon: icon };
        }
        return map;
    }

    /* ===== 获取当前页的标签信息 ===== */
    function getCurrentTabInfo(menuMap) {
        var currentUrl = window.location.pathname;
        if (menuMap[currentUrl]) {
            return { url: currentUrl, title: menuMap[currentUrl].title, icon: menuMap[currentUrl].icon };
        }
        var bestMatch = null, bestLen = 0;
        for (var menuUrl in menuMap) {
            if (menuMap.hasOwnProperty(menuUrl) &&
                currentUrl.indexOf(menuUrl) === 0 &&
                menuUrl.length > bestLen) {
                bestMatch = menuUrl;
                bestLen = menuUrl.length;
            }
        }
        if (bestMatch) {
            return { url: currentUrl, title: menuMap[bestMatch].title, icon: menuMap[bestMatch].icon };
        }
        var title = document.title || '';
        var dashIdx = title.indexOf(' — ');
        if (dashIdx > 0) title = title.substring(0, dashIdx);
        if (!title) {
            var segs = currentUrl.split('/').filter(Boolean);
            title = segs.length ? segs[segs.length - 1] : '首页';
        }
        return { url: currentUrl, title: title, icon: 'fas fa-file' };
    }

    /* ===== localStorage 读写 ===== */
    function loadTabs() {
        try {
            var raw = localStorage.getItem(STORAGE_KEY);
            return raw ? JSON.parse(raw) : [];
        } catch (e) { return []; }
    }
    function saveTabs(tabs) {
        try { localStorage.setItem(STORAGE_KEY, JSON.stringify(tabs)); } catch (e) {}
    }

    /* ===== 标签操作 ===== */
    function addTab(tabs, url, title, icon) {
        for (var i = 0; i < tabs.length; i++) {
            if (tabs[i].url === url) return tabs;
        }
        tabs.push({ url: url, title: title, icon: icon });
        return tabs;
    }

    function removeTab(tabs, url) {
        for (var i = 0; i < tabs.length; i++) {
            if (tabs[i].url === url) { tabs.splice(i, 1); break; }
        }
        return tabs;
    }

    /* ===== 渲染标签栏 ===== */
    function renderTabs(tabs, currentUrl) {
        var bar = document.getElementById('navTabsBar');
        if (!bar) return;
        if (!tabs || tabs.length === 0) {
            bar.innerHTML = '<div class="nav-tabs-empty">暂无标签</div>';
            return;
        }
        var html = '<div class="nav-tabs-scroller">';
        for (var i = 0; i < tabs.length; i++) {
            var t = tabs[i];
            var isActive = (t.url === currentUrl) ? ' active' : '';
            var iconHtml = t.icon ? '<i class="' + escAttr(t.icon) + ' nav-tab-icon"></i>' : '';
            html += '<div class="nav-tab' + isActive + '" data-url="' + escAttr(t.url) + '" title="' + escAttr(t.title) + '">' +
                iconHtml +
                '<span class="nav-tab-title">' + escHtml(t.title) + '</span>' +
                '<i class="fas fa-times nav-tab-close" title="关闭"></i>' +
                '</div>';
        }
        html += '</div>';
        bar.innerHTML = html;
        bindTabEvents(bar);
        scrollToActive(bar);
    }

    function scrollToActive(bar) {
        var active = bar.querySelector('.nav-tab.active');
        if (!active) return;
        var scroller = bar.querySelector('.nav-tabs-scroller');
        if (!scroller) return;
        var left = active.offsetLeft, right = left + active.offsetWidth;
        var viewLeft = scroller.scrollLeft, viewRight = viewLeft + scroller.offsetWidth;
        if (left < viewLeft) scroller.scrollLeft = left;
        else if (right > viewRight) scroller.scrollLeft = right - scroller.offsetWidth;
    }

    /* ===== 事件绑定 ===== */
    function bindTabEvents(bar) {
        var tabEls = bar.querySelectorAll('.nav-tab');
        for (var j = 0; j < tabEls.length; j++) {
            (function (el) {
                var url = el.getAttribute('data-url');
                // 左键点击
                el.addEventListener('click', function (e) {
                    if (e.target.classList.contains('nav-tab-close')) {
                        e.stopPropagation();
                        closeSingleTab(url);
                    } else {
                        if (url !== window.location.pathname) window.location.href = url;
                    }
                });
                // 右键菜单
                el.addEventListener('contextmenu', function (e) {
                    e.preventDefault();
                    e.stopPropagation();
                    showContextMenu(e.clientX, e.clientY, url);
                });
            })(tabEls[j]);
        }
    }

    /* ===== 关闭单个标签 ===== */
    function closeSingleTab(url) {
        var tabs = loadTabs();
        var isCurrent = (url === window.location.pathname);
        tabs = removeTab(tabs, url);
        saveTabs(tabs);
        if (isCurrent) {
            if (tabs.length > 0) window.location.href = tabs[tabs.length - 1].url;
            else window.location.href = HOME_URL;
        } else {
            renderTabs(tabs, window.location.pathname);
        }
    }

    /* ===== 右键菜单 ===== */
    var _contextMenu = null;

    function showContextMenu(x, y, targetUrl) {
        hideContextMenu();
        var tabs = loadTabs();
        var targetIdx = -1;
        for (var i = 0; i < tabs.length; i++) {
            if (tabs[i].url === targetUrl) { targetIdx = i; break; }
        }
        if (targetIdx < 0) return;

        var hasLeft = targetIdx > 0;
        var hasRight = targetIdx < tabs.length - 1;
        var hasOthers = tabs.length > 1;

        var menu = document.createElement('div');
        menu.className = 'nav-tab-context-menu';
        menu.innerHTML =
            '<div class="context-menu-item" data-action="close">关闭</div>' +
            '<div class="context-menu-item' + (hasOthers ? '' : ' disabled') + '" data-action="close-others">关闭其它</div>' +
            '<div class="context-menu-item' + (hasRight ? '' : ' disabled') + '" data-action="close-right">关闭右侧</div>' +
            '<div class="context-menu-item' + (hasLeft ? '' : ' disabled') + '" data-action="close-left">关闭左侧</div>' +
            '<div class="context-menu-divider"></div>' +
            '<div class="context-menu-item" data-action="close-all">关闭所有</div>';
        document.body.appendChild(menu);
        // 边界检测：避免菜单超出视口
        menu.style.left = Math.min(x, window.innerWidth - menu.offsetWidth - 8) + 'px';
        menu.style.top = Math.min(y, window.innerHeight - menu.offsetHeight - 8) + 'px';

        _contextMenu = menu;

        // 菜单项点击
        var items = menu.querySelectorAll('.context-menu-item');
        for (var k = 0; k < items.length; k++) {
            (function (item) {
                item.addEventListener('click', function (e) {
                    if (item.classList.contains('disabled')) return;
                    var action = item.getAttribute('data-action');
                    hideContextMenu();
                    handleContextAction(action, targetUrl);
                });
            })(items[k]);
        }

        // 点击外部 / Escape 关闭
        setTimeout(function () {
            document.addEventListener('click', hideContextMenu, { once: true });
            document.addEventListener('contextmenu', hideContextMenuOnce, { once: true });
            document.addEventListener('keydown', onEscHide);
        }, 0);
    }

    function hideContextMenuOnce() { hideContextMenu(); }
    function onEscHide(e) { if (e.key === 'Escape') hideContextMenu(); }

    function hideContextMenu() {
        if (_contextMenu) {
            _contextMenu.remove();
            _contextMenu = null;
        }
        document.removeEventListener('click', hideContextMenu);
        document.removeEventListener('contextmenu', hideContextMenuOnce);
        document.removeEventListener('keydown', onEscHide);
    }

    /* ===== 菜单操作 =====
     * 关闭操作后，如果当前页被移除了 → 跳转到目标标签（或最后一个标签/首页）
     */
    function handleContextAction(action, targetUrl) {
        var tabs = loadTabs();
        var currentUrl = window.location.pathname;

        if (action === 'close') {
            closeSingleTab(targetUrl);
            return;
        }

        if (action === 'close-all') {
            saveTabs([]);
            renderTabs([], window.location.pathname);
            return;
        }

        if (action === 'close-others') {
            var kept = null;
            for (var i = 0; i < tabs.length; i++) {
                if (tabs[i].url === targetUrl) { kept = tabs[i]; break; }
            }
            tabs = kept ? [kept] : [];
            saveTabs(tabs);
            if (currentUrl !== targetUrl) window.location.href = targetUrl;
            else renderTabs(tabs, currentUrl);
            return;
        }

        if (action === 'close-right') {
            var targetIdx = -1;
            for (var j = 0; j < tabs.length; j++) {
                if (tabs[j].url === targetUrl) { targetIdx = j; break; }
            }
            var removedRight = tabs.splice(targetIdx + 1);
            var currentRemoved = removedRight.some(function (t) { return t.url === currentUrl; });
            saveTabs(tabs);
            if (currentRemoved) window.location.href = targetUrl;
            else renderTabs(tabs, currentUrl);
            return;
        }

        if (action === 'close-left') {
            var idx = -1;
            for (var k = 0; k < tabs.length; k++) {
                if (tabs[k].url === targetUrl) { idx = k; break; }
            }
            var removedLeft = tabs.splice(0, idx);
            var curRemoved = removedLeft.some(function (t) { return t.url === currentUrl; });
            saveTabs(tabs);
            if (curRemoved) window.location.href = targetUrl;
            else renderTabs(tabs, currentUrl);
            return;
        }
    }

    /* ===== 初始化 ===== */
    function init() {
        var bar = document.getElementById('navTabsBar');
        if (!bar) return;
        if (!document.getElementById('sidebar-menu')) return;

        var menuMap = buildMenuMap();
        var currentInfo = getCurrentTabInfo(menuMap);
        var tabs = loadTabs();
        tabs = addTab(tabs, currentInfo.url, currentInfo.title, currentInfo.icon);
        saveTabs(tabs);
        renderTabs(tabs, currentInfo.url);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
