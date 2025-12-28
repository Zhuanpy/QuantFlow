# Stock RNN Trading System

一个基于 Flask 和 RNN 模型的股票量化交易系统。

## 功能特性

- 📊 **数据下载与管理**：支持从东方财富、雪球等平台下载股票数据
- 🤖 **RNN 模型预测**：使用深度学习模型进行股票趋势预测
- 📈 **技术指标分析**：MACD、布林带等技术指标计算
- 🔄 **自动化交易**：支持自动化交易策略执行
- 📱 **Web 界面**：基于 Flask 的 Web 管理界面

## 技术栈

- **后端框架**：Flask
- **数据库**：MySQL (使用 SQLAlchemy ORM)
- **数据处理**：Pandas, NumPy
- **机器学习**：Keras/TensorFlow
- **数据下载**：requests, selenium, pytdx

## 项目结构

```
Stock_RNN/
├── App/                    # Flask 应用主目录
│   ├── codes/             # 核心业务代码
│   │   ├── downloads/     # 数据下载模块
│   │   ├── RnnModel/      # RNN 模型相关
│   │   ├── Evaluation/    # 评估模块
│   │   └── ...
│   ├── models/            # 数据模型
│   ├── routes/           # 路由定义
│   └── templates/        # HTML 模板
├── config.example.py      # 配置文件模板
├── run.py                # 应用启动文件
└── README.md            # 项目说明
```

## 安装与配置

### 1. 克隆项目

```bash
git clone https://github.com/your-username/Stock_RNN.git
cd Stock_RNN
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置数据库

1. 复制配置文件模板：
```bash
cp config.example.py config.py
```

2. 编辑 `config.py`，填入你的数据库配置：
```python
DB_HOST = 'localhost'
DB_PORT = '3306'
DB_USER = 'your_username'
DB_PASSWORD = 'your_password'
DB_NAME = 'quanttradingsystem'
```

### 4. 初始化数据库

运行数据库迁移脚本（根据实际情况调整）：
```bash
python scripts/setup_auth_system.py
```

### 5. 启动应用

```bash
python run.py
```

应用将在 `http://localhost:5000` 启动。

## 使用说明

### 数据下载

1. 访问数据下载页面
2. 选择要下载的股票代码
3. 选择数据类型（1分钟、15分钟、日线等）
4. 点击下载按钮

### 模型训练

1. 准备训练数据
2. 运行模型训练脚本
3. 查看训练结果

### 策略回测

1. 选择回测策略
2. 设置回测参数
3. 运行回测
4. 查看回测报告

## 注意事项

⚠️ **重要**：
- `config.py` 文件包含敏感信息，已添加到 `.gitignore`，不会提交到仓库
- 请使用 `config.example.py` 作为配置模板
- 生产环境请使用环境变量管理敏感配置

## 开发

### 代码规范

- 遵循 PEP 8 Python 代码规范
- 使用类型提示（Type Hints）
- 添加适当的文档字符串

### 贡献

欢迎提交 Issue 和 Pull Request！

## 许可证

[请选择你的许可证]

## 联系方式

[你的联系方式]

