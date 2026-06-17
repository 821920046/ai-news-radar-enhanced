"""Critic Agent: quality control for generated reports.

Validates reports against quality criteria (length, vague ratio, required
sections) and applies automated cleanup of redundant or low-quality content.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


class CriticAgent:
    """Validates and cleans generated reports for quality assurance.

    对 EditorAgent 生成的日报进行质量把关：检查长度、模糊表述比例、
    必填板块完整性，并自动清理重复内容和多余空白行。
    """

    # 中英文模糊词汇 / 短语模式
    VAGUE_PATTERNS: list[str] = [
        "可能",
        "也许",
        "大概",
        "不确定",
        "有可能",
        "需要进一步观察",
        "有待验证",
        "尚不清楚",
        "might",
        "maybe",
        "perhaps",
        "unclear",
        "uncertain",
        "有待观察",
        "暂未明确",
        "尚需时日",
        "暂不清楚",
    ]

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.min_report_length = self.config.get("min_report_length", 200)
        self.max_vague_ratio = self.config.get("max_vague_ratio", 0.15)

    # ------------------------------------------------------------------
    # 质量校验
    # ------------------------------------------------------------------

    def validate(self, report: str) -> dict[str, Any]:
        """Validate a report's quality.

        Returns:
            {
                "valid": bool,       # 是否通过校验（issues <= 2）
                "issues": list[str],  # 质量问题列表
                "score": int,         # 0-100 质量评分
                "level": str,         # good / acceptable / poor
            }
        """
        issues: list[str] = []

        if not report or not report.strip():
            return {"valid": False, "issues": ["报告为空"], "score": 0, "level": "poor"}

        # 长度检查
        report_len = len(report)
        if report_len < self.min_report_length:
            issues.append(
                f"报告长度不足 ({report_len} < {self.min_report_length} 字符)"
            )

        # 模糊表述比例
        vague_hits = 0
        for pattern in self.VAGUE_PATTERNS:
            # 使用 count 而非 re.findall 避免重叠问题
            vague_hits += report.count(pattern)

        # 按句子数计算比例
        sentences = [
            s.strip()
            for s in re.split(r"[。！？!?\n]", report)
            if s.strip()
        ]
        if sentences and vague_hits > 0:
            vague_ratio = vague_hits / len(sentences)
            if vague_ratio > self.max_vague_ratio:
                issues.append(
                    f"模糊表述过多 ({vague_ratio:.0%} > {self.max_vague_ratio:.0%})"
                )

        # 必填板块检查
        required_sections = ["关键事件", "明日观察"]
        missing = [s for s in required_sections if s not in report]
        if missing:
            issues.append(f"缺少必要板块: {'、'.join(missing)}")

        # 评分：100 - 扣分项
        score = max(0, 100 - len(issues) * 15)

        # 等级映射
        if score >= 80:
            level = "good"
        elif score >= 50:
            level = "acceptable"
        else:
            level = "poor"

        result = {
            "valid": len(issues) <= 2,
            "issues": issues,
            "score": score,
            "level": level,
        }

        if issues:
            logger.info("[Critic] Validation result: level=%s, issues=%s", level, issues)
        else:
            logger.info("[Critic] Validation passed: level=%s, score=%d", level, score)

        return result

    # ------------------------------------------------------------------
    # 自动清洗
    # ------------------------------------------------------------------

    def clean(self, report: str) -> str:
        """Clean up a report by removing redundant or low-quality content.

        清理操作：
        - 删除完全重复的行
        - 压缩过多的连续空行（最多保留 2 行）
        - 去除首尾空白
        """
        if not report:
            return ""

        lines = report.split("\n")
        seen: set[str] = set()
        cleaned: list[str] = []

        for line in lines:
            stripped = line.strip()
            if stripped and stripped in seen:
                logger.debug("[Critic] Removing duplicate line: %s", stripped[:60])
                continue
            seen.add(stripped)
            cleaned.append(line)

        result = "\n".join(cleaned)

        # 压缩连续空行：最多保留 2 行
        result = re.sub(r"\n{3,}", "\n\n", result)

        return result.strip()

    # ------------------------------------------------------------------
    # 改进循环
    # ------------------------------------------------------------------

    def improve(
        self, report: str, feedback: dict[str, Any] | None = None
    ) -> str:
        """Apply improvements to a report based on validation feedback.

        Args:
            report: 原始报告 Markdown 文本
            feedback: 可选的校验反馈（不传则自动调用 validate）

        Returns:
            改进后的报告文本
        """
        if feedback is None:
            feedback = self.validate(report)

        issues = feedback.get("issues", [])
        if issues:
            for issue in issues:
                logger.info("[Critic] Addressing issue: %s", issue)
        else:
            logger.info("[Critic] No issues to address; applying cleanup only.")

        # 始终执行清洗，不做语义级修改（语义级改进需要 AI，超出纯规则范畴）
        result = self.clean(report)

        # 校验改进效果
        post_feedback = self.validate(result)
        if post_feedback["score"] > feedback.get("score", 0):
            logger.info(
                "[Critic] Improvement raised score from %d to %d",
                feedback.get("score", 0),
                post_feedback["score"],
            )
        elif post_feedback["score"] == feedback.get("score", 0):
            logger.debug("[Critic] Score unchanged after improvement: %d", post_feedback["score"])

        return result
