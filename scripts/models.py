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

OFFICIAL_AI_FEEDS: tuple[dict[str, str], ...] = (
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
    },
    {
        "title": "Microsoft AI Blog",
        "xml_url": "https://blogs.microsoft.com/ai/feed/",
        "html_url": "https://blogs.microsoft.com/ai/",
    },
    {
        "title": "Meta AI Blog",
        "xml_url": "https://ai.meta.com/blog/rss/",
        "html_url": "https://ai.meta.com/blog/",
    },
    {
        "title": "NVIDIA AI Blog",
        "xml_url": "https://blogs.nvidia.com/ai/feed/",
        "html_url": "https://blogs.nvidia.com/ai/",
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
)
OFFICIAL_AI_MAX_AGE_DAYS = 45
AIBREAKFAST_JINA_URL = "https://r.jina.ai/https://aibreakfast.beehiiv.com/"
FOLLOW_BUILDERS_FEED_BASE = "https://raw.githubusercontent.com/zarazhangrui/follow-builders/main"


@dataclass
class RawItem:
    site_id: str
    site_name: str
    source: str
    title: str
    url: str
    published_at: datetime | None
    meta: dict[str, Any]
