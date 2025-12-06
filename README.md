# 日志可视化分析系统

一个基于 FastAPI + Vue 3 + Art Design Pro 开发的日志可视化分析系统，支持日志解析、统计、搜索、可视化等功能。

## ⚡ 快速开始

使用一键启动脚本：

**Windows:**
```bash
quick_start.bat
```

**Linux/Mac:**
```bash
chmod +x quick_start.sh
./quick_start.sh
```

启动脚本会自动：
1. 生成200条测试日志数据
2. 显示日志统计信息
3. 启动FastAPI服务器

## 📋 项目简介

本系统用于分析和可视化应用程序日志，特别是使用 Loguru 库生成的日志。系统支持按 request_id 分组展示日志，提供直观的仪表板、强大的搜索功能和详细的日志追踪。

**注意：本系统已禁用登录功能，直接访问即可使用所有功能。**

## 🆕 新特性：数据库日志存储

### 传统方案的问题

旧的文件日志方案存在以下问题：
- ❌ 每次查询都要读取整个文件，性能差
- ❌ 多文件切割导致日志难以聚合查询
- ❌ 可能会有日志遗漏
- ❌ 文件管理复杂

### 新方案：数据库 + 文件备份

```
应用日志
    ↓
Loguru (多sink配置)
    ├─→ SQLite数据库 (主存储，用于查询)
    ├─→ 控制台输出 (开发调试)
    ├─→ 文件备份 (app_YYYY-MM-DD.log，保留7天)
    └─→ 错误日志 (error_YYYY-MM-DD.log，保留30天)
```

### 优势对比

| 特性 | 旧方案(文件) | 新方案(数据库) |
|------|-------------|---------------|
| **查询性能** | ❌ 慢 | ✅ 快（索引查询） |
| **多条件过滤** | ❌ 复杂 | ✅ 简单（SQL） |
| **统计聚合** | ❌ 全量读取 | ✅ 高效聚合 |
| **并发写入** | ❌ 可能丢失 | ✅ 事务保证 |
| **文件管理** | ❌ 多文件混乱 | ✅ 自动清理 |

### 使用新日志系统

详细文档请查看：[日志收集系统使用指南](LOGGING_GUIDE.md)

#### 1. 在应用中记录日志

```python
from app.logger.config import log_context

# 获取logger
logger = log_context.get_logger(ip="192.168.1.1", request_id="abc123")

# 记录日志
logger.info("用户登录成功")
logger.error("数据库连接失败")
```

#### 2. 查看日志统计

```bash
python manage_logs.py stats
```

#### 3. 清理旧日志

```bash
# 清理30天前的日志
python manage_logs.py clean --days 30
```

#### 4. 生成测试数据

```bash
# 生成100条测试日志
python generate_test_logs.py --count 100
```

### 日志格式

系统支持以下格式的日志：
```
{ip} {time:YYYY-MM-DD HH:mm:ss.ms} [{request_id}] | {level} | {module}.{function}:{line} : {message}
```

示例：
```
47.52.11.194 2025-12-06 01:17:59.1759 [7d5e7703-7605-4cf3-b60b-679a720c1e4a] | INFO | replace_total_n.new_change_json:195 : 处理请求数据
```

## 🏗️ 系统架构

### 后端 (FastAPI)
```
art_server/
├── main.py                  # FastAPI 主入口
├── requirements.txt         # Python 依赖
├── log.log                 # 日志文件
└── app/
    ├── models/             # 数据模型
    │   └── log.py         # 日志模型定义
    ├── services/           # 业务逻辑
    │   └── log_service.py # 日志处理服务
    ├── api/                # API 路由
    │   └── logs.py        # 日志相关接口
    └── utils/              # 工具函数
        └── log_parser.py  # 日志解析器
```

### 前端 (Vue 3 + Art Design Pro)
```
src/views/logs/
├── dashboard/              # 日志总览仪表板
│   ├── index.vue
│   └── modules/
│       ├── stats-cards.vue      # 统计卡片
│       ├── level-chart.vue      # 级别分布图
│       ├── timeline-chart.vue   # 时间趋势图
│       ├── ip-stats.vue         # IP统计
│       └── recent-logs.vue      # 最近日志
├── list/                   # 日志列表页
│   ├── index.vue
│   └── modules/
│       └── log-search.vue       # 搜索组件
└── detail/                 # 日志详情页
    ├── index.vue
    └── modules/
        ├── log-timeline.vue     # 时间线
        └── log-info.vue         # 详细信息
```

## 🚀 快速开始

### 后端启动

1. **安装依赖**
```bash
cd E:\git_project\art_server
pip install -r requirements.txt
```

2. **启动后端服务**
```bash
python main.py
```

后端服务将在 `http://localhost:8000` 启动

3. **查看 API 文档**
访问 `http://localhost:8000/docs` 查看自动生成的 API 文档

### 前端启动

1. **安装依赖**
```bash
cd E:\git_project\art-design-pro
pnpm install
```

2. **启动开发服务器**
```bash
pnpm dev
```

前端将在 `http://localhost:5173` 启动（或其他可用端口）

3. **访问系统**
浏览器访问前端地址，系统会自动跳转到日志总览页面（无需登录）

## 📊 核心功能

### 1. 日志总览仪表板 (/logs/dashboard)

- **统计卡片**: 显示总日志数、错误数、警告数、请求总数
- **日志级别分布**: 饼图展示各级别日志占比
- **时间趋势图**: 折线图展示日志随时间的变化趋势
- **IP 访问统计**: 横向柱状图展示访问量 Top10 的 IP
- **最近日志**: 展示最新的 20 条日志，支持快速跳转详情
- **自动刷新**: 每 30 秒自动刷新数据

### 2. 日志列表页 (/logs/list)

- **按 request_id 分组**: 同一请求的所有日志作为一组展示
- **可展开查看**: 点击展开查看该组下的所有日志明细
- **多维度搜索**:
  - 请求 ID
  - 日志级别 (DEBUG/INFO/WARNING/ERROR/CRITICAL)
  - IP 地址
  - 模块名
  - 时间范围
  - 关键词搜索
- **分页加载**: 支持自定义每页条数 (10/20/50/100)
- **状态标识**: 自动识别包含错误的日志组

### 3. 日志详情页 (/logs/detail/:requestId)

- **基本信息**: 展示请求的完整信息（ID、IP、时间、持续时间等）
- **级别分布**: 饼图展示该请求下各级别日志分布
- **时间线展示**: 按时间顺序展示所有日志，清晰呈现请求流程
- **错误高亮**: 自动高亮显示 ERROR 和 CRITICAL 级别日志
- **执行统计**: 显示请求执行时长、调用次数等

## 🔌 API 接口

### 1. 获取日志总览统计
```
GET /api/logs/overview
```

### 2. 获取日志列表
```
GET /api/logs/list
参数:
  - request_id: 请求ID
  - level: 日志级别
  - ip: IP地址
  - module: 模块名
  - start_time: 开始时间 (YYYY-MM-DD HH:MM:SS)
  - end_time: 结束时间 (YYYY-MM-DD HH:MM:SS)
  - keyword: 关键词
  - page: 页码
  - page_size: 每页条数
```

### 3. 获取日志详情
```
GET /api/logs/detail/{request_id}
```

## 🎨 技术栈

### 后端
- **FastAPI**: 现代、快速的 Web 框架
- **Pydantic**: 数据验证和设置管理
- **Uvicorn**: ASGI 服务器
- **Python-dateutil**: 日期时间处理

### 前端
- **Vue 3**: 渐进式 JavaScript 框架
- **TypeScript**: 类型安全的 JavaScript
- **Element Plus**: Vue 3 UI 组件库
- **ECharts**: 数据可视化图表库
- **Pinia**: Vue 状态管理
- **Vue Router**: 路由管理
- **Axios**: HTTP 客户端
- **Tailwind CSS**: 实用优先的 CSS 框架

## 📝 数据模型

### LogEntry (单条日志)
- id: 日志ID
- ip: IP地址
- timestamp: 时间戳
- request_id: 请求ID
- level: 日志级别
- module: 模块名
- function: 函数名
- line: 行号
- message: 日志消息

### LogGroup (日志分组)
- request_id: 请求ID
- count: 日志条数
- start_time: 开始时间
- end_time: 结束时间
- duration_ms: 持续时间(毫秒)
- levels: 各级别日志数量
- ip: IP地址
- has_error: 是否包含错误
- logs: 日志列表

## 🔧 配置说明

### 后端配置

日志文件路径在 `app/api/logs.py` 中配置：
```python
LOG_FILE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "log.log")
```

### 前端配置

API 地址在 `.env.development` 中配置：
```env
VITE_API_PROXY_URL = http://localhost:8000
```

## 📈 性能优化

- **增量读取**: 支持大文件增量解析
- **缓存机制**: 前端缓存统计数据
- **分页加载**: 避免一次性加载大量数据
- **懒加载**: 图表和组件按需加载

## 🔐 安全建议

生产环境部署时请注意：
1. 修改 CORS 配置，指定允许的前端域名
2. 添加认证和授权机制
3. 使用 HTTPS
4. 限制 API 访问频率
5. 日志文件访问权限控制

## 📚 相关文档

- [Art Design Pro 官方文档](https://www.artd.pro/docs/zh)
- [FastAPI 官方文档](https://fastapi.tiangolo.com/)
- [Element Plus 文档](https://element-plus.org/)
- [ECharts 文档](https://echarts.apache.org/)

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

MIT License
