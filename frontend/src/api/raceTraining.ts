export interface TrainingSession {
  training_date: string;     // 'YYYY-MM-DD'
  center: string;            // '栗東' | '美浦'
  course: string;            // '坂路' | 'A'–'E'
  record_type: "HC" | "WC"; // HC=坂路 WC=ウッド
  time_total: number | null;
  lap_1: number | null;      // ラスト1F
  lap_2: number | null;
  lap_3: number | null;
  lap_4: number | null;
}

export interface HorseTrainingSummary {
  horse_id: string;
  sessions: TrainingSession[];
}

export interface RaceTrainingResponse {
  race_id: string;
  race_date: string;
  horses: HorseTrainingSummary[];
}

import { apiFetch } from './client'

export async function fetchRaceTraining(
  raceId: string
): Promise<RaceTrainingResponse> {
  const res = await apiFetch(`/api/v2/races/${encodeURIComponent(raceId)}/training`);
  if (!res.ok) throw new Error(`training fetch failed: ${res.status}`);
  return res.json() as Promise<RaceTrainingResponse>;
}

/** 会場表示文字列: "栗東・坂路" / "栗東・CW(Aコース)" / "美浦・坂路" 等 */
export function formatVenue(session: TrainingSession): string {
  const center = session.center;
  if (session.record_type === "HC") return `${center}・坂路`;
  const courseLabel = session.course ? `${session.course}コース` : "ウッド";
  return `${center}・CW(${courseLabel})`;
}

/** 坂路ラップ表示: "14.2 - 13.5 - 12.1 - 11.9" (末尾から順に) */
function fmtLap(v: number | null): string {
  return v !== null ? v.toFixed(1) : "-";
}

export function formatLaps(session: TrainingSession): string {
  if (session.record_type === "HC") {
    return [session.lap_4, session.lap_3, session.lap_2, session.lap_1]
      .map(fmtLap)
      .join(" - ");
  }
  // WC: lap_2/3/4 は null のため末端のみ表示
  return `- - - ${fmtLap(session.lap_1)}`;
}

export function formatTotalTime(v: number | null): string {
  return v !== null ? v.toFixed(1) : "-";
}
