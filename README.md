# Radar Rain-Intensity Merge API

短临预警用的本地 API：两部雷达（A/B）按递增修订号提交某 UTC 整点小时的
32×32 稀疏雨强格网，服务端在每个格点上选出最优观测，合成一张预警图。

技术栈：FastAPI + SQLite（WAL 模式），无外部服务依赖。

## 业务规则

1. **报告内容**：雷达标识（A/B）、UTC 整点小时、递增修订号（≥1）、稀疏格点列表
   （行列 0–31、非负雨强、0–100 整数质量分）。
2. **单份报告行列重复 → 整份拒收（HTTP 422）**，不写入任何数据。
3. **新修订整体替换**该雷达该小时的全部旧格点；未重报的格点随之消失。
   替换在单个 SQLite 写事务内完成（删旧 + 插新），写入失败自动回滚。
4. **修订号控制**：
   - 低版迟到 → `409 StaleRevisionError`，不得回退已有高版本；
   - 同版同内容重送 → 幂等无影响（按排序后内容的 SHA-256 判定，格点顺序无关）；
   - 同版异内容 → `409 RevisionConflictError`，保留原内容。
5. **逐格合并**：质量分最高者胜；质量并列取雨强较高者；再并列取雷达 A。
6. **查询返回**：每个胜出格点的雨强、质量分、来源雷达，以及两路各自的最新修订号
   （从未上报的一路修订号为 `null`）。
7. **一致性**：查询在单个 WAL 快照事务中读取两路数据，并发更正或写入失败时，
   不会把不同修订版本的来源和格点拼成一张图；存储中每雷达每小时永远只保留最新
   修订的格点。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/reports` | 提交一份完整稀疏格点报告 |
| `GET` | `/merge?hour=2026-11-15T08:00:00Z` | 查询某小时的合并图 |
| `GET` | `/health` | 健康检查 |
| `GET` | `/docs` | Swagger UI（根路径 `/` 重定向到此） |

`hour` 必须是带时区的 UTC 整点（如 `2026-11-15T08:00:00Z`，或
`2026-11-15T16:00:00+08:00` 这种等价表达）；naive 时间或非整点返回 422。

### 示例

```bash
# 雷达 A 提交 rev1
curl -X POST http://localhost:8000/reports -H 'Content-Type: application/json' -d '{
  "radar": "A",
  "hour": "2026-11-15T08:00:00Z",
  "revision": 1,
  "points": [
    {"row": 0, "col": 0, "rain_intensity": 3.5, "quality": 80},
    {"row": 10, "col": 12, "rain_intensity": 12.0, "quality": 95}
  ]
}'

# 查询合并图
curl 'http://localhost:8000/merge?hour=2026-11-15T08:00:00Z'
```

合并响应：

```json
{
  "hour": "2026-11-15T08:00:00Z",
  "grid_size": 32,
  "radars": {
    "A": {"revision": 2, "point_count": 1},
    "B": {"revision": 1, "point_count": 2}
  },
  "points": [
    {"row": 0, "col": 0, "rain_intensity": 4.0, "quality": 95, "source": "B"}
  ]
}
```

## 用 Docker Compose 启动

```bash
docker compose up --build
# API:      http://localhost:8000
# Swagger:  http://localhost:8000/docs  （/ 自动跳转）
# 数据持久化在命名卷 radar-data 中（/data/radar.db，SQLite WAL）
```

## 本地直接运行（无 Docker 时）

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
# 数据库路径可用环境变量 RADAR_DB_PATH 覆盖，默认 data/radar.db
```

## 测试

```bash
pip install -r requirements-dev.txt
pytest
```

测试共 22 个，分四组：

- `tests/test_validation.py`：行列重复整份拒收、取值范围、UTC 整点/时区校验；
- `tests/test_revisions.py`：替换语义、低版拒收、同版幂等/冲突、雷达与小时隔离；
- `tests/test_merge.py`：质量→雨强→雷达 A 的三级裁决及替换后的实时合并；
- `tests/test_atomicity.py`：写入失败回滚、WAL 快照不拼接、双雷达并发更正压力测试。

## 目录结构

```
app/
  config.py       # 环境变量与常量（32×32、雷达 A/B）
  time_utils.py   # UTC 整点校验与规范化
  models.py       # Pydantic 请求/响应模型
  database.py     # SQLite 连接、WAL 配置、建表脚本
  services.py     # 修订号控制、事务化替换、快照读取、逐格合并
  routes.py       # HTTP 路由
  main.py         # FastAPI 入口（lifespan 建表、全局异常处理）
tests/            # pytest 测试套件
Dockerfile
docker-compose.yml
```
