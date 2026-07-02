import readingDictJson from "../data/reading_dict.json";

export interface ReadingDict {
  horses: Record<string, string>;
  venues: Record<string, string>;
  raceNames: Record<string, string>;
  grades: Record<string, string>;
  raceNumbers: Record<string, string>;
  marks: Record<string, string>;
}

export const readingDict = readingDictJson as ReadingDict;

interface VoicevoxUserDictEntry {
  surface: string;
  pronunciation: string;
  accent_type: number;
  word_type: "PROPER_NOUN";
}

function hiraganaToKatakana(text: string): string {
  return text.replace(/[ぁ-ゖ]/g, (char) =>
    String.fromCharCode(char.charCodeAt(0) + 0x60),
  );
}

/**
 * data/reading_dict.json を VOICEVOX の /user_dict 系エンドポイントが
 * 受け付ける形式に変換する。実際の登録リクエスト送信は次フェーズで実装する。
 */
export function toVoicevoxUserDictEntries(dict: ReadingDict): VoicevoxUserDictEntry[] {
  const categories = [dict.horses, dict.venues, dict.raceNames];
  return categories.flatMap((category) =>
    Object.entries(category).map(([surface, pronunciation]) => ({
      surface,
      pronunciation: hiraganaToKatakana(pronunciation),
      accent_type: 0,
      word_type: "PROPER_NOUN" as const,
    })),
  );
}
