import { z } from "zod";

export const markEnum = z.enum(["honmei", "taikou", "tanana", "renka"]);

export const horseSchema = z.object({
  mark: markEnum,
  number: z.number().int().positive(), // 馬番
  name: z.string(), // 馬名（カタカナ）
  reading: z.string().optional(), // VOICEVOX用読み仮名
});

export const titleSceneSchema = z.object({
  type: z.literal("title"),
  raceDate: z.string(), // "2026/6/28(日)"
  raceNames: z.array(z.string()), // ["G3函館記念", "G3ラジオNIKKEI賞"]
  catch: z.string().optional(), // "AIが推奨する本命馬は？"
  durationSec: z.number().optional(),
});

export const racePickSceneSchema = z.object({
  type: z.literal("racePick"),
  venue: z.string(), // "函館11R 函館記念"
  horses: z.array(horseSchema).min(1),
  durationSec: z.number().optional(),
});

export const evalPointsSceneSchema = z.object({
  type: z.literal("evalPoints"),
  horseNumber: z.number().int(), // 対象馬番（見出しに使う）
  horseName: z.string(), // "ニシノイストワール"
  points: z
    .array(
      z.object({
        title: z.string(), // "評価ポイント①"
        body: z.string(), // フクロウAI生成の根拠テキスト
      }),
    )
    .min(1)
    .max(4),
  durationSec: z.number().optional(),
});

export const endingSceneSchema = z.object({
  type: z.literal("ending"),
  durationSec: z.number().optional(),
});

export const sceneSchema = z.discriminatedUnion("type", [
  titleSceneSchema,
  racePickSceneSchema,
  evalPointsSceneSchema,
  endingSceneSchema,
]);

export const videoSchema = z.object({
  scenes: z.array(sceneSchema).min(1),
});

export type Horse = z.infer<typeof horseSchema>;
export type TitleScene = z.infer<typeof titleSceneSchema>;
export type RacePickScene = z.infer<typeof racePickSceneSchema>;
export type EvalPointsScene = z.infer<typeof evalPointsSceneSchema>;
export type EndingScene = z.infer<typeof endingSceneSchema>;
export type Scene = z.infer<typeof sceneSchema>;
export type VideoProps = z.infer<typeof videoSchema>;
