-- 用户认证系统数据库初始化脚本
-- 使用数据库
USE quanttradingsystem;

-- 1. 创建用户表
CREATE TABLE IF NOT EXISTS users (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '用户ID',
    username VARCHAR(50) NOT NULL UNIQUE COMMENT '用户名',
    email VARCHAR(100) NOT NULL UNIQUE COMMENT '邮箱',
    password_hash VARCHAR(255) NOT NULL COMMENT '密码哈希',
    full_name VARCHAR(100) COMMENT '真实姓名',
    phone VARCHAR(20) COMMENT '手机号',
    avatar_url VARCHAR(255) COMMENT '头像URL',
    
    -- 状态信息
    status ENUM('active', 'inactive', 'locked') DEFAULT 'active' COMMENT '账户状态',
    email_verified BOOLEAN DEFAULT FALSE COMMENT '邮箱是否验证',
    phone_verified BOOLEAN DEFAULT FALSE COMMENT '手机是否验证',
    
    -- 安全信息
    failed_login_attempts INT DEFAULT 0 COMMENT '登录失败次数',
    locked_until DATETIME COMMENT '锁定截止时间',
    last_login_at DATETIME COMMENT '最后登录时间',
    last_login_ip VARCHAR(45) COMMENT '最后登录IP',
    
    -- 时间戳
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    deleted_at DATETIME COMMENT '软删除时间',
    
    INDEX idx_username (username),
    INDEX idx_email (email),
    INDEX idx_status (status),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户表';

-- 2. 创建角色表
CREATE TABLE IF NOT EXISTS roles (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '角色ID',
    role_name VARCHAR(50) NOT NULL UNIQUE COMMENT '角色名称',
    role_code VARCHAR(50) NOT NULL UNIQUE COMMENT '角色代码',
    description TEXT COMMENT '角色描述',
    is_system BOOLEAN DEFAULT FALSE COMMENT '是否系统角色',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_role_code (role_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色表';

-- 3. 创建权限表
CREATE TABLE IF NOT EXISTS permissions (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '权限ID',
    permission_name VARCHAR(100) NOT NULL COMMENT '权限名称',
    permission_code VARCHAR(100) NOT NULL UNIQUE COMMENT '权限代码',
    resource_type VARCHAR(50) COMMENT '资源类型',
    description TEXT COMMENT '权限描述',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_permission_code (permission_code),
    INDEX idx_resource_type (resource_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='权限表';

-- 4. 创建用户角色关联表
CREATE TABLE IF NOT EXISTS user_roles (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL COMMENT '用户ID',
    role_id INT NOT NULL COMMENT '角色ID',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE KEY uk_user_role (user_id, role_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE,
    INDEX idx_user_id (user_id),
    INDEX idx_role_id (role_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户角色关联表';

-- 5. 创建角色权限关联表
CREATE TABLE IF NOT EXISTS role_permissions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    role_id INT NOT NULL COMMENT '角色ID',
    permission_id INT NOT NULL COMMENT '权限ID',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE KEY uk_role_permission (role_id, permission_id),
    FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE,
    FOREIGN KEY (permission_id) REFERENCES permissions(id) ON DELETE CASCADE,
    INDEX idx_role_id (role_id),
    INDEX idx_permission_id (permission_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色权限关联表';

-- 6. 创建登录日志表
CREATE TABLE IF NOT EXISTS login_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT COMMENT '用户ID',
    username VARCHAR(50) COMMENT '用户名',
    login_type ENUM('web', 'api', 'mobile') DEFAULT 'web' COMMENT '登录类型',
    login_status ENUM('success', 'failed') NOT NULL COMMENT '登录状态',
    failure_reason VARCHAR(255) COMMENT '失败原因',
    ip_address VARCHAR(45) COMMENT 'IP地址',
    user_agent TEXT COMMENT 'User Agent',
    location VARCHAR(100) COMMENT '登录位置',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_user_id (user_id),
    INDEX idx_login_status (login_status),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='登录日志表';

-- 初始化默认角色
INSERT INTO roles (role_name, role_code, description, is_system) VALUES
('超级管理员', 'super_admin', '拥有系统所有权限', TRUE),
('管理员', 'admin', '系统管理员，可管理用户和配置', TRUE),
('高级用户', 'premium', '付费用户，可使用高级功能', FALSE),
('普通用户', 'normal', '免费用户，使用基础功能', FALSE),
('游客', 'guest', '访客用户，只能查看公开内容', FALSE)
ON DUPLICATE KEY UPDATE role_name=VALUES(role_name);

-- 初始化基础权限
INSERT INTO permissions (permission_name, permission_code, resource_type, description) VALUES
-- 数据管理权限
('查看股票数据', 'stock.data.view', 'data', '查看股票行情数据'),
('下载股票数据', 'stock.data.download', 'data', '下载股票数据'),
('查看基金数据', 'fund.data.view', 'data', '查看基金持仓数据'),
('下载基金数据', 'fund.data.download', 'data', '下载基金数据'),
('分析基金数据', 'fund.data.analyze', 'data', '分析基金持仓数据'),

-- 策略管理权限
('查看策略', 'strategy.view', 'strategy', '查看交易策略'),
('创建策略', 'strategy.create', 'strategy', '创建交易策略'),
('编辑策略', 'strategy.edit', 'strategy', '编辑交易策略'),
('删除策略', 'strategy.delete', 'strategy', '删除交易策略'),
('运行策略', 'strategy.run', 'strategy', '运行交易策略'),

-- 交易管理权限
('查看交易记录', 'trade.view', 'trade', '查看交易记录'),
('模拟交易', 'trade.simulate', 'trade', '进行模拟交易'),
('实盘交易', 'trade.live', 'trade', '进行实盘交易'),

-- RNN模型权限
('查看模型', 'rnn.model.view', 'rnn', '查看RNN模型'),
('训练模型', 'rnn.model.train', 'rnn', '训练RNN模型'),
('使用预测', 'rnn.model.predict', 'rnn', '使用模型预测'),

-- 系统管理权限
('用户管理', 'system.user.manage', 'system', '管理系统用户'),
('角色管理', 'system.role.manage', 'system', '管理系统角色'),
('系统配置', 'system.config.manage', 'system', '管理系统配置'),
('查看日志', 'system.log.view', 'system', '查看系统日志')
ON DUPLICATE KEY UPDATE permission_name=VALUES(permission_name);

-- 初始化超级管理员权限（拥有所有权限）
INSERT INTO role_permissions (role_id, permission_id)
SELECT 1, id FROM permissions
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions 
    WHERE role_id = 1 AND permission_id = permissions.id
);

-- 初始化普通用户的基础权限
INSERT INTO role_permissions (role_id, permission_id)
SELECT 4, id FROM permissions 
WHERE permission_code IN (
    'stock.data.view', 'fund.data.view', 'strategy.view', 
    'trade.view', 'rnn.model.view'
)
AND NOT EXISTS (
    SELECT 1 FROM role_permissions 
    WHERE role_id = 4 AND permission_id = permissions.id
);

-- 初始化管理员权限（除了超级管理员的特殊权限外的所有权限）
INSERT INTO role_permissions (role_id, permission_id)
SELECT 2, id FROM permissions 
WHERE permission_code NOT IN ('system.config.manage')
AND NOT EXISTS (
    SELECT 1 FROM role_permissions 
    WHERE role_id = 2 AND permission_id = permissions.id
);

-- 创建默认管理员账号（密码：admin123）
-- 注意：请在生产环境中修改默认密码
INSERT INTO users (username, email, password_hash, full_name, status)
VALUES (
    'admin',
    'admin@example.com',
    'pbkdf2:sha256:600000$randomsalt$hashedpassword',  -- 这是示例，实际需要使用Flask生成
    '系统管理员',
    'active'
)
ON DUPLICATE KEY UPDATE username=VALUES(username);

-- 给管理员分配超级管理员角色
INSERT INTO user_roles (user_id, role_id)
SELECT u.id, 1 
FROM users u 
WHERE u.username = 'admin'
AND NOT EXISTS (
    SELECT 1 FROM user_roles ur 
    WHERE ur.user_id = u.id AND ur.role_id = 1
);

-- 显示初始化结果
SELECT '用户认证系统数据库初始化完成！' AS message;
SELECT COUNT(*) AS role_count FROM roles;
SELECT COUNT(*) AS permission_count FROM permissions;
SELECT COUNT(*) AS user_count FROM users;



