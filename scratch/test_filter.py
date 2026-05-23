import sys
from pathlib import Path

# 添加项目根目录到 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.topic_filter import is_ai_related_record, contains_meaningful_ai_signal, contains_any_keyword, AI_KEYWORDS, BROAD_AI_TERMS, TECH_KEYWORDS, EN_SIGNAL_RE

rec = {
    "site_id": "tophub",
    "site_name": "TopHub",
    "source": "机器之心",
    "title": "新一代推理模型刷新多模态数学基准",
    "url": "https://example.com/reasoning-model",
}

print("AI_KEYWORDS:", AI_KEYWORDS)
print("BROAD_AI_TERMS:", BROAD_AI_TERMS)
print("TECH_KEYWORDS:", TECH_KEYWORDS)

title = rec.get("title")
source = rec.get("source")
site_name = rec.get("site_name")
url = rec.get("url")
text = f"{title} {source} {site_name} {url}".lower()
print("text:", text)

has_ai = contains_meaningful_ai_signal(text)
has_broad_ai = contains_any_keyword(text, list(BROAD_AI_TERMS)) or EN_SIGNAL_RE.search(text) is not None
has_tech = contains_any_keyword(text, TECH_KEYWORDS)

print("has_ai:", has_ai)
print("has_broad_ai:", has_broad_ai)
print("has_tech:", has_tech)

ans = is_ai_related_record(rec)
print("is_ai_related_record:", ans)
