# Sub2API OpenAI OAuth 编排服务

一个最小可用的本地服务，使用 `FastAPI + requests + SQLite + React/Vite` 完成 Sub2API OpenAI OAuth 编排流程。

## 功能概览

已实现：

- `POST /provision/start`
  - 校验 email
  - 创建专属分组
  - 生成 OpenAI OAuth 登录链接
  - 不向 Sub2API OAuth 接口传入回调地址；上游使用固定回调配置
  - 将 flow 上下文持久化到 SQLite
- `POST /provision/oauth/complete`
  - 接收用户粘贴回来的 localhost callback URL
  - 从 callback URL 中解析 `code` 和 `state`
  - 通过持久化 flow 查回上下文
  - exchange OAuth code
  - 使用入口 email 作为 OpenAI OAuth 账号名称创建账号
  - 将 OAuth 账号绑定到目标分组
  - 更新 flow 状态并返回 JSON 结果
- `GET /`
  - 提供受保护的 React 前端页面，默认进入已有用户/已有分组编排
- `GET /login` + `POST /auth/login`
  - 使用固定用户名和启动时生成的密码登录
  - 登录成功后返回 `access_key`，并给浏览器设置 `HttpOnly` cookie
- `GET /ui/config`
  - 为 React 前端返回标题、固定登录用户名、OAuth redirect URI 和当前登录状态
- `GET /provision/flows`
  - 受保护的编排记录列表接口，支持按状态、分配模式和 email 过滤
- `GET /provision/flows/{flow_id}`
  - 受保护的编排详情接口，返回 flow 安全详情和步骤时间线
- `GET /orchestration/users`
  - 拉取 upstream 已有用户，并合并本地 assignment 作为当前分组上下文
- `GET /orchestration/groups`
  - 拉取 upstream 已有分组，并标记哪些分组支持整体 `replace-group`
- `GET /orchestration/users/{user_id}/api-keys`
  - 拉取某个已有用户的 API keys，用于单 key 分组调整
- `POST /orchestration/assignments/replace-group`
  - 对已有用户执行整体分组替换
  - 调用 upstream `POST /api/v1/admin/users/{user_id}/replace-group`
  - 不使用仅更新用户 `allowed_groups` 的方式作为编排生效路径
- `POST /orchestration/api-keys/update-group`
  - 对单个 API key 执行分组调整
  - 调用 upstream `PUT /api/v1/admin/api-keys/{key_id}`
- `POST /auth/logout`
  - 注销当前浏览器或 API 会话
- `GET /rotation/pool/candidates`
  - 拉取 upstream 当前分组
  - 区分专属组和非专属组
  - 标记哪些组已被选入本地轮换池
- `POST /rotation/pool/groups`
  - 将专属组加入本地轮换池
  - 支持设置轮换优先级
- `DELETE /rotation/pool/groups/{group_id}`
  - 从本地轮换池移除目标组
- `POST /rotation/manual`
  - 对已记录 assignment 的用户执行手动切组
  - 统一调用 upstream `replace-group`，自动迁移现有 key
  - 不使用仅更新用户 `allowed_groups` 的方式做轮换
- `POST /rotation/auto/run`
  - 按当前窗口和阈值执行自动轮换
  - 无 key 的用户会放到已有 key 用户之后调度
- 自动化测试
  - 覆盖 SQLite store 持久化行为
  - 覆盖登录、受保护接口、paste-back OAuth 编排流程和错误分支
  - 覆盖已有用户/分组编排、轮换池发现、managed-pool 预配、手动轮换、自动轮换

## 已有用户/分组编排

前端默认页是已有资源编排台，使用 Ant Design 做操作面板，并用可拖放的 React Flow 全局关系图按“所有 API Key → 所有用户 → 所有分组”的左到右顺序展示资源关系：

1. 选择已有 Sub2API 用户
2. 选择源分组和目标分组
3. 选择执行方式：
   - `整体替换`：调用 upstream `POST /api/v1/admin/users/{user_id}/replace-group`
   - `单 Key`：调用 upstream `PUT /api/v1/admin/api-keys/{key_id}`
4. 执行后本地记录 assignment 或 rotation event，方便后续轮换和审计

整体替换只允许目标为专属标准分组，因为 upstream `replace-group` 当前只支持这类分组。订阅分组不要走 `replace-group`；需要按实际 upstream 能力使用单 key 更新或其他专用接口。

## 关键流程

### OAuth 预配流程

1. 用户输入 email，调用 `POST /provision/start`
2. 服务创建分组，并返回 OAuth 登录链接
   - 分组创建默认携带 `platform=openai`
3. 用户手动点击 OAuth 登录链接
4. OAuth 提供方把浏览器跳到上游固定回调地址，通常是某个 localhost 地址
5. 用户把浏览器地址栏中的完整 callback URL 复制回来
6. 前端或调用方将该 URL 提交给 `POST /provision/oauth/complete`
7. 服务解析 `code/state`，继续完成 OAuth 账号创建和账号绑组
   - 账号名称强制使用入口 email
   - 账号默认按 `openai + oauth` 创建
   - 账号默认打开“临时不可调度”并附带 529/429/503 三条规则
   - `wsmode` 默认设置为 `context_pool`
   - 账号创建后仍会再调用一次账号绑组接口，确保绑定到上一步创建的专属分组

在进入这个流程之前，需要先登录：

1. 打开 `GET /login`
2. 用户名固定为 `app.auth_username`，默认 `admin`
3. 密码优先使用 `.env` 里的 `APP_AUTH_PASSWORD`
4. 登录成功后浏览器会拿到 `HttpOnly` cookie；API 调用方也可以复用登录返回的 `access_key`
5. 如果没有配置 `APP_AUTH_PASSWORD`，服务会在每次启动时生成临时密码并打印在启动日志里；服务重启后旧临时密码和旧 access key 会失效，需要重新登录

## 轮换池与自动轮换

### 轮换池选择

1. 调用 `GET /rotation/pool/candidates`
2. 从返回结果里挑选 `is_exclusive=true` 且 `is_subscription=false` 的专属标准组
3. 用 `POST /rotation/pool/groups` 加入本地轮换池
4. 通过 `priority` 指定顺序，数值越小越靠前

非专属组不会被允许加入轮换池；订阅分组也不会被允许加入轮换池，因为 upstream `replace-group` 当前只支持专属标准分组。

### managed-pool 预配

- 当 `provisioning.assignment_mode=managed_pool` 时，`POST /provision/start` 不再创建新专属组
- 服务会从本地轮换池里选择优先级最低的组作为默认目标组
- 如果轮换池为空，请求会失败

### 自动轮换策略

- V1 仅支持 4 个窗口：`5h`、`1d`、`7d`、`30d`
- `5h` / `1d` / `7d` 通过用户现有 API key 的窗口用量字段汇总
- `30d` 通过 upstream usage stats 聚合查询
- `auto_rotation.usage_thresholds` 需要是升序数组
- 实际切组使用 upstream `POST /api/v1/admin/users/{user_id}/replace-group`，由 upstream 迁移该用户旧分组下的 API keys 并失效认证缓存
- 正在进行中的流式请求、WebSocket 或已建立连接不会半路切换，需要下一次请求或重连才会使用新分组
- 轮换池中的组数量必须满足：
  - `len(rotation_pool_groups) == len(auto_rotation.usage_thresholds) + 1`

例子：

- 阈值是 `[10, 50]`
- 轮换池里有 3 个组，优先级从低到高分别是 `A -> B -> C`
- 用量 `<=10` 的用户会落到 `A`
- 用量 `>10 且 <=50` 的用户会落到 `B`
- 用量 `>50` 的用户会落到 `C`

### 推荐 rollout

1. 先保持 `provisioning.assignment_mode=dedicated`
2. 通过 `GET /rotation/pool/candidates` 和 `POST /rotation/pool/groups` 选出一小组专属轮换目标
3. 设置 `auto_rotation.usage_window`、`auto_rotation.usage_thresholds`、`auto_rotation.cooldown_minutes`
4. 先手动调用 `POST /rotation/auto/run` 验证策略
5. 再打开 `auto_rotation.interval_seconds`
6. 最后把 `provisioning.assignment_mode` 切到 `managed_pool`

### 回滚

1. 把 `provisioning.assignment_mode` 切回 `dedicated`
2. 关闭 `auto_rotation.enabled`
3. 把 `auto_rotation.interval_seconds` 设回 `0`
4. 如有需要，用 `POST /rotation/manual` 把用户迁回目标专属组
5. 再按需清理本地轮换池

## 目录结构

```text
.
├── .dockerignore
├── .env.example
├── .gitignore
├── build.sh
├── config.example.yaml
├── Dockerfile
├── README.md
├── docker-compose.yaml
├── requirements.txt
├── requirements-dev.txt
├── app
│   ├── __init__.py
│   ├── auth.py
│   ├── clients
│   │   ├── __init__.py
│   │   └── sub2api.py
│   ├── config.py
│   ├── errors.py
│   ├── logging_config.py
│   ├── main.py
│   ├── models
│   │   ├── __init__.py
│   │   ├── flow.py
│   │   └── schemas.py
│   ├── services
│   │   ├── __init__.py
│   │   └── provisioning.py
│   ├── stores
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── memory.py
│   │   └── sqlite.py
│   └── static
│       └── ui
│           └── # React build output
├── frontend
│   ├── index.html
│   ├── package.json
│   ├── src
│   │   ├── App.tsx
│   │   ├── main.tsx
│   │   └── styles.css
│   ├── tsconfig.json
│   └── vite.config.ts
├── openspec
│   ├── config.yaml
│   ├── changes
│   │   └── add-ephemeral-admin-auth
│   │       ├── design.md
│   │       ├── proposal.md
│   │       ├── specs
│   │       │   └── openai-oauth-provisioning
│   │       │       └── spec.md
│   │       └── tasks.md
│   └── specs
│       └── openai-oauth-provisioning
│           └── spec.md
└── tests
    ├── conftest.py
    ├── test_api.py
    └── test_sqlite_store.py
```

## 配置文件和环境变量

先复制两个模板：

```bash
cp config.example.yaml config.yaml
cp .env.example .env
```

`config.yaml` 放非敏感、可读性更强的运行配置，例如本服务地址、OAuth redirect、SQLite 路径、预配模式、自动轮换策略、Sub2API 默认 payload 和临时不可调度规则。

`.env` 只保留密钥和密码类字段：

```env
SUB2API_ADMIN_API_KEY=replace-me
APP_AUTH_PASSWORD=change-me
```

说明：

- `SUB2API_ADMIN_API_KEY` 是调用 Sub2API admin API 的密钥。
- `APP_AUTH_PASSWORD` 是本服务管理员登录密码，建议在 `.env` 中固定配置；如果留空或删除，服务会在每次启动时生成一个临时密码并打印到日志中。
- `CONFIG_PATH` 可选，默认读取项目根目录的 `config.yaml`。
- `app.base_path` 可选，默认空字符串；如果通过 Nginx Proxy Manager 挂在子路径，例如 `https://sub2api.example.com/sidecar/`，设置为 `/sidecar`。
- `auto_rotation.enabled=true` 且 `auto_rotation.interval_seconds > 0` 时，后台自动轮换才会启动；已登录管理员可请求 `GET /rotation/auto/scheduler` 确认是否运行。
- `credit_control.recharge_tick_seconds` 默认 `60`，用于后台执行到期充值策略；设为 `0` 会关闭后台执行。已登录管理员可请求 `GET /api/credit-control/scheduler` 确认是否运行。
- `notifications.scheduler_tick_seconds` 默认 `60`，用于后台定时评估告警规则；设为 `0` 会关闭后台评估。已登录管理员可请求 `GET /notifications/scheduler` 确认定时器是否启用、是否运行、最近一次 tick 时间和错误。
- 提交给 `POST /provision/start` 的 email 仅作为外部 OAuth 账号标识，不会创建 Sub2API 用户，也不会绑定任何 Sub2API 用户到分组；编排只创建专属 group 并完成 OAuth 账号挂接。

环境变量仍然可以覆盖 `config.yaml` 中的同名旧配置项，方便兼容已有部署和测试环境；新配置建议优先改 `config.yaml`。

### Nginx Proxy Manager 子路径部署

如果主 Sub2API 已经占用 `https://sub2api.example.com/`，sidecar 可以挂到同域名子路径，例如：

```yaml
app:
  base_url: https://sub2api.example.com/sidecar
  base_path: /sidecar

sub2api:
  base_url: http://sub2api:8080
```

在 Nginx Proxy Manager 的主站 Proxy Host 里新增 Custom Location：

```text
Location: /sidecar
Scheme: http
Forward Hostname / IP: sub2api-sidecar
Forward Port: 8000
```

这个 Custom Location 的 Advanced 配置：

```nginx
rewrite ^/sidecar/?(.*)$ /$1 break;
proxy_set_header X-Forwarded-Prefix /sidecar;
```

然后从浏览器访问：

```text
https://sub2api.example.com/sidecar/
```

## Sub2API 默认编排配置

这些默认值现在集中放在 `config.yaml` 的 `sub2api.provisioning_defaults`：

- 创建分组时携带 `platform=openai`
- 创建 OAuth 账号时携带 `provider=openai`
- 创建 OAuth 账号时携带 `platform=openai`
- 创建 OAuth 账号时携带 `type=oauth`
- 创建 OAuth 账号时携带 `wsmode=context_pool`
- 创建 OAuth 账号时打开 `temporary_unschedulable`
- 创建 OAuth 账号时附带三条默认停调规则：
  - `529` -> 暂停 `60` 分钟，关键词 `overloaded, too many`
  - `429` -> 暂停 `10` 分钟，关键词 `rate limit, too many requests`
  - `503` -> 暂停 `30` 分钟，关键词 `unavailable, maintenance`
- 创建 OAuth 账号时会把专属分组 ID 带入 payload
- 无论创建账号接口是否已经处理分组，服务仍然会再调用一次“账号绑组”接口，确保账号最终绑定到专属分组

## 安装依赖

安装后端依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

安装前端依赖并生成 React 静态资源：

```bash
cd frontend
npm ci
npm run build
cd ..
```

运行测试：

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
```

## 启动服务

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

前端本地开发可以单独启动 Vite，API 会代理到 `127.0.0.1:8000`：

```bash
cd frontend
npm run dev
```

如果 `.env` 没有配置 `APP_AUTH_PASSWORD`，启动后请查看日志，复制启动时打印的临时管理员密码。日志里会出现类似：

```text
Ephemeral admin credentials ready | username=admin | password=... | note=Copy this password from startup logs. It changes on every restart.
```

启动后访问：

- 登录页：[http://127.0.0.1:8000/login](http://127.0.0.1:8000/login)
- 首页/编排看板：[http://127.0.0.1:8000](http://127.0.0.1:8000)
- 健康检查：[http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)
- Ping 检查：[http://127.0.0.1:8000/ping](http://127.0.0.1:8000/ping)

登录后 React UI 默认进入“用户分组编排”。这里可以在拖放图上按 Key、用户、分组三列查看全局关系；点击图上的用户或 key 会同步左侧选择，再通过左侧面板选择目标分组执行整体 `replace-group` 或单 key 分组更新。切到“历史看板”可以查看历史 flow、按状态/email/分配模式过滤、刷新列表，并在详情面板查看 OAuth handoff、callback 示例、错误信息和步骤时间线。切到“OAuth 预配”可以继续使用原来的 email 发起和 callback paste-back 流程。

## Docker 启动

先准备配置文件和环境变量：

```bash
cp config.example.yaml config.yaml
cp .env.example .env
```

然后按需修改 `config.yaml` 和 `.env`。如果你希望容器内的 SQLite 文件稳定落在挂载卷里，推荐保持 `storage.sqlite_db_path: ./data/sub2api-sidecar.db`。

拉取镜像并启动：

```bash
docker pull cernss/sub2api-sidecar:latest
docker compose up -d
```

更新到最新镜像：

```bash
docker pull cernss/sub2api-sidecar:latest
docker compose up -d
```

停止服务：

```bash
docker compose down
```

查看启动日志里的临时登录密码：

```bash
docker compose logs -f
```

容器启动后访问：

- 登录页：[http://127.0.0.1:8000/login](http://127.0.0.1:8000/login)
- 首页/编排看板：[http://127.0.0.1:8000](http://127.0.0.1:8000)

说明：

- `docker-compose.yaml` 默认直接使用 `cernss/sub2api-sidecar:latest`，不会依赖本地 Dockerfile 构建
- 发布流程默认推送 `linux/amd64,linux/arm64` 多架构镜像，Docker 会按宿主机架构自动选择
- `docker-compose.yaml` 会读取项目根目录下的 `.env`，并把 `config.yaml` 挂载到容器内
- `docker-compose.yaml` 会显式创建并使用 `sub2api-sidecar` bridge network，方便与其它 Compose 服务按固定网络名互通
- `./data:/app/data` 会把 SQLite 数据库持久化到宿主机 `data/` 目录
- 如果你的 `sub2api.base_url` 指向宿主机本地服务，容器里通常不能直接用 `http://127.0.0.1:<port>`，需要改成宿主机可访问地址

## 镜像构建

项目根目录提供了一个 `build.sh`，使用 Docker Buildx 构建镜像。

先确保本机有：

- Docker
- Docker Buildx

默认构建：

```bash
./build.sh
```

自定义镜像名和 tag：

```bash
./build.sh --name myrepo/sub2api-sidecar --tag v1.0.0
```

推送到镜像仓库：

```bash
./build.sh --push --name registry.example.com/sub2api-sidecar --tag v1.0.0
```

`--push` 默认构建并推送 `linux/amd64,linux/arm64` 多架构镜像。

也可以通过环境变量覆盖：

```bash
IMAGE_NAME=myrepo/sub2api-sidecar IMAGE_TAG=v1.0.0 ./build.sh
```

脚本默认行为：

- 目标平台：`--load` 为 `linux/amd64`，`--push` 为 `linux/amd64,linux/arm64`
- 输出方式：`--load`
- Dockerfile：项目根目录 `Dockerfile`
- build context：项目根目录

## 测试命令

```bash
source .venv/bin/activate
pytest
```

## API 示例

### 1. 发起编排流程

先登录拿到 access key：

```bash
curl -X POST 'http://127.0.0.1:8000/auth/login' \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<APP_AUTH_PASSWORD 或启动日志里的临时密码>"}'
```

然后带着 `access_key` 调用：

```bash
curl -X POST 'http://127.0.0.1:8000/provision/start' \
  -H 'Content-Type: application/json' \
  -H 'X-Access-Key: <ACCESS_KEY>' \
  -d '{"email":"user@example.com"}'
```

示例返回：

```json
{
  "success": true,
  "flow_id": "...",
  "email": "user@example.com",
  "user_id": "...",
  "group_id": "...",
  "account_name": "user@example.com",
  "oauth_url": "https://...",
  "oauth_redirect_uri": "http://localhost:3000/callback"
}
```

其中 `oauth_redirect_uri` 只用于本地页面展示和人工核对；Sub2API 的 OAuth URL 生成与 code exchange 请求不再接收这个字段。

### 2. 粘贴 callback URL 完成 OAuth

```bash
curl -X POST 'http://127.0.0.1:8000/provision/oauth/complete' \
  -H 'Content-Type: application/json' \
  -H 'X-Access-Key: <ACCESS_KEY>' \
  -d '{"callback_url":"http://localhost:3000/callback?code=abc&state=xyz"}'
```

## 关键约束

- 入口 email 贯穿全流程。
- 创建 OpenAI OAuth 账号时，`name` 强制使用入口 email。
- `用户绑定分组` 和 `账号绑定分组` 两步都会执行。
- 轮换已有用户时不要只改用户 `allowed_groups`；必须使用 upstream `replace-group`，或在只迁移单个 key 的场景使用 `PUT /api/v1/admin/api-keys/{key_id}`。
- 页面和编排 API 需要先登录；登录密码默认每次启动重新生成。
- 编排看板只读；不会重试、删除、编辑或强制完成历史 flow。
- 编排看板会隐藏 OAuth token、密码、API key 等敏感字段。
- OAuth 授权本身由用户手动点击返回的 `oauth_url` 完成。
- OAuth 最终完成步骤依赖用户把 localhost callback URL 粘贴回来。
- 除 OAuth 授权本身外，其余步骤通过 Sub2API 管理接口完成。
- flow 状态默认持久化到 SQLite，而不是仅存在内存里。

## Sub2API 对接说明

考虑到不同 Sub2API 部署的接口路径和字段名可能略有差异，所有不确定部分都集中封装在：

- `app/clients/sub2api.py`

如果你的真实 Sub2API 返回结构不同，优先调整：

- 各方法的 `*_PATHS` 候选路径
- 请求 payload 字段
- `_extract_id()` / `_extract_value()` 的解析逻辑

这样不需要改 controller 和 service。

## 测试覆盖说明

当前测试包括：

- SQLite store 建表、保存、跨实例读取
- SQLite store 更新后重新读取
- 未登录访问 `/` 时跳转到 `/login`
- React 登录入口、`/ui/config`、登录成功、错误密码失败
- 编排看板列表/详情接口、过滤、分页、缺失 flow、事件时间线和敏感字段脱敏
- 受保护接口在未登录时拒绝访问
- `/provision/start` 在 cookie 和 access key header 模式下都可成功创建并写入 SQLite
- `/provision/oauth/complete` 在清空缓存并重新登录后仍可完成，验证 SQLite 持久化和 paste-back 流程生效
- 非法 email 返回 422
- 粘贴了不完整 callback URL 时返回 400
