# Safari hCaptcha Monitor API

该服务为 `/Users/neverland/js_reverse/hcaptcha` 提供本地监控 API。它读取当前 Loguru `application_*.log` 与轮转后的 `.log.zip`，增量建立 SQLite 查询索引，并汇总 hCaptcha `data/service.db` 中的 token 用量。Token 变更由监控后端带服务端密钥转发给 hCaptcha 管理 API，账本仍只有 hCaptcha 服务负责写入。默认只保留最近两天的索引数据，Redis 已退出在线数据链路。

## 架构

```text
hcaptcha/logs/application_*  -> 增量采集/解析 -> art_server/data/monitor.db
hcaptcha/data/service.db     -----------------> 只读 token 汇总
hcaptcha /admin/tokens       <----------------- token 管理代理
hcaptcha /health             -----------------> 在线状态与进程指标
                                                   |
                                             FastAPI /api/logs
                                                   |
                                             Vue 监控前端
```

日志文件是原始事实，`monitor.db` 只是可重建索引。采集器记录每个活动文件的 byte offset，并对归档成员做完成标记；每条日志另有内容指纹，因此轮转后重新出现也不会重复。详细取舍见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 启动

```bash
python -m pip install -r requirements-dev.txt
cp .env.example .env
./start.sh
```

默认后端地址为 `http://127.0.0.1:8000`，OpenAPI 文档位于 `/docs`。默认会从同一工作区的 `js_reverse/hcaptcha` 定位数据；其他部署通过 `HCAPTCHA_ROOT` 覆盖。Token 管理需要将 `HCAPTCHA_ADMIN_SECRET` 配置为与 hCaptcha 服务相同的值，密钥只保留在后端环境中。

## API

- `GET /api/logs/overview?hours=24`：成功率、延迟、趋势、目标 host、token 和服务状态。
- `GET /api/logs/list`：按结果、host、IP、级别、时间和关键词查询 solve 请求。
- `GET /api/logs/detail/{request_id}`：完整请求日志时间线。
- `POST /api/logs/sync`：立即执行一次幂等增量同步。
- `POST /api/logs/cleanup`：传入 `{ "confirm": true }`，清空可重建监控索引并执行 `VACUUM` 回收 SQLite 空间；原始日志、token 数据和当前文件采集进度保持不变。
- `GET /api/logs/tokens`：实时读取完整 Token 账本统计和原值记录。
- `POST /api/logs/tokens`：新建或重置 Token。
- `PATCH /api/logs/tokens/{token_id}`：更新次数、状态和过期时间。
- `DELETE /api/logs/tokens/{token_id}`：删除没有进行中预留的 Token。

## 保留与清理

- 监控索引默认保留 2 天，由 `MONITOR_RETENTION_DAYS` 控制；后台每次同步都会删除过期日志、请求和链路 span，并移除已经不存在的日志源状态。
- hCaptcha 原始日志默认每天轮转、压缩为 zip 并保留 2 天，由 hCaptcha 服务的 `HCAPTCHA_LOG_ROTATION`、`HCAPTCHA_LOG_COMPRESSION` 和 `HCAPTCHA_LOG_RETENTION_DAYS` 控制。
- 监控 API 自身的 `monitor_*.log` 每天轮转并保留 14 天。
- 自动清理后的 SQLite 空闲页会继续复用，文件不一定立即变小；需要立即释放磁盘空间时，在监控首页点击清理按钮。

监控链路没有 Redis 或常驻日志缓存。页面展示的“索引”包含 SQLite 主文件、WAL 和共享内存文件的磁盘占用；“源日志”是 hCaptcha 原始日志文件占用。

## 运维与测试

```bash
python manage_logs.py sync
python manage_logs.py stats
python -m unittest discover -s tests -v
```

索引异常时停止服务并删除 `data/monitor.db`，下一次启动会从仍在保留期内的原始日志完整重建。原始日志的删除与压缩继续由 hCaptcha 服务的 Loguru retention 配置负责。

## 部署

服务本体用 supervisor 启动，**必须走 `start.sh`**，不要直接跑 `uvicorn`：`.env` 是
`start.sh` 加载的，绕过它服务会带着一套默认值静静起来（`nodes=[local@:43333]`、
`database=data/monitor.db`），健康检查全绿而面板显示全零。这个坑踩过一次。

前端是独立仓库 `art-design-pro`，构建产物才是被 nginx 服务的东西。**构建时不要在命令行
覆盖 `VITE_*`** —— `.env.production` 是权威，覆盖不会报错，只会产出一个能加载但所有接口
都 404 的包（页面正常打开、数据全空，看起来像后端故障）。完整说明和验证方法在那个仓库的
README「Deployment」一节。

换前端后要验的是 **API 而不是页面**，页面能打开不能证明接口对：

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://<host>/hcp_solver/api/logs/overview   # 200
```

跨解释器兼容两处值得记住，都是把面板从 3.14 机器搬到 3.10 机器时炸出来的，而且症状都不
指向真正的原因：`datetime.fromisoformat` 在 3.11 之前只接受 3 或 6 位小数，而服务写的是
**纳秒**（9 位），于是每一行都解析失败，唯一的信号是 `parse_failures` 上升伴随
`imported: 0` —— 读起来像输入损坏，不像解释器差异。以及 `asyncio.TimeoutError` 只有从
3.11 起才等同于内建的 `TimeoutError`，写成 `except TimeoutError` 在 3.10 上抓不住，同步
任务在第一个间隔后就死了，而它从不被 await，异常没有任何去处。两处都已修在解析器和循环
里，不靠钉死解释器版本。
