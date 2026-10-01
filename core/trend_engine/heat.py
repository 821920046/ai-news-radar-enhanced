"""事件级热度算法（heat v1）。

设计目标
--------
回答一个问题：**「这件事现在有多少个独立来源在说？」**

与旧的 `compute_hotness()` 的区别，后者是「这条内容在它所属的源里排第几」，
是**单条内容**的排位分；本模块算的是**事件**（一组讲同一件事的内容）的
跨来源关注度，两者不可互相替代。

三条规则（借鉴 AIHOT 的 heat-v1-48h-halflife24h）
------------------------------------------------
1. **每个独立来源只算一次** —— 同一家媒体发十篇只计 1，重复抓取不加热度。
   参与键优先取 `owner`（同一实体的多个站点归一个），退回 `site_id`。
2. **24 小时半衰期** —— 一条 24 小时前的证据权重 0.5，48 小时前 0.25。
3. **48 小时窗口** —— 窗口外的证据不参与计算。

关键设计：「源没跟上」不等于「热度下跌」
--------------------------------------
如果某个信源恰好抓取失败，它贡献的证据会凭空消失，事件热度随之下降。
旧逻辑会把这个下降报成「趋势下降」，但真实情况是**我们没抓到数据**，
而不是**没人在讨论**。

因此本模块把「证据的可观测性」当作一等公民：

* `HeatResult.comparable` == False 表示这次比较**不可信**；
* 此时 `trend` 取 ``"unknown"`` 而不是 ``"down"``；
* `behind_sources` 列出哪些源拖后腿了，便于排查。

这与本项目在归档裁剪上确立的原则一致：**不可证明新鲜，就不当成新鲜**。
不可证明下跌，就不能报成下跌。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from core.utils import event_time, parse_iso

logger = logging.getLogger(__name__)

# ── 算法常量（改这些值等于换算法版本，请同步更新 HEAT_RULE_VERSION）──
HEAT_RULE_VERSION = "heat-v1-48h-halflife24h"
WINDOW_HOURS = 48
HALF_LIFE_HOURS = 24
#: 与 6 小时前比较（趋势方向）
TREND_COMPARE_HOURS = 6
#: 至少要有这么多个独立来源才认为「这是个事件级热点」
MIN_PARTICIPANTS = 2
#: 趋势判定为 up/down 所需的最小相对变化，低于此值算 flat
TREND_FLAT_BAND = 0.10
#: 检测「新」的时间阈值
NEW_WITHIN_HOURS = 6

#: 把热度换算成给读者看的分值（放大 10 倍，保留一位小数）
DISPLAY_SCALE = 10.0


@dataclass
class Participant:
    """一个参与讨论该事件的独立来源在这一时刻的证据。"""

    key: str
    site_id: str
    site_name: str
    observed_at: datetime
    is_first_party: bool = False


@dataclass
class HeatResult:
    """一个事件的热度计算结果。"""

    heat: float
    """衰减后的热度值（原始值，未经 DISPLAY_SCALE 放大）。"""

    participants: int
    """参与该事件的独立来源数。"""

    latest_at: datetime | None
    """最新一条证据的时间。"""

    first_at: datetime | None
    """窗口内最早一条证据的时间。"""

    trend: str
    """``up`` / ``down`` / ``flat`` / ``new`` / ``unknown``。

    ``unknown`` 表示证据不完整（有源未跟上），无法判断方向 —— 绝不等同于 ``down``。
    """

    trend_pct: float | None
    """相对变化的百分比；不可比时为 None。"""

    comparable: bool
    """本次趋势比较是否可信。"""

    behind_sources: list[str] = field(default_factory=list)
    """拖后腿的信源 id（因此比较不可比）。"""

    uncomparable_participants: int = 0
    """被排除在比较之外的参与者数。"""

    badges: list[str] = field(default_factory=list)
    """``surge`` / ``new`` / ``rising``。"""

    source_names: list[str] = field(default_factory=list)
    """参与来源名（去重，按最新活跃排序）。"""

    def display_heat(self) -> float:
        """给读者看的分值（放大 10 倍，保留一位小数）。"""
        return round(self.heat * DISPLAY_SCALE, 1)

    def to_dict(self) -> dict[str, Any]:
        """转为可直接写进 JSON 快照的 dict。"""
        return {
            "heat": self.display_heat(),
            "heat_raw": round(self.heat, 3),
            "heat_rule": HEAT_RULE_VERSION,
            "heat_participants": self.participants,
            "heat_trend": self.trend,
            "heat_trend_pct": self.trend_pct,
            "heat_comparable": self.comparable,
            "heat_badges": list(self.badges),
            "heat_sources": list(self.source_names),
            "heat_latest_at": self.latest_at.isoformat() if self.latest_at else None,
            "heat_first_at": self.first_at.isoformat() if self.first_at else None,
        }


def participant_key(item: dict[str, Any]) -> str:
    """计算参与键：同一实体的多个站点算一个参与者。

    优先级：``owner``（实体）> ``site_id``（站点）> ``url`` 的域名 > ``unknown``。
    """
    owner = str(item.get("owner") or item.get("owner_entity") or "").strip()
    if owner:
        return f"owner:{owner}"
    site_id = str(item.get("site_id") or "").strip()
    if site_id:
        return f"site:{site_id}"
    url = str(item.get("url") or "").strip()
    if url:
        try:
            from urllib.parse import urlparse

            host = urlparse(url).netloc.lower()
            if host:
                return f"host:{host}"
        except Exception:  # pragma: no cover - 解析失败退回 unknown
            pass
    return "unknown"


def _item_time(item: dict[str, Any]) -> datetime | None:
    """取证据的**事件时间**（而非抓取时间）。

    复用 `core.utils.event_time`，它已经处理了「RSS 源必须用 published_at，
    否则历史文章会被误判为 24h 内」这个坑。
    """
    try:
        return event_time(item)
    except Exception:
        pass
    # 兜底：退到原始字段，仍然是「不可解析 → None」的语义
    return (
        parse_iso(item.get("published_at"))
        or parse_iso(item.get("event_time"))
        or parse_iso(item.get("first_seen_at"))
    )


def detect_behind_sources(
    items: Sequence[dict[str, Any]],
    *,
    all_site_ids: Iterable[str] | None = None,
    min_items: int = 1,
) -> list[str]:
    """找出「本次没有产出任何证据、但通常应该产出」的信源。

    判定方式：把候选全集（`all_site_ids`，通常是本轮所有已抓取的信源）
    与本次实际产出证据的信源做差集。

    之所以需要这个函数：如果 A 源抓取失败，它对某事件贡献的证据就消失了，
    事件热度会下降。若不把 A 标为 behind，就会把这个下降误报成
    「讨论变少了」。

    Args:
        items: 本轮实际产出的内容。
        all_site_ids: 本轮理论上应该在场的信源 id 集合。
            为 None 时退化为「用 items 自身推断」，此时永远返回空列表。
        min_items: 一个信源至少产出多少条才算「在场」。

    Returns:
        缺席的信源 id 列表；无信息时返回空列表（即比较仍然可信）。
    """
    if all_site_ids is None:
        return []

    counts: dict[str, int] = {}
    for item in items:
        sid = str(item.get("site_id") or "").strip()
        if sid:
            counts[sid] = counts.get(sid, 0) + 1

    expected = {str(s).strip() for s in all_site_ids if str(s).strip()}
    behind = sorted(s for s in expected if counts.get(s, 0) < min_items)
    return behind


class HeatEngine:
    """事件级热度计算引擎（纯 Python，零外部调用）。"""

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.window_hours = float(cfg.get("window_hours", WINDOW_HOURS))
        self.half_life_hours = float(cfg.get("half_life_hours", HALF_LIFE_HOURS))
        self.trend_compare_hours = float(cfg.get("trend_compare_hours", TREND_COMPARE_HOURS))
        self.flat_band = float(cfg.get("trend_flat_band", TREND_FLAT_BAND))
        self.new_within_hours = float(cfg.get("new_within_hours", NEW_WITHIN_HOURS))
        # 半衰期必须为正，否则衰减公式会除以 0
        if self.half_life_hours <= 0:
            self.half_life_hours = float(HALF_LIFE_HOURS)

    # ------------------------------------------------------------------
    # 内部：单次窗口内的参与者聚合
    # ------------------------------------------------------------------

    def _aggregate(
        self,
        items: Sequence[dict[str, Any]],
        at: datetime,
        *,
        behind: set[str],
    ) -> dict[str, dict[str, Any]]:
        """把 items 聚合为 `{participant_key: {...}}`。

        每个参与者只保留窗口内**最新**的一条证据时间（重复抓取不加热度）。
        """
        window_start = at - timedelta(hours=self.window_hours)
        out: dict[str, dict[str, Any]] = {}

        for item in items:
            ts = _item_time(item)
            if ts is None:
                # 不可证明时间 → 不计入热度（与归档裁剪同策略）
                continue
            if ts > at or ts <= window_start:
                continue

            key = participant_key(item)
            if key == "unknown":
                continue

            sid = str(item.get("site_id") or "").strip()
            rec = out.get(key)
            if rec is None:
                out[key] = {
                    "last_at": ts,
                    "first_at": ts,
                    "site_id": sid,
                    "site_name": str(item.get("site_name") or sid),
                    "first_party": bool(item.get("first_party")),
                    "behind": sid in behind,
                }
            else:
                if ts > rec["last_at"]:
                    rec["last_at"] = ts
                if ts < rec["first_at"]:
                    rec["first_at"] = ts
                # 任一时刻来自 behind 源，该参与者就不可比
                rec["behind"] = rec["behind"] or (sid in behind)
                rec["first_party"] = rec["first_party"] or bool(item.get("first_party"))

        return out

    def _decayed_sum(
        self,
        participants: dict[str, dict[str, Any]],
        at: datetime,
        *,
        only_comparable: bool = False,
    ) -> float:
        """对参与者的证据时间做 24h 半衰期衰减求和。"""
        total = 0.0
        for rec in participants.values():
            if only_comparable and rec.get("behind"):
                continue
            age_hours = (at - rec["last_at"]).total_seconds() / 3600.0
            if age_hours < 0:
                age_hours = 0.0
            total += math.pow(0.5, age_hours / self.half_life_hours)
        return total

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def compute(
        self,
        items: Sequence[dict[str, Any]],
        *,
        at: datetime | None = None,
        behind_sources: set[str] | None = None,
    ) -> HeatResult:
        """计算一组同事件内容的热度。

        Args:
            items: 属于同一事件的内容列表。
            at: 计算基准时刻，默认当前 UTC 时间。
            behind_sources: 本轮未产出证据的信源 id 集合。传入后，
                若这些源参与过该事件，比较会被标记为不可信。

        Returns:
            HeatResult
        """
        at = at or datetime.now(timezone.utc)
        behind = {str(b).strip() for b in (behind_sources or set()) if str(b).strip()}

        current = self._aggregate(items, at, behind=behind)
        if not current:
            return HeatResult(
                heat=0.0,
                participants=0,
                latest_at=None,
                first_at=None,
                trend="unknown",
                trend_pct=None,
                comparable=True,
            )

        heat = self._decayed_sum(current, at)
        latest_at = max(r["last_at"] for r in current.values())
        first_at = min(r["first_at"] for r in current.values())

        # ── 与 N 小时前比较 ──
        prev_at = at - timedelta(hours=self.trend_compare_hours)
        previous = self._aggregate(items, prev_at, behind=behind)

        heat_prev = self._decayed_sum(previous, prev_at)
        # 只比较「前后两个时刻都观测完整」的参与者
        heat_cur_obs = self._decayed_sum(current, at, only_comparable=True)
        heat_prev_obs = self._decayed_sum(previous, prev_at, only_comparable=True)

        uncomparable = sum(1 for r in current.values() if r.get("behind"))
        uncomparable += sum(
            1 for k, r in previous.items() if r.get("behind") and k not in current
        )

        comparable = uncomparable == 0
        if comparable:
            cur_cmp, prev_cmp = heat, heat_prev
        else:
            cur_cmp, prev_cmp = heat_cur_obs, heat_prev_obs

        trend_pct: float | None
        if not comparable:
            # 证据不完整 → 方向不可判定，绝不报成 down（也不报成 up/new）。
            # 这个分支必须排在 prev_cmp 判断**之前**：否则「有源缺席」的
            # 新事件会被标成 "new"、旧事件被标成 "up/down"，
            # 两种情况都是把「没抓到」当成了「观测到的真实变化」。
            trend = "unknown"
            trend_pct = None
        elif prev_cmp <= 0:
            # 六小时前的窗口里一条证据都没有 → 这是个新事件。
            trend = "new" if heat > 0 else "unknown"
            trend_pct = None
        else:
            trend_pct = (cur_cmp - prev_cmp) / prev_cmp
            if trend_pct > self.flat_band:
                trend = "up"
            elif trend_pct < -self.flat_band:
                trend = "down"
            else:
                trend = "flat"

        badges: list[str] = []
        recent = sum(
            1
            for r in current.values()
            if (at - r["first_at"]).total_seconds() / 3600.0 <= self.new_within_hours
        )
        # surge: 近期新增参与者占比过半且数量可观
        if recent >= 3 and recent / max(1, len(current)) >= 0.5:
            badges.append("surge")
        if (at - first_at).total_seconds() / 3600.0 < self.new_within_hours:
            badges.append("new")
        if "surge" not in badges and trend == "up" and trend_pct is not None and trend_pct > 0.15:
            badges.append("rising")

        # 来源名：按最新活跃降序，精选方（first_party）优先
        ordered = sorted(
            current.values(),
            key=lambda r: (not r.get("first_party"), -r["last_at"].timestamp()),
        )
        names: list[str] = []
        for rec in ordered:
            name = rec.get("site_name") or rec.get("site_id") or ""
            if name and name not in names:
                names.append(name)

        return HeatResult(
            heat=heat,
            participants=len(current),
            latest_at=latest_at,
            first_at=first_at,
            trend=trend,
            trend_pct=round(trend_pct * 100, 1) if trend_pct is not None else None,
            comparable=comparable,
            behind_sources=sorted(behind) if behind else [],
            uncomparable_participants=uncomparable,
            badges=badges,
            source_names=names[:8],
        )

    def rank(
        self,
        groups: Sequence[Sequence[dict[str, Any]]],
        *,
        at: datetime | None = None,
        behind_sources: set[str] | None = None,
        min_participants: int = MIN_PARTICIPANTS,
    ) -> list[HeatResult]:
        """对多个事件（内容分组）批量计算热度并按热度降序排列。

        Args:
            groups: 每个元素是一个事件的内容列表。
            at: 计算基准时刻。
            behind_sources: 未产出证据的信源 id。
            min_participants: 少于这么多个独立来源的事件不参与排名
                （单来源的内容不是「事件热点」）。
        """
        results: list[HeatResult] = []
        for group in groups:
            if not group:
                continue
            res = self.compute(group, at=at, behind_sources=behind_sources)
            if res.participants < min_participants:
                continue
            results.append(res)
        results.sort(
            key=lambda r: (-r.heat, -(r.latest_at.timestamp() if r.latest_at else 0))
        )
        return results

    def annotate(
        self,
        groups: Sequence[Sequence[dict[str, Any]]],
        *,
        at: datetime | None = None,
        behind_sources: set[str] | None = None,
        min_participants: int = MIN_PARTICIPANTS,
    ) -> list[dict[str, Any]]:
        """批量计算并返回可直接序列化的字典列表（含命中事件的内容）。

        返回项结构::

            {"heat": 12.3, "heat_trend": "up", ..., "items": [...]}
        """
        at = at or datetime.now(timezone.utc)
        out: list[dict[str, Any]] = []
        for group in groups:
            if not group:
                continue
            res = self.compute(group, at=at, behind_sources=behind_sources)
            if res.participants < min_participants:
                continue
            payload = res.to_dict()
            payload["items"] = list(group)
            out.append(payload)
        out.sort(key=lambda d: -float(d.get("heat_raw") or 0.0))
        return out
