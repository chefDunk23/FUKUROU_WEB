/**
 * frontend/src/utils/router.ts
 * ================================
 * 共有ルーティングユーティリティ。
 * App.tsx の popstate ベースルーターと連携する。
 * 各ビューはこのモジュールをインポートして画面遷移を行う。
 */

export function navigate(path: string): void {
  window.history.pushState({}, '', path)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

/** レース詳細画面へ遷移。URL: /race/:raceId */
export function goToRace(raceId: string): void {
  navigate(`/race/${raceId}`)
}

/** 展開ストーリーページへ遷移。URL: /race-story/:raceId */
export function goToRaceStory(raceId: string): void {
  navigate(`/race-story/${raceId}`)
}

/** レースレベル検証ページへ遷移。URL: /race-level/:raceId[?self_horse_id=:horseId] */
export function goToRaceLevel(raceId: string, selfHorseId?: string): void {
  const params = selfHorseId ? `?self_horse_id=${encodeURIComponent(selfHorseId)}` : ''
  navigate(`/race-level/${raceId}${params}`)
}
