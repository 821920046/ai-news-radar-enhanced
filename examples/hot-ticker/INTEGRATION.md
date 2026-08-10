# 顶部热榜滚动区 · 集成说明

按需求图：把顶部「AI信号 / 覆盖站点 / 来源分组 / 归档总量」四张统计卡，
改为「24 小时最热新闻 + 最热开源」两条实时横向滚动榜，点击查看详情。

## 文件

| 文件 | 说明 |
|---|---|
| `examples/hot-ticker/hot-ticker.js` | 自包含、零依赖组件（含样式与弹窗） |
| `examples/hot-ticker/demo.html` | 独立演示页（内置示例数据，双击即可预览） |
| `api/app.py` | 新增 `/hot` 接口，返回 `news` / `opensource` 两个榜单 |

## 三步集成

### 1) 引入脚本
把 `hot-ticker.js` 拷贝到前端目录，在 `index.html` 底部引入：

```html
<script src="hot-ticker.js"></script>
```

### 2) 替换统计卡区域
找到原来放「AI信号 / 覆盖站点 / 来源分组 / 归档总量」四张卡的容器（例如 `<div class="stats-row">…</div>`），整体替换为：

```html
<hot-ticker
  data-endpoint="/hot"
  data-news-json="data/latest-24h.json"
  data-all-json="data/latest-24h-all.json"
  data-top="20"
  data-refresh="300000"></hot-ticker>
```

属性说明：

| 属性 | 作用 | 默认 |
|---|---|---|
| `data-endpoint` | 优先读取的热榜 API | 空（不调 API） |
| `data-news-json` | 回退数据源（AI 强相关） | `data/latest-24h.json` |
| `data-all-json` | 回退数据源（全量，含开源热榜） | `data/latest-24h-all.json` |
| `data-top` | 每个榜单最多条数 | `20` |
| `data-refresh` | 自动刷新间隔（毫秒，0=不刷新） | `0` |

> 不想用自定义元素？也可手动挂载：
> ```js
> mountHotTicker(document.getElementById("hot"), { endpoint: "/hot", top: 20 })
> ```

### 3) （可选）部署 /hot 接口
若你部署了 FastAPI，`/hot` 会自动可用：

```bash
curl "http://localhost:8000/hot?top=20"
# -> { generated_at, news:[...], opensource:[...], news_total, opensource_total }
```

纯静态部署（GitHub Pages）无需 API：组件会自动从 `data/latest-24h-all.json` / `data/latest-24h.json`（或预渲染内联数据）派生两个榜单。

## 开源 / 新闻 如何区分
组件与后端按以下任一命中即判定为「开源」：`is_opensource===true`、`category/type` 含 open/repo/github、
来源含 github/trending、`tags` 含 开源/github/trending/repo、或含 `stars` 字段。其余计入「新闻」。

## 热度排序
`hotness` > `stars` > `signal_score`，降序取 Top N。开源卡显示 `★ 星数`，新闻卡显示 `🔥 热度`。

## 安全
所有插入文本（标题/摘要/标签/来源）均经 `esc()` 转义，防止第三方内容 XSS。
