# rpa-form

表单批量录入器，配一个本地模拟站点 `rpa/mock_server.py`。仅依赖 Python 标准库。

## 能力

- **会话与 CSRF**：先 `GET /form/<id>` 拿到 `sid` cookie 与页面内的 csrf token，再带两者 `POST /submit`；会话在连接内复用。服务端每 5 次提交让会话失效，收到 403 时自动重新取表单、换新 token 后**重试同一行**，不重跑整批；重试次数有上限（默认 3），超限记为失败并写明原因。
- **幂等**：提交前按 `biz_id` 在批内去重，同一主键只提交一次；上一轮已成功落库（SQLite）的行，本轮直接跳过，不重复提交。
- **数据校验前置**：必填为空、字段超长、枚举越界、数值非法均在本地拦截并记入 rejected，脏数据不发往服务端。允许集合与长度限制从 `config.json` 读取。
- **吞吐与恢复**：并发度可配置（默认 1），每个工作线程持有独立的 `Recorder` 连接，会话与 token 按连接隔离、互不串用；支持每秒 N 次的限速；每行结果落 SQLite（`biz_id / status / attempts / last_error`），中断重启跳过已成功的行。
- **报告与退出码**：输出 `total / submitted / duplicate_skipped / rejected / failed / retried` 六项计数及问题行明细；全部成功退出 0，存在失败或被拒退出 1，配置错误退出 2。

## 模块

| 文件 | 职责 |
| --- | --- |
| `rpa/core.py` | `Recorder`（保留 `fetch_form` / `submit` 方法签名）、csrf 解析、批量编排 `record_all`、403 重试 |
| `rpa/config.py` | 加载并校验 JSON 规则配置 |
| `rpa/validation.py` | 必填/长度/枚举/数值四类本地校验 |
| `rpa/state.py` | SQLite 断点续跑存储 |
| `rpa/ratelimit.py` | 每秒 N 次的全局限速器 |
| `rpa/cli.py` | 命令行入口、报告输出与退出码 |
| `rpa/mock_server.py` | 本地模拟站点（行为未改动） |
| `config.json` | 校验规则：必填、长度上限、枚举集合、数值字段 |

## 运行

```powershell
python gen_samples.py
python -m rpa.mock_server 8099
python -m rpa.cli rows.csv --form f001
python -m pytest tests -q
```

常用参数：

```
--config config.json      校验规则配置
--db rpa_state.db         断点续跑用的 SQLite 路径
--concurrency 4           并发连接数，默认 1
--rate-limit 10           每秒最多提交次数，0 表示不限速
--max-403-retries 3       单行 403 会话失效的最大重试次数
```

## 实测统计（`python -m rpa.cli rows.csv --form f001`）

`rows.csv` 共 12 行，其中 `B001` 重复一次，`B005` 名字为空、`B006` 名字超长、
`B007` 金额非数值、`B008` 枚举值非法。干净服务端首次运行输出：

```
批量录入完成：
  total              = 12
  submitted          = 7
  duplicate_skipped  = 1
  rejected           = 4
  failed             = 0
  retried            = 1
问题行明细：
  [rejected] B005 -> 必填字段 name 为空
  [rejected] B006 -> 字段 name 超长(60 > 20)
  [rejected] B007 -> 字段 amount 数值非法: 'abc'
  [rejected] B008 -> 字段 status 枚举值 'unknown' 不在允许集合 ['normal', 'vip']
```

- 7 个不同且合法的主键（B001–B004、B009–B011）全部录入成功；
- `B001` 第二次出现被本地去重，记 `duplicate_skipped`；
- 4 行脏数据在本地被拦，记 `rejected`，未发往服务端；
- 第 5 次提交后服务端清会话，`B009` 首次提交收到 403，自动换新会话重试成功，记 `retried = 1`（该行 `attempts = 2`）；
- 退出码为 1（存在被拒行）。

紧接着第二次运行（断点续跑，状态库保留）：

```
批量录入完成：
  total              = 12
  submitted          = 0
  duplicate_skipped  = 8
  rejected           = 4
  failed             = 0
  retried            = 0
```

7 个上一轮已成功的行与 1 个批内重复行全部跳过，没有任何重复提交。

## 测试

```
python -m pytest tests -q
```

`tests/test_flow.py` 在进程内启动 `mock_server`，覆盖：取 token 后提交成功、
会话失效后自动恢复、批内去重（不触发 409）、服务端 409 记失败、本地校验拦截、
403 重试上限、断点续跑、并发会话隔离、限速以及 CLI 三类退出码。
