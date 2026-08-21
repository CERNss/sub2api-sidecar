# Sub2API OpenAI OAuth 编排服务

一个最小可用的本地服务，使用 `FastAPI + requests + PostgreSQL + React/Vite` 完成 Sub2API OpenAI OAuth 编排流程。

## 功能概览

已实现：

- `POST /provision/start`
  - 校验 email
  - 创建 `{email}_{platform}` 专属分组（同名同平台已存在则复用）
  - 生成 OpenAI OAuth 登录链接
  - 不向 Sub2API OAuth 接口传入回调地址；上游使用固定回调配置
  - 将 flow 上下文持久化到 PostgreSQL
- `POST /provision/oauth/complete`
  - 接收用户粘贴回来的 localhost callback URL
  - 从 callback URL 中解析 `code` 和 `state`
  - 通过持久化 flow 查回上下文
  - exchange OAuth code
  - 使用入口 email 作为 OpenAI OAuth 账号名称创建账号
  - 将 OAuth 账号绑定到目标分组
  - 更新 flow 状态并返回 JSON 结果
- `POST /provision/apikey/start`
  - 不走 OAuth：直接用输入的 `name`、`api_base_url`、`api_key` 创建 OpenAI **API Key** 账号（`type=api_key`，credentials 写入 `api_key`/`base_url`）
  - 分组解析与账号默认配置（并发、`model_mapping`、临时不可调度规则等）与 OAuth 流程完全一致
  - 同步完成（无回调步骤），创建并绑定分组后直接返回 `status=completed`
  - 账号 type 可通过 `SUB2API_ACCOUNT_APIKEY_TYPE` 或 `sub2api.provisioning_defaults.account_apikey_type` 配置（默认 `api_key`）
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
  - 返回 `assignments: [{platform, group_id, group_name}]`，每个平台一条；某平台上绑了多个分组或分组没有 platform 时，该平台不出现在列表里
  - `current_group_id` / `current_group_name` 是过渡字段，取 `assignments` 里 `platform=openai` 的那条
- `GET /orchestration/groups`
  - 拉取 upstream 全部平台的已有分组（不按平台过滤），每条带自己的 `platform`
  - 并标记哪些分组支持整体 `replace-group`
- `GET /orchestration/users/{user_id}/api-keys`
  - 拉取某个已有用户的 API keys，用于单 key 分组调整
- `POST /orchestration/assignments/replace-group`
  - 对已有用户执行整体分组替换
  - 调用 upstream `POST /api/v1/admin/users/{user_id}/replace-group`
  - 不使用仅更新用户 `allowed_groups` 的方式作为编排生效路径
- `POST /orchestration/api-keys/update-group`
  - 对单个 API key 执行分组调整
  - 调用 upstream `PUT /api/v1/admin/api-keys/{key_id}`
- `POST /api/v1/apikey`
  - Bearer token / `X-Access-Key` 鉴权的自动化接口
  - `action=create` 按指定 `name` 创建 API key，复用密钥管理里的邮箱解析和首个可用分组选择逻辑
  - `action=create` 可选传 `platform`，只在目标用户该平台的分组里选组；不传等于 `openai`
  - `action=list` 列出 `service:environment:object:version:email` 格式 key，可用 `email` 精确过滤
- `POST /auth/api-token`
  - 登录后生成可用于自动化调用的长期 bearer-compatible API token
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
  - 覆盖 PostgreSQL store 持久化行为
  - 覆盖登录、受保护接口、paste-back OAuth 编排流程和错误分支
  - 覆盖已有用户/分组编排、轮换池发现、专属组预配复用、手动轮换、自动轮换

## 平台维度的分组规则

核心业务规则：**一个用户在每个平台上最多一个专属分组，以及一把绑在该分组上的 key**。

- 平台（platform）来自上游 `group.platform` 字段，是不透明字符串，不是枚举；当前实际用到的是 `openai` 和 `grok`
- 判断一个分组/key/绑定属于哪个平台，一律读 `group.platform`，**不解析分组名**
- 分组命名习惯是 `{email}_{platform}`（预配建组时生成）。名字太长时从 email 一侧截断，保证 `_{platform}` 后缀不被吃掉；后缀只是给人看的标签，程序不从名字里反解平台
- 上游用户只有 `allowed_groups`，所以“当前直属分组”是按平台分桶后**恰好只有一个分组**的那个桶；同平台绑了多个分组属于违规，按“没有直属分组”处理并打 warning 日志
- 换组、分组迁移、key transfer 都拒绝跨平台；某平台还没有分组时才允许直接授权加入，已经有分组则要求显式指定要替换哪个
- 本地绑定表按 `(user, platform)` 存行（复合主键），不同平台的绑定互相独立轮换
- 轮换池行记录自己的 platform，自动轮换在平台桶内做负载均衡；landing pool 也按平台过滤

升级注意：本地 `user_group_assignments` 表的主键从 `user_id_key` 变成 `(user_id_key, platform)`。启动时如果发现是旧表结构，会直接删表重建，旧的 assignment 记录会丢失，下一轮同步会从上游重新建立；`rotation_pool_groups` 是原地加 `platform` 列，已有轮换池不受影响。

## 已有用户/分组编排

前端默认页是已有资源编排台，使用 Ant Design 做操作面板，并用可拖放的 React Flow 全局关系图按“所有 API Key → 所有用户 → 所有分组”的左到右顺序展示资源关系：

1. 选择已有 Sub2API 用户
2. 选择源分组和目标分组
3. 选择执行方式：
   - `整体替换`：调用 upstream `POST /api/v1/admin/users/{user_id}/replace-group`
   - `单 Key`：调用 upstream `PUT /api/v1/admin/api-keys/{key_id}`
4. 执行后本地记录 assignment 或 rotation event，方便后续轮换和审计

整体替换只允许目标为专属标准分组，因为 upstream `replace-group` 当前只支持这类分组。订阅分组不要走 `replace-group`；需要按实际 upstream 能力使用单 key 更新或其他专用接口。

源分组和目标分组必须是同一个平台，跨平台会直接被拒绝。源分组要等于该用户**在目标分组所属平台上**的直属分组；不传源分组时，只有该用户在这个平台上一个分组都没有才允许直接授权加入，否则会返回“已绑了 N 个分组，请指定要替换哪个”。分组到分组迁移同理：只搬同平台的 key，其他平台的 key 原地不动。

上游写入语义（`app/clients/sub2api.py`）：

- 换组用 upstream `replace-group` 定向替换，它会同时迁移旧组下的 key；不要用改写 `allowed_groups` 代替，那样会撤销授权但不迁移 key，把其他组的 key 变成 403 孤儿
- 授权加入某个分组时，先读用户再 PUT `allowed_groups` 的**并集**；上游用户更新接口没有顶层 `group_id` 字段，传了会被静默丢弃
- 账号绑分组走 `PUT /api/v1/admin/accounts/{account_id}`，`group_ids` 同样是先读后取**并集**，并带上 `confirm_mixed_channel_risk`；上游没有 `POST /api/v1/admin/groups/{group_id}/accounts` 这条路由（恒 404）
- 更新已有账号（改名、换凭证）时 `group_ids` 也取并集，避免顺带把账号从其他分组上解绑

## 自动化 API Key 接口

在 `密钥管理` 页面点击 `查看 API Token` 打开弹窗，再点击弹窗里的 `刷新 API Token` 获取长期 token；也可以用已登录会话调用 `POST /auth/api-token` 获取长期 token。每次刷新会让同一用户之前生成的 API token 失效，但不会影响当前浏览器登录会话。调用方可以用 `Authorization: Bearer <token>` 或 `X-Access-Key: <token>` 访问：

```bash
curl -sS -X POST https://sub2api.tcgcard.jp/sidecar/api/v1/apikey \
  -H "Authorization: Bearer $SIDECAR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"create","name":"svc:prod:obj:v1:user@example.com","quota":0}'
```

创建时 `name` 必须是 `service:environment:object:version:email` 格式。`target` 为空时，会沿用 key 名最后一段邮箱作为目标用户；如果能精确匹配一个 Sub2API 用户，会放到该用户在目标平台上第一个可用分组；没有对应账号时默认放到 admin 用户下。请求里的 `group_id` / `group_ids` 会被忽略，分组只由 sidecar 选择。旧的 `service:object:version:email` 格式不会被创建或匹配。

`platform` 可选，缺省 `openai`（兼容多平台改造之前的调用方）。选组时只看目标用户在这个平台上的分组（按上游 `group.platform` 判断，不看分组名）；该用户在这个平台上没有可用分组时直接报错，错误信息里会带上平台名，不会退到别的平台的分组：

```bash
curl -sS -X POST https://sub2api.tcgcard.jp/sidecar/api/v1/apikey \
  -H "Authorization: Bearer $SIDECAR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"create","name":"svc:prod:obj:v1:user@example.com","platform":"grok","quota":0}'
```

也可以显式传 `target` 强制指定用户：

```bash
curl -sS -X POST https://sub2api.tcgcard.jp/sidecar/api/v1/apikey \
  -H "Authorization: Bearer $SIDECAR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"create","name":"svc:prod:obj:v1:user@example.com","target":"forced@example.com","quota":0}'
```

显式 `target` 不存在或不唯一时不会 fallback 到 admin，会返回 `{"success":false,"status":"USER_NOT_FOUND"}` 或 `{"success":false,"status":"USER_EMAIL_NOT_UNIQUE"}`。

当目标用户在该平台上有多个可用分组时，默认沿用旧逻辑选择第一个可用分组；可以在 `config.yaml` 设置 `sub2api.api_key_group_selection: random`，让 API key 创建时从该用户在该平台的可用分组中随机选择一个。这个配置只影响自动化 API Key 创建，不影响自动轮换或迁移。

```bash
curl -sS -X POST https://sub2api.tcgcard.jp/sidecar/api/v1/apikey \
  -H "Authorization: Bearer $SIDECAR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"list","email":"user@example.com"}'
```

列表接口只返回 `service:environment:object:version:email` 格式的 key，不返回原始 key 值，并会返回解析后的 `key_service`、`key_environment`、`key_object`、`key_version` 和 `target_email`。

## 关键流程

### OAuth 预配流程

1. 用户输入 email，调用 `POST /provision/start`
2. 服务按 email 解析专属分组，并检查上游是否已有同名或同邮箱 OAuth 账号
   - 专属分组名是 `{email}_{platform}`；已存在同名**且同平台**的分组才复用，同名但在别的平台上的分组算另一个槽位，不会被复用
   - 没有可复用分组时：默认上游会先看 landing pool（只看同平台的 landing 分组），都没有才创建新分组
   - 分组创建携带的 `platform` 来自 `sub2api.provisioning_defaults.group_platform`（默认 `openai`）
3. 如果已有同名或同邮箱 OAuth 账号，服务会保留现有 OAuth token，按默认账号配置更新该账号并补齐分组绑定，然后直接返回 `status=completed`、`oauth_required=false`、`oauth_url=null`
4. 如果没有匹配账号，服务返回 OAuth 登录链接
5. 用户手动点击 OAuth 登录链接
6. OAuth 提供方把浏览器跳到上游固定回调地址，通常是某个 localhost 地址
7. 用户把浏览器地址栏中的完整 callback URL 复制回来
8. 前端或调用方将该 URL 提交给 `POST /provision/oauth/complete`
9. 服务解析 `code/state`，继续完成 OAuth 账号创建，以及账号绑组
   - 账号名称强制使用入口 email
   - 如果回调期间发现同名或同邮箱 OAuth 账号已经存在，则复用该账号
   - 账号默认按 `openai + oauth` 创建
   - 账号默认打开“临时不可调度”并附带 529/429/503 三条规则
   - `wsmode` 默认设置为 `context_pool`
   - 如果已有账号已经绑定目标分组，不会重复绑组；缺少绑定时才补绑到目标分组

### API Key 预配流程

前端「账号预配」页提供 OAuth / API Key 两种模式切换。API Key 模式不走 OAuth：

1. 用户输入 `名称`、`API 地址`、`API Key` 三项，调用 `POST /provision/apikey/start`
2. 服务按 `名称` 解析专属分组（与 OAuth 同样的逻辑：复用同名同平台分组 / 同平台 landing pool / 新建专属分组）
3. 直接创建 `type=api_key` 账号，credentials 写入 `api_key` 和 `base_url`；并发、`model_mapping`、临时不可调度规则等默认配置与 OAuth 流程一致
4. 绑定到目标分组、补齐默认定时测试计划后，同步返回 `status=completed`（无回调步骤）
5. 如已存在同名且类型为 API Key 的账号，则更新其凭证并补齐绑定，避免重复创建；同名但为 OAuth 的账号不会被改写

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

轮换池行会记下分组的 platform，候选和池列表接口也会返回 platform。轮换池可以同时放多个平台的分组，自动轮换会自己按平台分桶，不需要为每个平台单独配一份策略。

### 自动轮换策略

- V1 仅支持 4 个窗口：`5h`、`1d`、`7d`、`30d`
- `5h` / `1d` / `7d` 通过用户现有 API key 的窗口用量字段汇总
- `30d` 通过 upstream usage stats 聚合查询
- 自动轮换的业务策略通过后台页面或 `/rotation/auto/config` 运行时配置保存，不再放进 `config.yaml`
- 负载均衡严格在平台桶内进行：候选只会被挪到**自己平台**的轮换池分组，最小负载选组、dead band（`imbalance_epsilon`）和单次移动增益（`improvement_delta`）都在桶内算
- 用户在某个平台上的绑定，如果这个平台在轮换池里一个分组都没有，会被 skip 并在原因里点名该平台
- 同步已有用户时按 `(user, platform)` 出行；某平台绑了多个分组、或分组没有 platform 的，分别计入 `skipped_multiple_groups_on_platform`、`skipped_unknown_group_platform`，不做猜测
- 执行前还会再校验一次：目标分组平台和该绑定的平台不一致时记为 failed，不会调用 upstream 替换接口
- key 只跟着同平台走：当前挂在别的平台分组下的 key 原地不动（没挂任何分组的 key 可以跟随）
- 实际切组使用 upstream `POST /api/v1/admin/users/{user_id}/replace-group`，由 upstream 迁移该用户旧分组下的 API keys 并失效认证缓存
- 正在进行中的流式请求、WebSocket 或已建立连接不会半路切换，需要下一次请求或重连才会使用新分组

### 推荐 rollout

1. 通过 `GET /rotation/pool/candidates` 和 `POST /rotation/pool/groups` 选出一小组专属轮换目标
2. 通过动态编排页面或 `/rotation/auto/config` 设置 usage window、cooldown、阈值、dead-band 等运行时策略
3. 先手动调用 `POST /rotation/auto/run` 验证策略
4. 再在动态编排页面打开运行时自动轮换开关

### 回滚

1. 在动态编排页面关闭运行时自动轮换开关
2. 如有需要，用 `POST /rotation/manual` 把用户迁回目标专属组
3. 再按需清理本地轮换池

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
│   │   └── postgres.py
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
    └── test_postgres_store.py
```

## 配置文件和环境变量

先复制两个模板：

```bash
cp config.example.yaml config.yaml
cp .env.example .env
```

`config.yaml` 放非敏感、启动前必须确定的部署配置，例如本服务地址、数据库地址和库名、OAuth redirect、Sub2API 连接参数、Sub2API 默认 payload 和临时不可调度规则。Sub2API admin key、PostgreSQL 密码、登录密码这类密钥和密码放 `.env`。自动轮换、自动充值、运行态数据采集这类运行时开关和策略在后台页面/API 保存到 PostgreSQL，不再放进 `config.yaml`。

`.env` 只保留密钥和密码类字段：

```env
SUB2API_ADMIN_API_KEY=replace-me
# 配了多个 sub2api.upstreams 时，每个 admin_api_key_env 都要在这里提供。
# SUB2API_BACKUP_ADMIN_API_KEY=replace-me-too
POSTGRES_PASSWORD=change-me-postgres-password
APP_AUTH_PASSWORD=change-me
```

数据库非密钥配置写在 `config.yaml`：

```yaml
database:
  url: sidecar-postgres
  port: 5432
  username: sub2api_sidecar
  name: sub2api_sidecar
  pool_max_size: 10
```

说明：

- `database.pool_max_size`（环境变量 `DATABASE_POOL_MAX_SIZE`，默认 `10`）是共享数据库连接池的最大物理连接数。整个进程按 database URL 共享一个连接池,避免每次查询都新建连接;请把它设得明显低于 PostgreSQL 的 `max_connections`。
- `SUB2API_ADMIN_API_KEY` 是调用默认 Sub2API admin API 的密钥。每个 `sub2api.upstreams[*].admin_api_key_env` 对应一个 `.env` 密钥。
- `POSTGRES_PASSWORD` 是 PostgreSQL 密码。数据库 url、port、username、name 写在 `config.yaml` 的 `database` 段；其中 `database.url` 是主机名或 IP，不是整条 DSN。应用会自己拼接连接串，部署时不要提供 `DATABASE_URL`、`POSTGRES_DB` 或 `POSTGRES_USER`。
- `APP_AUTH_PASSWORD` 是本服务管理员登录密码，建议在 `.env` 中固定配置；如果留空或删除，服务会在每次启动时生成一个临时密码并打印到日志中。
- `CONFIG_PATH` 可选，默认读取项目根目录的 `config.yaml`。
- URL 类启动配置放在 `config.yaml`，包括 `app.base_url`、`app.base_path`、`openai.oauth_redirect_uri`、`sub2api.upstreams[*].base_url`、以及 `database.url`；不要写进 `.env`。
- 当前运行时只支持 PostgreSQL，不再支持 SQLite，也不做 SQLite 数据迁移。
- `app.base_path` 默认空字符串；如果通过 Nginx Proxy Manager 挂在子路径，例如 `https://sub2api.example.com/sidecar/`，设置为 `/sidecar`。
- 预配页面不再提供 assignment mode 切换；`POST /provision/start` 会先复用入口 email 在该平台上的专属分组。没有专属分组且配置了同平台 landing 分组时，新 OAuth 账号会进入其中优先级最高的一个并把 flow 记录为 `managed_pool`；该平台没有可用 landing 分组时才创建新的 `{email}_{platform}` 专属分组并记录为 `dedicated`。
- 自动轮换执行开关和业务策略通过动态编排页面或 `/rotation/auto/config` 运行时配置；已登录管理员可请求 `GET /rotation/auto/scheduler` 查看后台调度线程和当前运行时开关。
- 自动充值后台执行开关通过额度控制页面或 `/api/credit-control/settings` 运行时配置；已登录管理员可请求 `GET /api/credit-control/scheduler` 查看状态。
- 运行态数据采集开关、`collect_interval_seconds`、`expiration`、`retention_seconds` 和 `max_storage_mb` 通过全局设置或 `/api/operational-data/settings` 运行时配置，不写进 `config.yaml`。采集线程默认 60 秒一轮，按当前 PostgreSQL 设置调整间隔，按顺序拉取 Sub2API accounts、groups、users、用户 usage、用户 API keys、当天 usage、昨天 usage，先落 PostgreSQL raw snapshots 和派生 metrics，再由告警、自动编排、额度控制读取本地数据。`expiration` 不设置表示本地数据永不过期，设置为正整数秒时，告警读取到缺失或过期样本会记为 `no_data`，不会触发告警。`retention_seconds` 按时间删除旧 snapshots/metrics，`max_storage_mb` 按大小从最老记录开始清理并保留每个 source/metric 的最新记录。已登录管理员可请求 `GET /api/operational-data/status` 查看采样状态、当前占用、每个数据源状态、最近一次 tick 时间和错误。
- 告警白名单用于过滤"已知/刻意状态"导致的误报，只作用于两类告警：
  - **账号白名单**（`ids` / `names` / `emails`）只作用于 `account_invalid`（账号失效）告警——命中的账号即使失效也不告警；不影响限流、需重授、容量、额度、Key 健康等其他账号信号，也不影响运维聚合（`admin_ops_alert`/`admin_dashboard`）。
  - **分组白名单**（`ids` / `names`）只作用于 `group_capacity_full`（分组容量满载）告警——命中即整组排除。分组容量本身仍按真实账号容量统计，白名单只影响是否告警，不改变计算。
  - 配置入口：通知页「账号 / 分组告警白名单」常驻区块（写入数据库，改完即时生效，无需重启）。也可配置一份静态基础白名单（与 UI 取并集）：账号用 `config.yaml` 的 `notifications.account_invalid_whitelist.ids/names/emails` 或环境变量 `NOTIFICATION_ACCOUNT_INVALID_WHITELIST_IDS/NAMES/EMAILS`；分组用 `notifications.group_whitelist.ids/names` 或环境变量 `NOTIFICATION_GROUP_WHITELIST_IDS/NAMES`（逗号分隔）。匹配不区分大小写。
- 提交给 `POST /provision/start` 的 email 仅作为外部 OAuth 账号标识，不会创建 Sub2API 用户，也不会绑定任何 Sub2API 用户到分组；编排只解析目标 group，并完成 OAuth 账号挂接。

环境变量可以覆盖 `config.yaml` 中的同名配置项；已移除的运行时环境变量不再兼容，出现时会直接启动失败。新部署建议优先改 `config.yaml`。

### Sub2API 上游配置

上游必须写在 `sub2api.upstreams`。即使只管理一个上游，也要配置为只有一个元素的数组：

```yaml
sub2api:
  request_timeout_seconds: 30
  usage_log_max_items: 100000
  request_max_retries: 2
  api_keys_fetch_concurrency: 8
  api_key_group_selection: random
  upstreams:
    - id: main
      name: 主站 Sub2API
      base_url: http://sub2api:8080
      admin_api_key_env: SUB2API_ADMIN_API_KEY
    - id: us-proxy-2
      name: US Proxy 2
      base_url: https://us-proxy-2.sub2api.tcgcard.jp
      admin_api_key_env: SUB2API_US_PROXY_2_ADMIN_API_KEY
```

`usage_log_max_items`（环境变量 `SUB2API_USAGE_LOG_MAX_ITEMS`，默认 `100000`，`0` 表示不限制）限制单次拉取用量明细时在内存中累积的最大条数。运行态数据采集每个周期会把当天和前一天的全部用量明细拉进内存，量大时可能触发 OOM；该上限是防止进程被 OOM kill 的安全阀。命中上限时会打印 warning，并可能导致该时间窗的聚合数值偏低——如属预期，可调大该值或缩短用量窗口。

`request_max_retries`（环境变量 `SUB2API_REQUEST_MAX_RETRIES`，默认 `2`，`0` 关闭）只对幂等的 GET 读请求做自动重试,采用指数退避并遵循上游的 `Retry-After`,触发条件是 429/500/502/503/504。账号创建、OAuth code 交换、余额变更等 POST 接口**永不**自动重试,避免重复副作用。

`api_keys_fetch_concurrency`（环境变量 `SUB2API_API_KEYS_FETCH_CONCURRENCY`，默认 `8`）是拉取“全部用户 API key”时逐用户请求的线程池并发度,用于消除 N+1 的串行等待;同时它也决定底层 HTTP 连接池大小(`pool_maxsize`)。结果顺序与串行版本一致。

第一个 upstream 是默认上游，也是 Sub2API 跳转携带 `token` 登录 sidecar 时唯一验证 JWT 的主站；后续 upstream 按从站处理，不参与跳转 token 登录。登录后的顶部“当前用户”区域会显示全局 Sub2API 切换器，切换目标后，用户分组编排、密钥管理和 OAuth 预配会把对应 `upstream_id` 发给后端并访问该从站。也可以通过 `GET /api/upstreams` 读取可选上游，响应只包含 `id/name/base_url/is_default`，不会返回 admin key。运行态数据采集会遍历所有 upstream：主站继续写入原有 source/metric key，从站写入 `upstream:<id>:` 前缀的 source/metric key，避免不同站点数据混在一起；余额管理、用量分层、自动轮换仍消费主站的兼容 key。

### Nginx Proxy Manager 子路径部署

如果主 Sub2API 已经占用 `https://sub2api.example.com/`，sidecar 可以挂到同域名子路径，例如：

```yaml
app:
  base_url: https://sub2api.example.com/sidecar
  base_path: /sidecar

sub2api:
  upstreams:
    - id: main
      name: 主站 Sub2API
      base_url: http://sub2api:8080
      admin_api_key_env: SUB2API_ADMIN_API_KEY
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

- 创建分组时携带 `platform=openai`（`group_platform` 是自由字符串，不是枚举；它只是调用方没指定平台时的默认值，下游所有平台判断读的是上游 `group.platform`）
- 创建 OAuth 账号时携带 `provider=openai`
- 创建 OAuth 账号时携带 `platform=openai`
- 创建 OAuth 账号时携带 `type=oauth`
- 创建 OAuth 账号时携带 `wsmode=context_pool`
- 创建 OAuth 账号时携带 `concurrency=6`
- 创建 OAuth 账号时打开 `temporary_unschedulable`
- 创建 OAuth 账号时附带模型白名单：`gpt-5.3-codex`、`gpt-5.4`、`gpt-5.4-mini`、`gpt-5.5`、`codex-auto-review`、`gpt-images-2`
- 创建 OAuth 账号时附带三条默认停调规则：
  - `529` -> 暂停 `60` 分钟，关键词 `overloaded, too many`
  - `429` -> 暂停 `10` 分钟，关键词 `rate limit, too many requests`
  - `503` -> 暂停 `30` 分钟，关键词 `unavailable, maintenance`
- 创建 OAuth 账号时会把目标分组 ID 带入 payload：优先使用已有的同平台 `{email}_{platform}` 专属分组，其次使用同平台 landing pool，最后才创建新的专属分组
- 如果已存在同名或同邮箱 OAuth 账号，`POST /provision/start` 会跳过授权登录，保留已有 OAuth token，只更新账号默认配置并确保绑定到目标分组

这份模板还有一层平台维度：上面列的是 base，`provisioning_defaults.per_platform` 按平台名只覆盖它点名的键。
不写 `per_platform` 时行为与没有这一层完全一致。可覆盖的键见 `config.example.yaml`，另有两个只在模板里出现的键：
`account_base_url`（写进 `credentials.base_url`，留空则不发）和 `account_extra`（自由键值，合并进账号 `extra`）。

base 里的字段分两类：

- **全平台通用**：`account_type`、`account_apikey_type`、`account_concurrency`、`account_priority`、
  `account_rate_multiplier`、`account_auto_pause_on_expired`、`account_temporary_unschedulable(_rules)`、
  `account_base_url`、`account_extra`。停调规则是通用 HTTP 错误码，线上 grok 账号也是同一套。
- **只作用于默认平台 `account_platform`（生产 = openai）**：`account_model_whitelist`、`account_ws_mode`。
  这两项描述的是 openai 形态的账号（模型清单、`openai_oauth_responses_websockets_v2_*` 传输层键），
  其他平台**不继承**，必须在 `per_platform.<平台>` 里显式配置才生效；不配就是不发 `model_mapping`、
  不发 ws 键（上游对缺失 `model_mapping` 有运行时兜底）。否则将来给 anthropic / gemini 建号时，
  会被静默写进 openai 的 `gpt-5.x` 模型映射，把账号配废。

sidecar 内置了 grok 的平台默认值，不配也生效：模型白名单 `composer-2.5`、`grok-4.5`、`grok-4.6`、
`grok-4.6-latest`（恒等映射），`account_base_url=https://cli-chat-proxy.grok.com/v1`（OAuth 账号用的
代理域名，与 API Key 账号的 `api.x.ai` 不是一回事），`account_extra.grok_client_tool_cache_enabled=true`，
以及空的 `account_ws_mode`（`openai_oauth_responses_websockets_v2_*` 是 openai 传输层的键，grok 账号不带）。
停调规则与并发度等则沿用 base，与线上手工配置的 grok 账号一致。

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

然后按需修改 `config.yaml` 和 `.env`，至少要在 `config.yaml` 设置 `app.base_url`、`openai.oauth_redirect_uri`、`database` 段、`sub2api.upstreams`；在 `.env` 设置对应的 Sub2API admin key、`POSTGRES_PASSWORD` 和 `APP_AUTH_PASSWORD`。新部署直接使用 PostgreSQL 空库启动，不需要也不会执行 SQLite 数据迁移。

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
- `docker-compose.yaml` 会读取项目根目录下的 `.env`，把所有密钥环境变量传给 sidecar 容器，并把 `config.yaml` 挂载到容器内
- `docker-compose.yaml` 会启动服务名和容器名均为 `sidecar-postgres` 的 `postgres:17-alpine`，默认创建 `sub2api_sidecar` 用户和同名数据库，用 `POSTGRES_PASSWORD` 设置密码，并用 `postgres-data` volume 持久化 PostgreSQL 数据
- `docker-compose.yaml` 使用外部 `npm-network`，启动前请确保该网络已存在
- 如果你的 `sub2api.upstreams[*].base_url` 指向宿主机本地服务，容器里通常不能直接用 `http://127.0.0.1:<port>`，需要改成宿主机可访问地址

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
  "status": "pending_oauth",
  "oauth_required": true,
  "oauth_account_id": null,
  "oauth_url": "https://...",
  "oauth_redirect_uri": "http://localhost:3000/callback"
}
```

如果上游已有同名或同邮箱 OAuth 账号，返回会是 `status=completed`、`oauth_required=false`、`oauth_account_id=<已有账号 ID>`，且 `oauth_url` 为 `null`，调用方无需再打开授权链接或提交 callback。

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
- 一个用户每个平台最多一个专属分组 + 一把绑该分组的 key；平台一律读上游 `group.platform`，不解析分组名。
- 换组、分组迁移、key transfer 都不允许跨平台。
- 轮换已有用户时不要只改用户 `allowed_groups`；必须使用 upstream `replace-group`，或在只迁移单个 key 的场景使用 `PUT /api/v1/admin/api-keys/{key_id}`。
- 写 `allowed_groups`（用户）和 `group_ids`（账号）时必须先读后取并集：这两个字段在上游是声明式的，直接覆盖等于撤销其他绑定，且不会迁移 key。
- 页面和编排 API 需要先登录；登录密码默认每次启动重新生成。
- 编排看板只读；不会重试、删除、编辑或强制完成历史 flow。
- 编排看板会隐藏 OAuth token、密码、API key 等敏感字段。
- OAuth 授权本身由用户手动点击返回的 `oauth_url` 完成。
- OAuth 最终完成步骤依赖用户把 localhost callback URL 粘贴回来。
- 除 OAuth 授权本身外，其余步骤通过 Sub2API 管理接口完成。
- flow 状态持久化到 PostgreSQL，而不是仅存在内存里。

## Sub2API 对接说明

Sub2API 管理接口集中封装在：

- `app/clients/sub2api.py`

如果你的真实 Sub2API 返回结构不同，优先调整：

- 各方法的路径常量
- 请求 payload 字段
- `_extract_id()` / `_extract_value()` 的解析逻辑

这样不需要改 controller 和 service。

## 测试覆盖说明

当前测试包括：

- PostgreSQL store 建表、保存、跨实例读取
- PostgreSQL store 更新后重新读取
- 未登录访问 `/` 时跳转到 `/login`
- React 登录入口、`/ui/config`、登录成功、错误密码失败
- 编排看板列表/详情接口、过滤、分页、缺失 flow、事件时间线和敏感字段脱敏
- 受保护接口在未登录时拒绝访问
- `/provision/start` 在 cookie 和 access key header 模式下都可成功创建并写入 PostgreSQL
- `/provision/oauth/complete` 在清空缓存并重新登录后仍可完成，验证 PostgreSQL 持久化和 paste-back 流程生效
- 非法 email 返回 422
- 粘贴了不完整 callback URL 时返回 400
