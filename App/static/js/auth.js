/**
 * 认证工具类
 */
class Auth {
    /**
     * 获取Token
     */
    static getToken() {
        return localStorage.getItem('token');
    }
    
    /**
     * 设置Token
     */
    static setToken(token) {
        localStorage.setItem('token', token);
    }
    
    /**
     * 移除Token
     */
    static removeToken() {
        localStorage.removeItem('token');
        localStorage.removeItem('refresh_token');
        localStorage.removeItem('user');
    }
    
    /**
     * 检查是否登录
     */
    static isAuthenticated() {
        return !!this.getToken();
    }
    
    /**
     * 获取当前用户信息
     */
    static getCurrentUser() {
        const userStr = localStorage.getItem('user');
        return userStr ? JSON.parse(userStr) : null;
    }
    
    /**
     * 登出
     */
    static async logout() {
        try {
            const token = this.getToken();
            if (token) {
                await fetch('/auth/api/logout', {
                    method: 'POST',
                    headers: {
                        'Authorization': `Bearer ${token}`
                    }
                });
            }
        } catch (error) {
            console.error('登出失败:', error);
        } finally {
            this.removeToken();
            window.location.href = '/auth/login';
        }
    }
    
    /**
     * 检查是否有指定权限
     */
    static hasPermission(permissionCode) {
        const user = this.getCurrentUser();
        if (!user || !user.roles) return false;
        
        for (const role of user.roles) {
            if (role.permissions && role.permissions.some(p => p.permission_code === permissionCode)) {
                return true;
            }
        }
        return false;
    }

    /**
     * 检查是否有指定角色
     */
    static hasRole(roleCode) {
        const user = this.getCurrentUser();
        if (!user || !user.roles) return false;
        
        return user.roles.some(r => r.role_code === roleCode);
    }

    /**
     * 显示/隐藏需要权限的元素
     */
    static setupPermissions() {
        // 查找所有带有 data-permission 属性的元素
        const elements = document.querySelectorAll('[data-permission]');
        elements.forEach(el => {
            const permission = el.getAttribute('data-permission');
            if (!this.hasPermission(permission)) {
                el.style.display = 'none';
            }
        });

        // 查找所有带有 data-role 属性的元素
        const roleElements = document.querySelectorAll('[data-role]');
        roleElements.forEach(el => {
            const role = el.getAttribute('data-role');
            if (!this.hasRole(role)) {
                el.style.display = 'none';
            }
        });
    }

    /**
     * 更新用户信息显示
     */
    static updateUserDisplay() {
        const user = this.getCurrentUser();
        if (!user) return;

        // 更新用户名显示
        const usernameElements = document.querySelectorAll('.user-name');
        usernameElements.forEach(el => {
            el.textContent = user.username;
        });

        // 更新头像
        const avatarElements = document.querySelectorAll('.user-avatar');
        avatarElements.forEach(el => {
            if (user.avatar_url) {
                el.src = user.avatar_url;
            }
        });
    }
}

/**
 * Fetch请求拦截器
 */
const originalFetch = window.fetch;
window.fetch = function(...args) {
    let [url, config] = args;
    
    // 初始化config
    config = config || {};
    config.headers = config.headers || {};
    
    // 添加Token到请求头
    if (Auth.isAuthenticated()) {
        config.headers['Authorization'] = `Bearer ${Auth.getToken()}`;
    }
    
    return originalFetch(url, config).then(response => {
        // 处理401未授权
        if (response.status === 401) {
            console.warn('Token无效或已过期，请重新登录');
            Auth.removeToken();
            // 如果不是登录页，则跳转到登录页
            if (!window.location.pathname.startsWith('/auth/')) {
                window.location.href = '/auth/login';
            }
        }
        return response;
    }).catch(error => {
        console.error('请求失败:', error);
        throw error;
    });
};

/**
 * 页面加载时的初始化
 */
document.addEventListener('DOMContentLoaded', function() {
    // 不需要登录的页面列表
    const publicPages = ['/auth/login', '/auth/register'];
    const currentPath = window.location.pathname;
    
    // 如果是公开页面，不做检查
    if (publicPages.some(page => currentPath.startsWith(page))) {
        return;
    }
    
    // 检查登录状态
    if (!Auth.isAuthenticated()) {
        console.log('用户未登录，跳转到登录页');
        window.location.href = '/auth/login';
        return;
    }

    // 设置权限控制
    Auth.setupPermissions();

    // 更新用户信息显示
    Auth.updateUserDisplay();

    // 设置登出按钮
    const logoutBtns = document.querySelectorAll('.logout-btn, [data-action="logout"]');
    logoutBtns.forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.preventDefault();
            if (confirm('确定要退出登录吗？')) {
                Auth.logout();
            }
        });
    });
});



