# GitHub Pages 卡在 deployment_in_progress —— 第一性原理排查

## 0. 现象拆解
`actions/deploy-pages` 自身只做三件事：
1. 找到本次 run 里名为 `github-pages` 的 artifact（日志显示 Found 1 artifact(s)，✅ 正常）
2. 调 API 创建 deployment（日志显示 Created deployment，✅ 正常）
3. 每 5s 轮询状态，直到 `succeed` / 报错 / 超时（10 分钟）

所以“一直 deployment_in_progress”= **Action 本身没问题，是 GitHub Pages 后端那一侧的部署没有完成**。
排查范围因此被压缩到 4 个变量：deployment 排队、artifact 内容、Pages 源设置、自定义域名/证书。

## 1. 部署排队（最高概率）
- Pages 同一时刻只允许一个 deployment。
- 本仓库 `update-news.yml` 每小时 push 一次（572 commits），每次 push 触发一次 Pages 部署。
- 如果 Pages workflow 没有 `concurrency: group: pages` 或者用了 `cancel-in-progress: true`，
  取消掉的 job 留下一个永远不会 finalize 的 deployment，后面的全部排在它后面 → 一直 in_progress。
- 另外：如果同时存在 **两个** 部署来源（自建 Actions workflow + Settings 里选了 Deploy from a branch
  产生的 pages-build-deployment），二者会互相抢锁。

**动作**：Settings → Pages → Source 必须是 **GitHub Actions**（唯一来源）；
Settings → Environments → github-pages 里删掉 pending/in_progress 的旧部署；
Actions 里取消所有排队中的 pages 相关 run，然后只手动跑一次。

## 2. artifact 内容过重 / 结构不对
- 仓库 `data/` 27MB，其中 `archive.json` 16MB、`trend_history.json` 2.4MB、`latest-24h-all.json` 6.5MB。
- 这些是**后端历史数据**，前端首屏根本不需要，却被整仓库打包进 Pages artifact，
  每小时全量重传 + 后端解包，显著拉长 finalize 时间。
- 缺 `.nojekyll`：Pages 默认走 Jekyll 处理，会忽略 `_headers` 这类下划线开头文件，
  且对大量 JSON 做无意义扫描。

**动作**：只打包 `index.html / assets / manifest.json / sw.js / robots.txt / sitemap.xml`
+ 前端真正 fetch 的几个 JSON，并加 `.nojekyll`。见 `deploy-pages.yml`。

## 3. 自定义域名 / HTTPS 证书
- README 写着当前域名 `news.my-tv.eu.cc`，但仓库里**没有 CNAME 文件**。
- 若 Settings→Pages 里配了自定义域名，而 artifact 里没有 CNAME，每次部署会覆盖掉域名配置；
  反过来，DNS 未生效 / 证书还在签发时，deployment 会长时间挂在 in_progress。

**动作**：要么彻底不用自定义域名（用 `821920046.github.io/ai-news-radar-enhanced/`），
要么在 `_site` 里 `echo news.my-tv.eu.cc > _site/CNAME`，并确认 DNS CNAME 指向 `821920046.github.io`，
且先关掉 Enforce HTTPS，等证书签好再打开。

## 4. 权限 / environment
必须同时具备：
```yaml
permissions: { contents: read, pages: write, id-token: write }
environment: { name: github-pages }
```
若 `github-pages` environment 配了 required reviewers / branch 保护，deployment 会一直挂起等审批。

**动作**：Settings → Environments → github-pages，检查没有 protection rules。

## 5. 顺带发现的两个隐患（不影响部署但影响可用性）
- `update-news.yml` 里 `${{ }}` 被写坏成 `$ github.ref ` / `$ secrets.WEBHOOK_URL `，
  concurrency group 和 webhook 告警实际是失效的（甚至可能 YAML 解析异常）。
- `sw.js` 用的是 `./` 相对路径，配合 project pages 的 `/ai-news-radar-enhanced/` 子路径没问题；
  但更新频繁时建议在 `sw.js` 里对 `data/*.json` 强制 network-first（当前已是），
  并在每次发布时 bump `CACHE` 版本号，否则用户看到旧快照。

## 执行顺序（照做即可）
1. Settings → Pages → Source = **GitHub Actions**（不要选 branch）。
2. Settings → Environments → github-pages：删除 protection rules，清掉卡住的部署。
3. Actions：取消所有 queued / in_progress 的 pages 部署 run。
4. 提交 `.github/workflows/deploy-pages.yml`（本目录提供），删掉旧的 pages 部署 workflow。
5. 修复 `update-news.yml` 里坏掉的 `${{ }}` 表达式。
6. 手动 Run workflow 一次，观察 build job 输出的 `du -sh _site`（应该只有几 MB）。
7. 若 3 次仍卡住 → 100% 是自定义域名/证书问题，按第 3 节处理。
