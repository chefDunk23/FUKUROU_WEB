import { loadFont } from "@remotion/google-fonts/NotoSansJP";

// NotoSansJP は CJK グリフを ~120 個の unicode-range チャンクに分割配信しており、
// 名前付き "japanese" subset は存在しない（"cyrillic"/"latin"/"latin-ext"/"vietnamese" のみ名前あり）。
// 生成AIが出すテキストの漢字を事前に絞れないため、subsets は指定せず全チャンクを読み込む。
export const { fontFamily, waitUntilDone } = loadFont("normal", {
  weights: ["400", "700", "900"],
  ignoreTooManyRequestsWarning: true,
});
