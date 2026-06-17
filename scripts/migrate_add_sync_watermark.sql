-- sync_watermark: JV-Link 差分取得の基準時刻を dataspec ごとに管理する
-- jvlink.py は完了時に各 dataspec の last_synced_at を記録し、
-- 次回同期時はその値以降のデータだけを取得する（差分のみ保証）。

CREATE TABLE IF NOT EXISTS sync_watermark (
    dataspec      TEXT        NOT NULL,
    last_synced_at TEXT       NOT NULL,   -- "YYYYMMDDHHmmss" (JV-Link 形式)
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (dataspec)
);

COMMENT ON TABLE  sync_watermark            IS 'JV-Link 差分取得ウォーターマーク（dataspec ごとの最終同期時刻）';
COMMENT ON COLUMN sync_watermark.dataspec   IS 'JV-Link データ種別 (RACE / DIFF / SLOP / WOOD 等)';
COMMENT ON COLUMN sync_watermark.last_synced_at IS 'JVOpen の from_time 引数に渡す文字列 (YYYYMMDDHHmmss)';
