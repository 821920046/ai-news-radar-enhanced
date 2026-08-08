# 在 Cloudflare 上为 GitHub Pages 注入安全响应头（免费）

> 你的部署是 **GitHub Pages + Cloudflare 代理**。GitHub Pages **不支持**自定义响应头，所以仓库里的 `_headers` 文件在这种部署下**不会生效**（它只在 Cloudflare Pages 生效）。
> 请改用下面的 **Cloudflare Transform Rules**（免费版每个规则集可用 10 条规则，完全够用）。

## 前提
- 域名已接入 Cloudflare，DNS 记录为**橙色云（已代理）**状态。

## 步骤：添加响应头
Cloudflare 控制台 → 选中域名 → **Rules → Transform Rules → Modify Response Header → Create rule**。

规则名：`security-headers`；匹配条件选 **All incoming requests**（或 `Hostname equals news.my-tv.eu.cc`）。
在 **Then... Set static** 逐条添加（Header name / Value）：

| Header name | Value |
|---|---|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `geolocation=(), microphone=(), camera=()` |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` |
| `Content-Security-Policy` | `default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self' https:; font-src 'self' data:; base-uri 'self'; form-action 'self'; frame-ancestors 'none'` |

保存并部署。

## 可选：缓存规则（提速 + 省回源）
Cloudflare → **Caching → Cache Rules → Create rule**：
- 规则 A：`URI Path starts with /assets/` → Edge TTL = 1 year，Browser TTL = 1 year（资源指纹名，可长缓存）。
- 规则 B：`URI Path ends with .json` 或 `/index.html` → Edge TTL = 5 minutes（保“实时”）。

## 验证
部署后执行：
```bash
curl -sI https://news.my-tv.eu.cc/ | grep -iE 'content-security-policy|x-frame-options|x-content-type|referrer-policy|strict-transport|permissions-policy'
```
或用 https://securityheaders.com/ 扫描，目标 A/A+。

## 说明
- 若以后迁到 **Cloudflare Pages**，仓库里的 `_headers` 会自动生效，可不再需要上述手动规则。
- CSP 里保留了 `'unsafe-inline'`，因为页面内联了少量 `<style>`/`<script>`；如今后改为外部文件 + nonce，可进一步去掉。
