/**
 * frontend/src/api/client.ts
 * ===========================
 * バックエンド API 呼び出し共通ヘルパー。
 * 認証ヘッダー (X-Api-Key) を自動付与する。
 *
 * API_KEY は frontend/.env.local に VITE_API_KEY=<同じ値> を設定してください。
 * 未設定時は空文字 → バックエンドが開発モード（認証スキップ）のまま動作します。
 */

const API_KEY = import.meta.env.VITE_API_KEY ?? ''

/**
 * fetch の薄いラッパー。バックエンドリクエストに認証ヘッダーを追加する。
 * 使い方は通常の fetch と同じ（URL, init? をそのまま渡す）。
 */
export function apiFetch(url: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers)
  if (API_KEY) {
    headers.set('X-Api-Key', API_KEY)
  }
  return fetch(url, { ...init, headers })
}
