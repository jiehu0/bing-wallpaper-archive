# 日序 · 必应壁纸档案

独立部署在 BERO 的壁纸归档站。公开画廊、月份筛选、关键词搜索、大图预览、原图下载、每日/随机图片接口，以及管理员 WebDAV 设置与测试。

线上地址：<https://wallpaper.boyweb.net/>；管理地址：<https://wallpaper.boyweb.net/admin/>。

## 采集与存储

- 直接读取 Bing 官方 `HPImageArchive.aspx`，当前采集 `zh-CN` 市场；无需依赖 Synteq 在线。
- 启动时和之后每小时检查一次；每次查询近期 8 天，按日期去重，遗漏日期在上游仍提供时自动补采。
- 只收录实际取得的图片；不会声称拥有参考站从 2010 年开始的全部历史数据。
- 每张保存 UHD 原图、640px WebP 缩略图、1920px WebP 预览图及版权信息。
- 本地壁纸容量上限由后台配置，默认 **40 GB**（1 GB = 10 亿字节）。统计原图、缩略图和预览图的实际文件大小，不含服务器其他应用、备份和数据库。超限时按图片日期从旧到新归档原图，达到上限以内即停止；不再按天数删除。
- WebDAV 未启用或上传失败时保留本地原图，占用可能暂时超过上限。缩略图、预览图与 SQLite 索引长期保留；如果已没有可归档原图但仍超限，任务会提示提高上限。旧版保留天数配置自动迁移为 40 GB，WebDAV 凭据保持不变，已有远端归档不会自动回迁。
- 新归档按图片日期存放到 WebDAV 的 `年份/月份/` 目录，例如 `bing-wallpaper/2026/01/2026-01-01-<校验值>.jpg`；元数据 JSON 与原图放在同一目录。
- 旧原图 PUT 到 WebDAV 后，通过 GET 回读并比对 SHA-256，同时上传元数据 JSON；成功后才删除本地原图。
- 历史原图通过服务端读取 WebDAV 返回，凭据不会发给浏览器。并发远端下载最多 4 个，单文件最大 32 MiB。
- 更换已有归档的 WebDAV 地址或目录会被阻止，避免历史图片失联；迁移存储需要先迁移文件。

## WebDAV 后台

填写 HTTPS WebDAV 根地址、专用目录、用户名、密码，点击 **测试连接**。

测试依次检查 PROPFIND、MKCOL、PUT、GET/内容比对、DELETE。仅操作随机命名的 `.connection-test-*.txt` 文件；若清理失败，会显示具体临时文件名。测试使用表单当前值，不会自动保存配置。测试通过后勾选“启用 WebDAV 自动归档”并保存。

密码留空保留旧密码。配置由 Fernet 加密落盘，密钥在 `data/.encryption-key`；数据目录和密钥必须一起备份。磁盘访问权限仍然是主要的主机安全边界。

WebDAV 下载支持跳转到签名对象存储地址；跨域跳转不会携带 WebDAV 的 Authorization 凭据。
如果上传成功后暂时返回 404 等可重试错误，会等待并重试回读；不会重复上传，回读校验始终成功后才删除本地原图。

## 补充历史壁纸

历史索引来自 [npanuhin/Bing-Wallpaper-Archive](https://github.com/npanuhin/Bing-Wallpaper-Archive)。导入前先备份数据目录，检查计划，再追加缺少的日期：

```sh
docker compose exec wallpaper python -m app.backfill --start 2026-07-12 --end 2026-10-03 --source-date-offset=-1
# 确认计划后执行；已存在的日期会跳过，可以中断后重试。
docker compose exec wallpaper python -m app.backfill --start 2026-07-12 --end 2026-10-03 --source-date-offset=-1 --apply
```

当前站点沿用 Bing `startdate`，与该中文归档源的展示日期相差一天；工具通过已有图片 ID 交叉核对偏移，不匹配时拒绝导入。只处理最近 8 天采集窗口之前的数据，以避免与日常采集同时写入同一天。

导入后在后台执行“立即采集与归档”，本地壁纸超过容量上限时，最早的原图会按现有 WebDAV 配置迁移。历史批量导入期间也会检查容量并在超限时归档。导入进度保存在 `data/backfill-latest.json`。工具保留图片版权信息，限制请求速度，不覆盖已有图片或改动站点界面。

更早的中文原图可使用 [Zhu-junwei/bing-wallpaper-archive 发行包](https://github.com/Zhu-junwei/bing-wallpaper-archive/releases)；从发行 ZIP 读取真实原图，并用文件名中的 Bing 图片 ID 与索引核对，不依赖可能失效的旧 Bing 链接：

```sh
# 先将相应年度 ZIP 放到容器内的 /tmp/2023.zip；先不带 --apply 检查计划。
docker compose exec wallpaper python -m app.backfill --release-zip /tmp/2023.zip --start 2023-10-12 --end 2023-12-30 --source-date-offset=-1 --apply
```

## API

| 路径 | 用途 |
| --- | --- |
| `/api/wallpapers?q=&month=2026-10&page=1` | 分页检索 |
| `/img/` | 最新预览图，临时重定向 |
| `/img/4k` | 最新原图 |
| `/img/rand` | 随机已归档壁纸 |
| `/img/?date=20261011&type=json` | 指定已收录日期的元数据 |
| `/media/2026-10-11/original?download=true` | 原图下载 |
| `/healthz` | 数据库健康检查 |

`/img/` 指最新已收录日期，可能因 Bing 地区更新时间或采集失败落后于当前日历日期。随机接口只从已收录档案中选择。

## 部署

运行目录 `/opt/wallpaper-archive`；Docker Compose 服务 `wallpaper`，容器 `boyweb-wallpaper`。只绑定 `127.0.0.1:8093`；1Panel 网站管理中的 `wallpaper.boyweb.net` 负责反代、HTTPS 和证书续期。

```sh
cp .env.example .env
# 设置盐与管理密码哈希，生成方法见 app/main.py 的 password_hash。
mkdir -p data
sudo chown 10001:10001 data
docker compose up -d --build
```

运行单个 Uvicorn worker，采集锁与管理会话保存在当前进程；重启会注销管理会话。管理员密码采用 PBKDF2-SHA256 验证，登录受频率限制，Cookie 为 HttpOnly/SameSite=Strict/Secure。生产访问必须使用 HTTPS。

升级前备份 `data/` 与 `.env`。一致性备份可先 `docker compose stop`，备份后 `docker compose up -d`；WebDAV 上的原图及 JSON 也应纳入备份策略。

## 验证

```sh
python -m venv .venv
.venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest -q
```

测试覆盖归档成功、校验失败、上传失败、容量边界及最早优先、重复执行、中断恢复、测试文件清理、配置加密、目标变更保护、管理鉴权、跨域写入拒绝、检索与图片下载。

前端无需构建。可选的 DOM 交互测试使用 Node.js 24：`npm install && npm test`，覆盖搜索、预览、下载、WebDAV 测试按钮、保存和密码清空。

## 来源

界面功能参考 <https://bing.ee123.net/>。Synteq 的现有 API 源自 <https://github.com/TimothyYe/bing-wallpaper>；本项目独立实现归档与 WebDAV 存储，不替换该容器或 `bing.boyweb.net`。Bing 图片版权归原摄影师及权利人所有，页面保留各图片版权说明。
