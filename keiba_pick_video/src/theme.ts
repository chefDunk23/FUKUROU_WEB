export const theme = {
  colors: {
    assistant: "#ff751f", // 助手（ヒナ）テロップ色
    doctor: "#149120", // 博士テロップ色
    emphRed: "#e43131", // 強調・赤
    emphYellow: "#f8c11a", // 強調・黄
    textMain: "#3a3a3a", // 本文グレー
    stroke: "#ffffff", // 袋文字の白フチ
    raceName: "#6a3fa0", // レース名見出し（紫色）
    // 帯・背景は焼き込み済み背景画像を使用（bg_*.png）
  },

  // 印（本命/対抗/単穴/連下）の記号と色
  marks: {
    honmei: { symbol: "◎", label: "本命" },
    taikou: { symbol: "○", label: "対抗" },
    tanana: { symbol: "▲", label: "単穴" },
    renka: { symbol: "△", label: "連下" },
  },

  fonts: {
    display: "Noto Sans JP",
    body: "Noto Sans JP",
  },

  // 袋文字（白フチ）の標準スタイル
  outline: {
    strokeWidth: 6, // px。WebkitTextStroke で表現
    strokeColor: "#ffffff",
  },

  // シーンごとの標準尺（秒）。データ側で個別上書き可能
  durations: {
    title: 4,
    racePick: 8,
    evalPoints: 10,
    ending: 5,
  },

  layout: {
    canvas: { width: 1920, height: 1080 },

    racePick: {
      // 中央白地上のテキスト配置目安（bg_racepick.png 上）
      raceName: { left: 190, top: 300 },
      markList: { left: 230, top: 380, lineHeight: 90 },
    },

    ending: {
      channelIcon: { left: 579, top: 418, width: 585, height: 585 },
    },
  },
} as const;
