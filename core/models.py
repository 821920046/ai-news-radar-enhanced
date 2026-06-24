"""Data model and global constants for the news pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
SH_TZ = ZoneInfo("Asia/Shanghai")
WAYTOAGI_DEFAULT = (
    "https://waytoagi.feishu.cn/wiki/QPe5w5g7UisbEkkow8XcDmOpn8e?fromScene=spaceOverview"
)
WAYTOAGI_HISTORY_FALLBACK = "https://waytoagi.feishu.cn/wiki/FjiOwWp2giA7hRk6jjfcPioCnAc"

RSS_FEED_REPLACEMENTS: dict[str, str] = {
    "https://rsshub.app/infoq/recommend": "https://www.infoq.cn/feed",
    "https://rsshub.app/huggingface/blog-zh": "https://huggingface.co/blog/feed.xml",
    "https://rsshub.app/readhub/daily": "https://readhub.cn/rss",
    "https://rsshub.app/36kr/hot-list": "https://36kr.com/feed",
    "https://rsshub.app/sspai/index": "https://sspai.com/feed",
    "https://rsshub.app/sspai/matrix": "https://sspai.com/feed",
    "https://rsshub.app/meituan/tech": "https://tech.meituan.com/feed",
    "https://mjg59.dreamwidth.org/data/rss": "http://mjg59.dreamwidth.org/data/rss",
}

RSS_FEED_SKIP_PREFIXES: tuple[str, ...] = (
    "https://rsshub.app/telegram/channel/",
    "https://rsshub.app/jike/",
    "https://rsshub.app/bilibili/",
    "https://rsshub.app/zhihu/",
    "https://rsshub.app/xiaoyuzhou/podcast/",
    "https://rsshub.app/xyzrank",
    "https://rsshub.app/mittrchina/hot",
    "https://wechat2rss.bestblogs.dev/",
    "https://werss.bestblogs.dev/",
    "http://47.122.94.119:18080/",
)

RSS_FEED_SKIP_EXACT: set[str] = {
    "https://rachelbythebay.com/w/atom.xml",
    "https://flak.tedunangst.com/rss",
}

_DEFAULT_OFFICIAL_AI_FEEDS: tuple[dict[str, str], ...] = (
    {
        "title": "OpenAI News",
        "xml_url": "https://openai.com/news/rss.xml",
        "html_url": "https://openai.com/news",
    },
    {
        "title": "Google DeepMind",
        "xml_url": "https://deepmind.google/blog/rss.xml",
        "html_url": "https://deepmind.google/blog",
    },
    {
        "title": "Google AI Blog",
        "xml_url": "https://blog.google/innovation-and-ai/technology/ai/rss/",
        "html_url": "https://blog.google/innovation-and-ai/technology/ai/",
    },
    {
        "title": "Hugging Face Blog",
        "xml_url": "https://huggingface.co/blog/feed.xml",
        "html_url": "https://huggingface.co/blog",
    },
    {
        "title": "GitHub AI & ML",
        "xml_url": "https://github.blog/ai-and-ml/feed/",
        "html_url": "https://github.blog/ai-and-ml/",
    },
    {
        "title": "GitHub Changelog",
        "xml_url": "https://github.blog/changelog/feed/",
        "html_url": "https://github.blog/changelog/",
    },
    {
        "title": "OpenAI Skills",
        "xml_url": "https://github.com/openai/skills/commits/main.atom",
        "html_url": "https://github.com/openai/skills",
        "include_keywords": "hatch,pet,migrate-to-codex",
    },
    {
        "title": "Hugging Face Papers",
        "xml_url": "https://huggingface.co/papers/feed.xml",
        "html_url": "https://huggingface.co/papers",
        "hf_token_required": True,
    },
    {
        "title": "Microsoft AI Blog",
        "xml_url": "https://blogs.microsoft.com/blog/tag/microsoft-ai/feed/",
        "html_url": "https://blogs.microsoft.com/blog/tag/microsoft-ai/",
    },
    {
        "title": "Meta AI Blog",
        "xml_url": "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_meta_ai.xml",
        "html_url": "https://ai.meta.com/blog/",
        "olshansk_note": "官方 RSS 400 失效，改用 Olshansk 抓取",
    },
    {
        "title": "NVIDIA AI Blog",
        "xml_url": "https://blogs.nvidia.com/feed/",
        "html_url": "https://blogs.nvidia.com/",
    },
    {
        "title": "MIT Technology Review",
        "xml_url": "https://www.technologyreview.com/feed/",
        "html_url": "https://www.technologyreview.com/topic/artificial-intelligence/",
        "include_keywords": "ai,artificial intelligence,llm,gpt,openai,anthropic,deepseek,ml,machine learning,robot,agent",
    },
    {
        "title": "The Verge AI",
        "xml_url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
        "html_url": "https://www.theverge.com/ai-artificial-intelligence",
    },
    {
        "title": "TechCrunch AI",
        "xml_url": "https://techcrunch.com/category/artificial-intelligence/feed/",
        "html_url": "https://techcrunch.com/category/artificial-intelligence/",
    },
    {
        "title": "VentureBeat AI",
        "xml_url": "https://venturebeat.com/category/ai/feed/",
        "html_url": "https://venturebeat.com/category/ai/",
    },
    {
        "title": "Ars Technica",
        "xml_url": "https://feeds.arstechnica.com/arstechnica/technology-lab",
        "html_url": "https://arstechnica.com",
        "include_keywords": "ai,artificial intelligence,llm,gpt,openai,anthropic,deepseek,ml,machine learning,robot,agent,neural,transformer",
    },
    {
        "title": "机器之心",
        "xml_url": "https://www.jiqizhixin.com/rss",
        "html_url": "https://www.jiqizhixin.com/",
    },
    {
        "title": "NVIDIA Technical Blog",
        "xml_url": "https://developer.nvidia.com/blog/feed/",
        "html_url": "https://developer.nvidia.com/blog",
        "include_keywords": "ai,ml,llm,inference,training,cuda,tensorrt,neural,transformer,generative",
    },
    {
        "title": "PyTorch Blog",
        "xml_url": "https://pytorch.org/blog/feed.xml",
        "html_url": "https://pytorch.org/blog",
    },
    {
        "title": "TensorFlow Blog",
        "xml_url": "https://blog.tensorflow.org/feeds/posts/default?alt=rss",
        "html_url": "https://blog.tensorflow.org",
    },
    {
        "title": "Ollama Blog",
        "xml_url": "https://ollama.com/blog/rss.xml",
        "html_url": "https://ollama.com/blog",
    },
    {
        "title": "Papers With Code",
        "xml_url": "https://paperswithcode.com/latest.rss",
        "html_url": "https://paperswithcode.com",
        "include_keywords": "ai,artificial intelligence,llm,gpt,ml,machine learning,neural,transformer,deep learning,nlp,computer vision,agent",
    },
    {
        "title": "Lilianweng's Blog",
        "xml_url": "https://lilianweng.github.io/index.xml",
        "html_url": "https://lilianweng.github.io",
    },
    {
        "title": "Together AI Blog",
        "xml_url": "https://www.together.ai/blog/rss.xml",
        "html_url": "https://www.together.ai/blog",
    },
    {
        "title": "fast.ai",
        "xml_url": "https://www.fast.ai/index.xml",
        "html_url": "https://www.fast.ai",
    },
    # ── OPML 示例中已有但未内置的高信噪比源 ──
    {
        "title": "Simon Willison's Weblog",
        "xml_url": "https://simonwillison.net/atom/entries/",
        "html_url": "https://simonwillison.net",
    },
    {
        "title": "Latent Space",
        "xml_url": "https://www.latent.space/feed",
        "html_url": "https://www.latent.space",
    },
    {
        "title": "Vercel Blog",
        "xml_url": "https://vercel.com/blog/feed",
        "html_url": "https://vercel.com/blog",
        "include_keywords": "ai,ml,llm,agent,inference,model,ai-sdk,generative,copilot,assistant",
    },
    # ── 模型提供商 / AI 基础设施厂商 ──
    {
        "title": "Anthropic Blog",
        "xml_url": "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_news.xml",
        "html_url": "https://www.anthropic.com/news",
        "olshansk_note": "官方 RSS (news.rss) 已失效，改用 Olshansk 抓取",
    },
    # ── Anthropic 细分（Olshansk 抓取，官方 RSS 未覆盖）──
    {
        "title": "Anthropic Engineering",
        "xml_url": "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_engineering.xml",
        "html_url": "https://www.anthropic.com/engineering",
    },
    {
        "title": "Anthropic Research",
        "xml_url": "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_research.xml",
        "html_url": "https://www.anthropic.com/research",
    },
    {
        "title": "Anthropic Frontier Red Team",
        "xml_url": "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_red.xml",
        "html_url": "https://www.anthropic.com/news",
    },
    {
        "title": "Mistral AI Blog",
        "xml_url": "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_mistral.xml",
        "html_url": "https://mistral.ai/news",
        "olshansk_note": "官方 RSS (mistral.ai/feed.xml) 404，改用 Olshansk 抓取",
    },
    {
        "title": "Cohere Blog",
        "xml_url": "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_cohere.xml",
        "html_url": "https://cohere.com/blog",
        "olshansk_note": "官方 RSS 404，改用 Olshansk 抓取",
    },
    {
        "title": "xAI Blog",
        "xml_url": "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_xainews.xml",
        "html_url": "https://x.ai/blog",
        "olshansk_note": "官方 RSS (x.ai/news/rss.xml) 404，改用 Olshansk 抓取",
    },
    {
        "title": "Perplexity Blog",
        "xml_url": "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_perplexity_hub.xml",
        "html_url": "https://www.perplexity.ai/hub",
        "olshansk_note": "官方 RSS 404，改用 Olshansk 抓取",
    },
    {
        "title": "Groq Blog",
        "xml_url": "https://wow.groq.com/feed/",
        "html_url": "https://wow.groq.com",
    },
    # ── AI 开发框架 / 工具链生态 ──
    {
        "title": "LangChain Blog",
        "xml_url": "https://blog.langchain.dev/rss/",
        "html_url": "https://blog.langchain.dev",
    },
    {
        "title": "Weights & Biases Blog",
        "xml_url": "https://wandb.ai/fully-connected/feed",
        "html_url": "https://wandb.ai/fully-connected",
    },
    {
        "title": "Pinecone Blog",
        "xml_url": "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_pinecone.xml",
        "html_url": "https://www.pinecone.io/blog",
        "olshansk_note": "官方 RSS 404，改用 Olshansk 抓取",
    },
    {
        "title": "Weaviate Blog",
        "xml_url": "https://weaviate.io/blog/rss.xml",
        "html_url": "https://weaviate.io/blog",
    },
    # ── AI 编程工具生态（Olshansk 抓取，无官方 RSS）──
    {
        "title": "Cursor Blog",
        "xml_url": "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_cursor.xml",
        "html_url": "https://cursor.com/blog",
    },
    {
        "title": "Windsurf Blog",
        "xml_url": "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_windsurf_blog.xml",
        "html_url": "https://windsurf.com/blog",
    },
    {
        "title": "Windsurf Changelog",
        "xml_url": "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_windsurf_changelog.xml",
        "html_url": "https://windsurf.com/changelog",
    },
    # ── Newsletter / 个人博客 ──
    {
        "title": "The Batch (Andrew Ng)",
        "xml_url": "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_the_batch.xml",
        "html_url": "https://www.deeplearning.ai/the-batch/",
        "olshansk_note": "官方 RSS 403，改用 Olshansk 抓取",
    },
    {
        "title": "AI Snake Oil",
        "xml_url": "https://www.aisnakeoil.com/feed",
        "html_url": "https://www.aisnakeoil.com",
    },
    {
        "title": "Interconnects (Nathan Lambert)",
        "xml_url": "https://www.interconnects.ai/feed",
        "html_url": "https://www.interconnects.ai",
    },
    # ── 中文 AI 媒体 ──
    {
        "title": "量子位",
        "xml_url": "https://www.qbitai.com/feed",
        "html_url": "https://www.qbitai.com",
        "olshansk_note": "量子位 RSS 间歇性 403，第三方反爬触发限制",
    },
    {
        "title": "爱范儿 ifanr",
        "xml_url": "https://www.ifanr.com/feed",
        "html_url": "https://www.ifanr.com",
        "include_keywords": "ai,人工智能,大模型,llm,gpt,agent,智能,机器人,芯片,gpu,nvidia,deepseek,openai,算力",
    },
    # ── 学术 / 研究 ──
    {
        "title": "arXiv cs.AI",
        "xml_url": "https://rss.arxiv.org/rss/cs.AI",
        "html_url": "https://arxiv.org/list/cs.AI/recent",
        "include_keywords": "ai,artificial intelligence,llm,language model,agent,reinforcement learning,neural,transformer,reasoning,multimodal",
    },
    # ── AI 安全 / 对齐 ──
    {
        "title": "Alignment Forum",
        "xml_url": "https://www.alignmentforum.org/feed.xml",
        "html_url": "https://www.alignmentforum.org",
    },
    # ── 热门项目 / Trending ──
    {
        "title": "GitHub Trending",
        "xml_url": "https://rsshub.rssforever.com/github/trending/daily/any",
        "html_url": "https://github.com/trending",
    },
    # HuggingFace Trending Models / DockerHub Trending 暂无可靠的 RSS 源，
    # 可在 feeds/follow.opml 中按需添加自托管 RSSHub 路由。
)

def _load_official_feeds() -> tuple[dict[str, Any], ...]:
    from pathlib import Path
    config_path = Path(__file__).resolve().parent.parent / "config" / "sources.yaml"
    if not config_path.exists():
        return _DEFAULT_OFFICIAL_AI_FEEDS
    try:
        import yaml
        with open(config_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
            yaml_feeds = data.get("official_ai_feeds")
            if not yaml_feeds or not isinstance(yaml_feeds, list):
                return _DEFAULT_OFFICIAL_AI_FEEDS
            
            merged: dict[str, dict[str, Any]] = {}
            for f in _DEFAULT_OFFICIAL_AI_FEEDS:
                url = str(f.get("xml_url") or "").strip()
                if url:
                    merged[url] = dict(f)
            
            for f in yaml_feeds:
                if not isinstance(f, dict):
                    continue
                url = str(f.get("xml_url") or "").strip()
                if not url:
                    continue
                # 用 YAML 中的源覆盖或新增，其余保留默认
                merged[url] = {
                    "title": str(f.get("title", "")),
                    "xml_url": url,
                    "html_url": str(f.get("html_url", "")),
                    "include_keywords": f.get("include_keywords") or merged.get(url, {}).get("include_keywords"),
                    "hf_token_required": f.get("hf_token_required") or merged.get(url, {}).get("hf_token_required"),
                }
            return tuple(merged.values())
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Failed to load official feeds from sources.yaml: %s", e)
        return _DEFAULT_OFFICIAL_AI_FEEDS

OFFICIAL_AI_FEEDS = _load_official_feeds()
OFFICIAL_AI_MAX_AGE_DAYS = 45
AIBREAKFAST_JINA_URL = "https://r.jina.ai/https://aibreakfast.beehiiv.com/"
FOLLOW_BUILDERS_FEED_BASE = "https://raw.githubusercontent.com/zarazhangrui/follow-builders/main"

# ── 开源热榜 ──
GITHUB_TRENDING_URL = "https://github.com/trending?since=daily"
VERCEL_TEMPLATES_URL = "https://vercel.com/templates"
VERCEL_AI_SDK_TOPIC_URL = "https://github.com/topics/vercel-ai-sdk"
OSS_MAX_COMBINED = 30  # 综合排名取 TOP N


@dataclass
class RawItem:
    site_id: str
    site_name: str
    source: str
    title: str
    url: str
    published_at: datetime | None
    meta: dict[str, Any]
    description: str = ""
