/**
 * useFontLoader.ts
 * =================
 * Remotion の delayRender / continueRender を用いて
 * Noto Sans JP (Black / 900) と Oswald のロードを完全に待機する。
 *
 * @remotion/google-fonts API:
 *   import { loadFont, fontFamily } from "@remotion/google-fonts/NotoSansJP";
 *   const { waitUntilDone } = loadFont();  // モジュール評価時にフェッチ開始
 *   await waitUntilDone();                 // ロード完了まで待機
 */
import { useEffect, useState } from "react";
import { continueRender, delayRender } from "remotion";
import {
  fontFamily as fontFamilyNoto,
  loadFont as loadNotoSansJP,
} from "@remotion/google-fonts/NotoSansJP";
import {
  fontFamily as fontFamilyOswald,
  loadFont as loadOswald,
} from "@remotion/google-fonts/Oswald";

// ── モジュールレベルでロード開始（コンポーネントマウント前から非同期でフェッチ）
const notoLoader = loadNotoSansJP();
const oswaldLoader = loadOswald();

/** CSS fontFamily 文字列として使用する定数。 */
export const FONT_JP = fontFamilyNoto; // "Noto Sans JP"
export const FONT_DATA = fontFamilyOswald; // "Oswald"

/** ロードが完了するまで Remotion のレンダリングを停止するフック。 */
export function useFontLoader(): boolean {
  const [loaded, setLoaded] = useState(false);
  const [handle] = useState(() =>
    delayRender("Fonts: Noto Sans JP (900) / Oswald"),
  );

  useEffect(() => {
    Promise.all([notoLoader.waitUntilDone(), oswaldLoader.waitUntilDone()])
      .then(() => {
        setLoaded(true);
        continueRender(handle);
      })
      .catch(() => {
        // フォントロード失敗でもレンダリングをブロックしない（フォールバックフォントで続行）
        console.warn(
          "[useFontLoader] フォントロード失敗。フォールバックフォントで継続します。",
        );
        setLoaded(true);
        continueRender(handle);
      });
  }, [handle]);

  return loaded;
}
