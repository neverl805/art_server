# Safari hCaptcha Monitor API

该服务为 `/Users/neverland/js_reverse/hcaptcha` 提供本地监控 API。它读取当前 Loguru `application_*.log` 与轮转后的 `.log.zip`，增量建立 SQLite 查询索引，并以只读方式汇总 hCaptcha `data/service.db` 中的 token 用量。默认只保留最近两天的索引数据，Redis 已退出数据链路。

## 架构

```text
hcaptcha/logs/application_*  -> 增量采集/解析 -> art_server/data/monitor.db
hcaptcha/data/service.db     -----------------> 只读 token 汇总
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

默认后端地址为 `http://127.0.0.1:8000`，OpenAPI 文档位于 `/docs`。默认会从同一工作区的 `js_reverse/hcaptcha` 定位数据；其他部署通过 `HCAPTCHA_ROOT` 覆盖。

## API

- `GET /api/logs/overview?hours=24`：成功率、延迟、趋势、目标 host、token 和服务状态。
- `GET /api/logs/list`：按结果、host、IP、级别、时间和关键词查询 solve 请求。
- `GET /api/logs/detail/{request_id}`：完整请求日志时间线。
- `POST /api/logs/sync`：立即执行一次幂等增量同步。

## 运维与测试

```bash
python manage_logs.py sync
python manage_logs.py stats
python -m unittest discover -s tests -v
```

索引异常时停止服务并删除 `data/monitor.db`，下一次启动会从仍在保留期内的原始日志完整重建。原始日志的删除与压缩继续由 hCaptcha 服务的 Loguru retention 配置负责。
