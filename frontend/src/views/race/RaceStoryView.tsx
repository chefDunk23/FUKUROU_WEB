/**
 * frontend/src/views/RaceStoryView.tsx
 * =====================================
 * タブ埋め込み用の再エクスポート専用ファイル。panels/RaceStoryPanel を直接使用。
 *
 * 2026-07: V2アンサンブル引退に伴い、スタンドアロンページ本体
 * （旧: default export RaceStoryView、/race-story/:raceId 用）を削除した。
 * fetchRaceDetail が参照していた GET /api/v2/races/{race_id} が廃止され、
 * 実際には到達不能（ルーター側も /race-story パスを処理していなかった）
 * デッドコードだったため。RaceStoryPanel（タブ埋め込み用、RaceDetailView経由で
 * 使用）自体は削除対象ではないため、再エクスポートのみ維持する。
 */
export type { RaceStoryPanelProps } from '../../panels/RaceStoryPanel'
export { RaceStoryPanel } from '../../panels/RaceStoryPanel'
