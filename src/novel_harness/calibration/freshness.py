"""摘要新鲜度的**唯一**确定性判据（ADR 0033 Task 7 / §8.2）。

「无法证明与当前快照一致的总结不得进入当前 Writer 背景」。判据收敛为一条读路径：
总结行的 `created_at` 不得早于当前快照行的 `created_at`，且（当磁盘哈希已知时）
当前 DB 哈希必须等于磁盘哈希。无法取得水位时一律 `UNVERIFIED`（fail-closed）。
"""

from __future__ import annotations

from .models import Freshness


def summary_freshness(
    *,
    summary_created_at: str,
    watermark_text_sha256: str | None,
    watermark_snapshot_created_at: str | None,
    disk_sha256: str | None,
) -> Freshness:
    """返回一条章节总结的新鲜度。

    Args:
        summary_created_at: `chapter_summary.created_at`（ISO UTC）。
        watermark_text_sha256: 当前快照（`chapter.text_sha256` 指向的那行）的哈希。
        watermark_snapshot_created_at: 当前快照行的 `created_at`。
        disk_sha256: 磁盘上当前正文的哈希。`None` = 没读磁盘（只做 DB 级判断）。
    """
    if watermark_text_sha256 is None or watermark_snapshot_created_at is None:
        return Freshness.UNVERIFIED
    if disk_sha256 is not None and disk_sha256 != watermark_text_sha256:
        return Freshness.STALE
    if summary_created_at < watermark_snapshot_created_at:
        # 总结行早于当前快照 ⇒ 它不可能是对着当前正文生成的。
        return Freshness.STALE
    return Freshness.CURRENT
